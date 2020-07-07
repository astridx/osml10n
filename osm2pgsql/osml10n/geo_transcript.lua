local osml10n = {}

local server_url = "http://localhost:8080"

local http = require"socket.http"
local ltn12 = require"ltn12"

function osml10n.geo_transcript(name,bbox)
  local lon,lat,reqbody
  if (bbox == nil) then
    reqbody = "/" .. "/" .. name
  else
    lon = (bbox[1]+bbox[3])/2.0
    lat = (bbox[2]+bbox[4])/2.0
    reqbody = lon .. "/" .. lat .. "/" .. name
  end
  local respbody = {} -- for the response body
  local result, respcode, respheaders, respstatus = http.request {
    method = "POST",
    url = server_url,
    source = ltn12.source.string(reqbody),
    headers = {
      ["content-type"] = "text/plain",
      ["content-length"] = tostring(#reqbody)
    },
    sink = ltn12.sink.table(respbody)
  }
  if (result ~= 1) then
    print("http error: " .. respcode)
    return("transcription error")
  end
  return table.concat(respbody)
end

return osml10n
