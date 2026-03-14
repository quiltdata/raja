-- RAJEE S3 Authorization Library
-- Pure Lua functions for authorization logic (testable without Envoy)

local M = {}

local multipart_actions = {
  ["s3:InitiateMultipartUpload"] = true,
  ["s3:UploadPart"] = true,
  ["s3:CompleteMultipartUpload"] = true,
  ["s3:AbortMultipartUpload"] = true,
}

local function ends_with(value, suffix)
  return string.sub(value, -#suffix) == suffix
end

local function matches_key(granted, requested)
  if ends_with(granted, "/") then
    return string.sub(requested, 1, #granted) == granted
  end
  return granted == requested
end

local function action_matches(granted_action, requested_action)
  if granted_action == requested_action then
    return true
  end
  if requested_action == "s3:HeadObject" and granted_action == "s3:GetObject" then
    return true
  end
  if requested_action == "s3:GetObjectAttributes" and granted_action == "s3:GetObject" then
    return true
  end
  if multipart_actions[requested_action] and granted_action == "s3:PutObject" then
    return true
  end
  return false
end

local function parse_scope(scope)
  if not scope then
    return nil, "scope missing"
  end

  local first = string.find(scope, ":", 1, true)
  if not first then
    return nil, "invalid scope format"
  end
  local second = string.find(scope, ":", first + 1, true)
  if not second then
    return nil, "invalid scope format"
  end

  local resource_type = string.sub(scope, 1, first - 1)
  local resource_id = string.sub(scope, first + 1, second - 1)
  local action = string.sub(scope, second + 1)

  if resource_type == "" or resource_id == "" or action == "" then
    return nil, "invalid scope format"
  end

  local _, action_colons = action:gsub(":", "")
  if action_colons > 1 then
    return nil, "invalid scope format"
  end

  local parsed = {
    resource_type = resource_type,
    resource_id = resource_id,
    action = action,
  }

  if resource_type == "S3Object" then
    local bucket, key = string.match(resource_id, "^([^/]+)/(.+)$")
    parsed.bucket = bucket
    parsed.key = key
  elseif resource_type == "S3Bucket" then
    parsed.bucket = resource_id
  end

  return parsed
end

local function matches_prefix(granted_scope, requested_scope)
  local granted, granted_err = parse_scope(granted_scope)
  if not granted then
    return false, granted_err
  end

  local requested, requested_err = parse_scope(requested_scope)
  if not requested then
    return false, requested_err
  end

  if granted.resource_type ~= requested.resource_type then
    return false, "resource type mismatch"
  end

  if not action_matches(granted.action, requested.action) then
    return false, "action mismatch"
  end

  if granted.resource_type == "S3Object" then
    if not granted.bucket or not granted.key or not requested.bucket or not requested.key then
      return false, "missing bucket or key"
    end
    if granted.bucket ~= requested.bucket then
      return false, "bucket mismatch"
    end
    if not matches_key(granted.key, requested.key) then
      return false, "key mismatch"
    end
    return true, "matched scope: " .. granted_scope
  end

  if granted.resource_type == "S3Bucket" then
    if granted.resource_id ~= requested.resource_id then
      return false, "bucket mismatch"
    end
    return true, "matched scope: " .. granted_scope
  end

  if granted.resource_id == requested.resource_id then
    return true, "matched scope: " .. granted_scope
  end
  return false, "resource mismatch"
end

function M.parse_query_string(query_string)
  if not query_string or query_string == "" then
    return {}
  end

  -- Reject malformed query strings (only ampersands)
  if string.match(query_string, "^&+$") then
    return nil, "malformed query string"
  end

  local params = {}
  local has_valid_param = false

  for pair in string.gmatch(query_string, "[^&]+") do
    -- Reject parameters that start with =
    if string.sub(pair, 1, 1) == "=" then
      return nil, "parameter without key"
    end

    local key, value = string.match(pair, "([^=]+)=?(.*)")

    -- Reject parameters without keys
    if not key or key == "" then
      return nil, "parameter without key"
    end

    has_valid_param = true

    -- Handle duplicate parameters by creating array
    if params[key] then
      if type(params[key]) == "table" then
        table.insert(params[key], value or "")
      else
        params[key] = { params[key], value or "" }
      end
    else
      params[key] = value or ""
    end
  end

  -- Reject conflicting multipart parameters
  if params["uploadId"] and params["uploads"] then
    return nil, "conflicting multipart parameters"
  end

  return params
end

local function get_s3_action(method, key, query_params)
  -- List of known query parameters for S3 API
  local known_params = {
    versionId = true,
    tagging = true,
    uploads = true,
    uploadId = true,
    partNumber = true,
    versions = true,
    ["list-type"] = true,
    prefix = true,
    location = true,
    delimiter = true,
    marker = true,
    ["max-keys"] = true,
    ["encoding-type"] = true,
    attributes = true,
  }

  -- Reject unknown query parameters
  for param in pairs(query_params) do
    if not known_params[param] then
      return nil
    end
  end

  if query_params["versionId"] then
    if query_params["tagging"] and method == "GET" then
      return "s3:GetObjectVersionTagging"
    elseif query_params["tagging"] and method == "PUT" then
      return "s3:PutObjectVersionTagging"
    elseif method == "GET" then
      return "s3:GetObjectVersion"
    elseif method == "DELETE" then
      return "s3:DeleteObjectVersion"
    end
  end

  if query_params["uploads"] then
    return "s3:InitiateMultipartUpload"
  elseif query_params["uploadId"] and query_params["partNumber"] then
    return "s3:UploadPart"
  elseif query_params["uploadId"] and method == "POST" then
    return "s3:CompleteMultipartUpload"
  elseif query_params["uploadId"] and method == "DELETE" then
    return "s3:AbortMultipartUpload"
  end

  if query_params["versions"] then
    return "s3:ListBucketVersions"
  elseif query_params["list-type"] or query_params["prefix"] then
    return "s3:ListBucket"
  elseif query_params["location"] then
    return "s3:GetBucketLocation"
  elseif query_params["attributes"] then
    return "s3:GetObjectAttributes"
  end

  if method == "GET" then
    if key == "" then
      return "s3:ListBucket"
    end
    return "s3:GetObject"
  elseif method == "PUT" then
    if key == "" then
      return nil
    end
    return "s3:PutObject"
  elseif method == "DELETE" then
    if key == "" then
      return nil
    end
    return "s3:DeleteObject"
  elseif method == "HEAD" then
    if key == "" then
      return nil
    end
    return "s3:HeadObject"
  end

  return nil
end

function M.parse_s3_request(method, path, query_params)
  -- Security: reject empty, double-slash, or trailing-slash paths
  if not path or path == "" or path == "/" then
    return nil, "invalid path"
  end
  if string.find(path, "//") then
    return nil, "double slash in path"
  end
  if string.find(path, "/$") and path ~= "/" then
    return nil, "trailing slash in path"
  end

  local clean_path = string.gsub(path, "^/", "")

  -- Security: reject path traversal attempts
  if string.find(clean_path, "%.%.") then
    return nil, "path traversal attempt"
  end

  -- Security: reject null bytes
  if string.find(clean_path, "\0") then
    return nil, "null byte in path"
  end

  local bucket, key = string.match(clean_path, "([^/]+)/(.*)")
  if not bucket then
    bucket = clean_path
    key = ""
  end

  if not bucket or bucket == "" then
    return nil, "missing bucket"
  end

  local action = get_s3_action(string.upper(method or ""), key, query_params or {})
  if not action then
    return nil, "unknown action"
  end

  local resource_type = "S3Object"
  if action == "s3:ListBucket" or action == "s3:ListBucketVersions" or action == "s3:GetBucketLocation" then
    resource_type = "S3Bucket"
  end

  if resource_type == "S3Object" and key == "" then
    return nil, "missing object key"
  end

  local resource_id
  if resource_type == "S3Bucket" then
    resource_id = bucket
  else
    resource_id = bucket .. "/" .. key
  end

  return resource_type .. ":" .. resource_id .. ":" .. action
end

function M.authorize(scopes, requested_scope)
  if not scopes or #scopes == 0 then
    return false, "no scopes in token"
  end

  local last_reason = "no matching scope"
  for _, scope in ipairs(scopes) do
    local allowed, reason = matches_prefix(scope, requested_scope)
    if allowed then
      return true, reason
    end
    -- Preserve validation errors (malformed scopes, type mismatches), not normal matching failures
    if reason and (string.find(reason, "invalid scope") or string.find(reason, "missing bucket") or string.find(reason, "resource type mismatch")) then
      last_reason = reason
    end
  end

  return false, last_reason
end

return M
