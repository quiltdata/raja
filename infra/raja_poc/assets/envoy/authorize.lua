-- RAJEE Envoy Authorization Filter
-- Integrates authorize_lib with Envoy's request handling

package.path = package.path .. ";/usr/local/share/lua/5.1/?.lua"
package.cpath = package.cpath
  .. ";/usr/lib/aarch64-linux-gnu/lua/5.1/?.so"
  .. ";/usr/lib/x86_64-linux-gnu/lua/5.1/?.so"

local auth_lib = require("authorize_lib")
local cjson = require("cjson")

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

local public_grants = split_csv(os.getenv("RAJEE_PUBLIC_GRANTS"))

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
  local request_string = auth_lib.parse_s3_request(method, clean_path, query_params)

  if #public_grants > 0 then
    local public_allowed, public_reason = auth_lib.authorize(public_grants, request_string)
    if public_allowed then
      request_handle:logInfo(
        string.format("ALLOW: %s (reason: %s)", request_string, public_reason)
      )
      request_handle:headers():add("x-raja-decision", "allow")
      request_handle:headers():add("x-raja-reason", public_reason)
      request_handle:headers():add("x-raja-request", request_string)
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

  local grants = jwt_payload.grants or {}
  local allowed, reason = auth_lib.authorize(grants, request_string)

  if allowed then
    request_handle:logInfo(string.format("ALLOW: %s (reason: %s)", request_string, reason))
    request_handle:headers():add("x-raja-decision", "allow")
    request_handle:headers():add("x-raja-reason", reason)
    request_handle:headers():add("x-raja-request", request_string)
    return
  end

  request_handle:logWarn(string.format("DENY: %s (reason: %s)", request_string, reason))
  request_handle:respond(
    {
      [":status"] = "403",
      ["x-raja-decision"] = "deny",
      ["x-raja-reason"] = reason,
      ["x-raja-request"] = request_string,
    },
    "Forbidden: " .. reason
  )
end
