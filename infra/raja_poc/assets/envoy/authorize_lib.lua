-- RAJEE S3 Authorization Library
-- Pure Lua functions for authorization logic (testable without Envoy)

local M = {}

function M.parse_query_string(query_string)
  if not query_string or query_string == "" then
    return {}
  end

  local params = {}
  for pair in string.gmatch(query_string, "[^&]+") do
    local key, value = string.match(pair, "([^=]+)=?(.*)")
    if key then
      params[key] = value or ""
    end
  end

  return params
end

function M.parse_s3_request(method, path, query_params)
  local clean_path = string.gsub(path or "", "^/", "")
  local bucket, key = string.match(clean_path, "([^/]+)/(.*)")
  if not bucket then
    bucket = clean_path
    key = ""
  end

  local action

  if query_params["uploads"] and not query_params["uploadId"] then
    action = "s3:InitiateMultipartUpload"
  elseif query_params["uploadId"] then
    if method == "POST" then
      action = "s3:CompleteMultipartUpload"
    elseif method == "DELETE" then
      action = "s3:AbortMultipartUpload"
    elseif method == "PUT" then
      action = "s3:UploadPart"
    else
      action = "s3:ListParts"
    end
  elseif method == "GET" and key == "" then
    action = "s3:ListBucket"
  elseif method == "GET" then
    action = "s3:GetObject"
  elseif method == "PUT" then
    action = "s3:PutObject"
  elseif method == "DELETE" then
    action = "s3:DeleteObject"
  elseif method == "HEAD" then
    action = "s3:HeadObject"
  else
    action = "s3:Unknown"
  end

  if action == "s3:ListBucket" then
    return action .. "/" .. bucket .. "/"
  end

  return action .. "/" .. bucket .. "/" .. key
end

function M.authorize(grants, request_string)
  if not grants or #grants == 0 then
    return false, "no grants in token"
  end

  for _, grant in ipairs(grants) do
    if string.find(grant, "*", 1, true) then
      local escaped = string.gsub(grant, "([%%%+%-%*%?%[%]%^%$%(%)%.])", "%%%1")
      local pattern = "^" .. string.gsub(escaped, "%%%*", ".*") .. "$"
      if string.match(request_string, pattern) then
        return true, "matched grant: " .. grant
      end
    else
      if string.sub(request_string, 1, #grant) == grant then
        return true, "matched grant: " .. grant
      end
    end
  end

  return false, "no matching grant"
end

return M
