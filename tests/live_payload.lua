-- Parses the bridge's currently installed Data.lua without opening the game.
local root = arg[1] or "/mnt/data/Games/World of Warcraft/_classic_beta_/Interface/AddOns/AgentBoard"

function time()
  return os.time()
end

local ns = {}
local function loadAddonFile(name, ...)
  local chunk = assert(loadfile(root .. "/" .. name))
  chunk(...)
end

loadAddonFile("Locale.lua", "AgentBoard", ns)
loadAddonFile("Payload.lua", "AgentBoard", ns)
ns.STATUS_LABELS = {
  needs = "Needs you",
  error = "Error",
  working = "Working",
  waiting = "Waiting",
  reply = "New reply",
  idle = "Idle",
  finished = "Finished",
}
dofile(root .. "/Data.lua")

local parsed = assert(ns:ParsePayload(AgentBoardData), "published payload did not parse")
assert(parsed.schema == ns.PAYLOAD_SCHEMA, "schema mismatch")
assert(#parsed.projects > 0, "no projects parsed")
assert(#parsed.sessions > 0, "no sessions parsed")
for _, session in ipairs(parsed.sessions) do
  assert(type(session.id) == "string" and session.id ~= "", "session id missing")
  assert(type(session.status) == "string" and session.status ~= "", "session status missing")
  assert(type(session.user_input_question_id) == "string", "new input fields missing")
end

print(string.format("LIVE PAYLOAD OK (%d sessions, %d projects)", #parsed.sessions, #parsed.projects))
