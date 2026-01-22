-- RAJEE Envoy Authorization Filter
-- Integrates authorize_lib with Envoy's request handling

package.path = package.path .. ";/usr/local/share/lua/5.1/?.lua"
package.cpath = package.cpath
  .. ";/usr/lib/aarch64-linux-gnu/lua/5.1/?.so"
  .. ";/usr/lib/x86_64-linux-gnu/lua/5.1/?.so"

local auth_lib = require("authorize_lib")
local cjson = require("cjson")

local function respond_xml(request_handle, status, code, message)
  local body = string.format(
    "<Error><Code>%s</Code><Message>%s</Message></Error>",
    code,
    message
  )
  request_handle:respond(
    {
      [":status"] = tostring(status),
      ["content-type"] = "application/xml",
    },
    body
  )
end

local function split_csv(value)
  local items = {}
  if not value or value == "" then
    return items
  end
  for item in string.gmatch(value, "([^,]+)") do
    local trimmed = item:gsub("^%s*(.-)%s*$", "%1")
    if trimmed ~= "" then
      table.insert(items, trimmed)
    end
  end
  return items
end

local public_scopes = split_csv(os.getenv("RAJA_PUBLIC_SCOPES") or os.getenv("RAJEE_PUBLIC_GRANTS"))

local function base64url_decode(input)
  local b64chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
  local decoded_input = input:gsub("-", "+"):gsub("_", "/")
  local pad = #decoded_input % 4
  if pad > 0 then
    decoded_input = decoded_input .. string.rep("=", 4 - pad)
  end

  local bits = decoded_input:gsub(".", function(x)
    if x == "=" then
      return ""
    end
    local idx = b64chars:find(x)
    if not idx then
      return ""
    end
    local value = idx - 1
    local out = ""
    for i = 6, 1, -1 do
      if value % 2^i - value % 2^(i - 1) > 0 then
        out = out .. "1"
      else
        out = out .. "0"
      end
    end
    return out
  end)

  return bits:gsub("%d%d%d?%d?%d?%d?%d?%d?", function(x)
    if #x ~= 8 then
      return ""
    end
    local c = 0
    for i = 1, 8 do
      if x:sub(i, i) == "1" then
        c = c + 2^(8 - i)
      end
    end
    return string.char(c)
  end)
end

local function extract_bearer_token(headers)
  local header_value = headers:get("x-raja-authorization") or headers:get("authorization")
  if not header_value then
    return nil
  end
  local token = string.match(header_value, "[Bb]earer%s+(.+)")
  return token or header_value
end

local function decode_jwt_payload(token)
  if not token then
    return nil
  end
  local parts = {}
  for part in string.gmatch(token, "[^.]+") do
    table.insert(parts, part)
  end
  if #parts < 2 then
    return nil
  end
  local payload_json = base64url_decode(parts[2])
  local ok, decoded = pcall(function()
    return cjson.decode(payload_json)
  end)
  if ok then
    return decoded
  end
  return nil
end

function envoy_on_request(request_handle)
  local method = request_handle:headers():get(":method")
  local path = request_handle:headers():get(":path")

  if not method or not path then
    request_handle:logErr("Missing method or path")
    request_handle:respond(
      {[":status"] = "400"},
      "Bad Request: Missing method or path"
    )
    return
  end

  if path == "/health" then
    return
  end

  local path_parts = {}
  for part in string.gmatch(path, "[^?]+") do
    table.insert(path_parts, part)
  end

  local clean_path = path_parts[1] or path
  local query_string = path_parts[2] or ""
  local query_params = auth_lib.parse_query_string(query_string)
  local request_scope, parse_error = auth_lib.parse_s3_request(method, clean_path, query_params)
  if not request_scope then
    request_handle:logWarn("Failed to parse S3 request: " .. tostring(parse_error))
    respond_xml(request_handle, 403, "AccessDenied", tostring(parse_error))
    return
  end

  if #public_scopes > 0 then
    local public_allowed, public_reason = auth_lib.authorize(public_scopes, request_scope)
    if public_allowed then
      request_handle:logInfo(
        string.format("ALLOW: %s (reason: %s)", request_scope, public_reason)
      )
      request_handle:headers():add("x-raja-decision", "allow")
      request_handle:headers():add("x-raja-reason", public_reason)
      request_handle:headers():add("x-raja-request", request_scope)
      return
    end
  end

  local jwt_payload_header = request_handle:headers():get("x-raja-jwt-payload")
  if not jwt_payload_header then
    request_handle:logWarn("Missing JWT payload header")
    request_handle:respond(
      {[":status"] = "401"},
      "Unauthorized: Missing JWT"
    )
    return
  end

  if string.sub(jwt_payload_header, 1, 1) ~= "{" then
    jwt_payload_header = base64url_decode(jwt_payload_header)
  end

  local jwt_payload
  local success, err = pcall(function()
    jwt_payload = cjson.decode(jwt_payload_header)
  end)

  if not success then
    request_handle:logErr("Failed to parse JWT payload: " .. tostring(err))
    request_handle:respond(
      {[":status"] = "401"},
      "Unauthorized: Invalid JWT payload"
    )
    return
  end

  if not jwt_payload.sub or jwt_payload.sub == "" then
    request_handle:logWarn("Missing subject in JWT payload")
    request_handle:respond(
      {[":status"] = "401"},
      "Unauthorized: Missing subject"
    )
    return
  end

  local expected_audience = os.getenv("RAJA_AUDIENCE") or "raja-s3-proxy"
  local token = extract_bearer_token(request_handle:headers())
  local token_payload = decode_jwt_payload(token)
  local aud = token_payload and token_payload.aud or jwt_payload.aud
  local aud_ok = false
  if type(aud) == "string" then
    aud_ok = aud == expected_audience
  elseif type(aud) == "table" then
    for _, value in ipairs(aud) do
      if value == expected_audience then
        aud_ok = true
        break
      end
    end
  end
  if not aud_ok then
    request_handle:logWarn("Invalid audience in JWT payload")
    request_handle:respond(
      {[":status"] = "401"},
      "Unauthorized: Invalid audience"
    )
    return
  end

  local exp = jwt_payload.exp
  if type(exp) == "number" and os.time() >= exp then
    request_handle:logWarn("Expired JWT payload")
    request_handle:respond(
      {[":status"] = "401"},
      "Unauthorized: Token expired"
    )
    return
  end

  local scopes = jwt_payload.scopes or jwt_payload.grants or {}
  if type(scopes) ~= "table" then
    request_handle:logWarn("Invalid scopes type in JWT payload")
    respond_xml(request_handle, 403, "AccessDenied", "invalid scopes type")
    return
  end

  -- Validate that all scopes are non-null strings
  for i, scope in ipairs(scopes) do
    if type(scope) ~= "string" then
      request_handle:logWarn(string.format("Invalid scope at index %d: expected string, got %s", i, type(scope)))
      respond_xml(request_handle, 403, "AccessDenied", "invalid scope in token")
      return
    end
  end

  local allowed, reason = auth_lib.authorize(scopes, request_scope)

  if allowed then
    request_handle:logInfo(string.format("ALLOW: %s (reason: %s)", request_scope, reason))
    request_handle:headers():add("x-raja-decision", "allow")
    request_handle:headers():add("x-raja-reason", reason)
    request_handle:headers():add("x-raja-request", request_scope)
    return
  end

  request_handle:logWarn(string.format("DENY: %s (reason: %s)", request_scope, reason))
  respond_xml(request_handle, 403, "AccessDenied", reason)
end

function envoy_on_response(response_handle)
  -- No response processing needed
end
