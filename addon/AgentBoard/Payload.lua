-- Payload: the published snapshot, parsed defensively.
--
-- The bridge writes data, never code, and this is the whole reason the format is
-- a delimited string instead of a Lua table: a truncated file or a hostile one
-- yields "no data" here, not an error frame and not injected Lua.
--
-- Fallback order matters for honesty. A payload that is missing, malformed or
-- from an incompatible bridge version must NOT render as an empty board (that
-- reads as "your agents are idle"), so the last good snapshot is kept in
-- SavedVariables and labelled as stale instead.

local addonName, ns = ...

local L = ns.L

ns.PAYLOAD_TAG = "HE1"
ns.PAYLOAD_SCHEMA = 5

local function split(text, sep)
  local out, pos = {}, 1

  while true do
    local start, stop = string.find(text, sep, pos, true)
    if not start then
      out[#out + 1] = string.sub(text, pos)
      return out
    end
    out[#out + 1] = string.sub(text, pos, start - 1)
    pos = stop + 1
  end
end

--- Is this a session id, or the debris of a torn record?
---
--- A stray ";" in a header shifts records by one, and a shifted record still has
--- a non-empty first field (";" itself). Accepting it would put a phantom row on
--- the board, so an id has to look like one: the store's ids are
--- `YYYYMMDD_HHMMSS_xxxxxx`, and nothing shorter or stranger is one.
local function looksLikeId(value)
  return type(value) == "string" and #value >= 8 and string.match(value, "^[%w_%-%.]+$") ~= nil
end

local function toNumber(value, fallback)
  local number = tonumber(value)
  if number == nil then
    return fallback
  end
  return number
end

local function clamp(value, low, high)
  if value < low then
    return low
  end
  if value > high then
    return high
  end
  return value
end

--- Parse a serialized payload. nil means unusable, for any reason.
function ns:ParsePayload(raw)
  if type(raw) ~= "string" or raw == "" then
    return nil
  end

  local records = split(raw, ";;")
  local header = split(records[1] or "", "|")
  if header[1] ~= self.PAYLOAD_TAG then
    return nil
  end

  local meta = {
    schema = nil,
    bridge = "",
    generated = 0,
    rows = nil,
    acked = 0,
    error = "",
    notice = "",
    new = {},
    hosts = {},
    projects = {},
    providers = {},
    counts = {},
    attention = 0,
  }
  for index = 2, #header do
    local key, value = string.match(header[index] or "", "^([%w_]+)=(.*)$")
    if key == "schema" then
      meta.schema = toNumber(value, nil)
    elseif key == "bridge" then
      meta.bridge = value or ""
    elseif key == "generated" then
      meta.generated = toNumber(value, 0)
    elseif key == "rows" then
      meta.rows = toNumber(value, nil)
    elseif key == "error" then
      -- The bridge saying it could not read the session store: the difference
      -- between "no agents are running" and "T3 is not answering".
      meta.error = value or ""
    elseif key == "notice" then
      meta.notice = value or ""
    elseif key == "acked" then
      -- How far the bridge has got through our outbox. Anything at or below it
      -- has been dealt with, so the addon can stop carrying it around.
      meta.acked = toNumber(value, 0)
    elseif key == "hosts" then
      for entry in string.gmatch(value or "", "([^;]+)") do
        -- Split on the LAST colon: the state never contains one, and a host name
        -- may. Taking the first colon turned "foundry:2222:ok" into a host called
        -- `foundry` that is down plus a phantom one, which marked every row from
        -- the real host offline and named a machine nobody configured.
        local name, state = string.match(entry, "^(.+):([^:]+)$")
        if name then
          meta.hosts[name] = state
        end
      end
    elseif key == "new" then
      for id in string.gmatch(value or "", "([^;]+)") do
        table.insert(meta.new, id)
      end
    elseif key == "projects" then
      for entry in string.gmatch(value or "", "([^;]+)") do
        local id, title = string.match(entry, "^([^~]+)~?(.*)$")
        if id and id ~= "" then
          table.insert(meta.projects, { id = id, title = (title ~= "" and title) or id })
        end
      end
    elseif key == "providers" then
      for entry in string.gmatch(value or "", "([^;]+)") do
        local name, state = string.match(entry, "^([^:]+):(.+)$")
        if name then
          meta.providers[name] = state
        end
      end
    elseif key == "counts" then
      for entry in string.gmatch(value or "", "([^,]+)") do
        local name, count = string.match(entry, "^([%w_]+):(%d+)$")
        if name then
          meta.counts[name] = tonumber(count) or 0
        end
      end
    elseif key == "attention" then
      meta.attention = toNumber(value, 0)
    end
  end

  if meta.schema ~= self.PAYLOAD_SCHEMA then
    -- A newer bridge than this addon, or an older one: say so rather than
    -- showing half a board.
    return { incompatible = true, schema = meta.schema, bridge = meta.bridge }
  end

  local sessions, counts = {}, {}
  local rejected = 0

  for index = 2, #records do
    local fields = split(records[index], "|")
    local id = fields[1]

    if not looksLikeId(id) then
      -- Not a row: a torn record, or an id this addon does not understand. Counted
      -- and reported, never silently turned into a session.
      rejected = rejected + 1
    elseif true then
      -- An empty field is a broken row, not a status: "" would render a blank
      -- label in the fallback colour, which is a board full of ghost rows.
      local status = fields[2]
      if not status or status == "" then
        status = "idle"
      end
      table.insert(sessions, {
        id = id,
        status = status,
        age = toNumber(fields[3], 0),
        host = (fields[4] ~= nil and fields[4] ~= "") and fields[4] or "local",
        profile = fields[5] or "",
        project = fields[6] or "",
        title = fields[7] or "",
        activity = fields[8] or "",
        messages = toNumber(fields[9], 0),
        cost = toNumber(fields[10], 0),
        offline = fields[11] == "1",
        preview = fields[12] or "",
        activity_at = fields[13] or "",
        approval_request_id = fields[14] or "",
        approval_summary = fields[15] or "",
        user_input_request_id = fields[16] or "",
        user_input_question_id = fields[17] or "",
        user_input_summary = fields[18] or "",
        user_input_options = fields[19] or "",
        provider = (fields[20] ~= nil and fields[20] ~= "") and fields[20] or "hermes",
        provider_label = (fields[21] ~= nil and fields[21] ~= "") and fields[21] or "Hermes",
        capabilities = {},
        label = self.STATUS_LABELS[status] or status,
      })
      for capability in string.gmatch(fields[22] or "", "([^,]+)") do
        sessions[#sessions].capabilities[capability] = true
      end
      counts[status] = (counts[status] or 0) + 1
    end
  end

  -- A torn payload (the bridge died mid-write, or the file was hand-edited) is
  -- refused whole: half a board is worse than a labelled stale one. The count to
  -- compare against is what the bridge said it WROTE, so a row this addon cannot
  -- read is reported rather than taken as proof the whole file is broken.
  -- Fewer records than the bridge wrote means the file is short: torn, truncated,
  -- or a hostile row that ate a separator. More records than it wrote means one
  -- row was split or carries something this addon cannot read, which is counted
  -- in `rejected` and shown, because one odd row must not cost the player the
  -- whole board.
  if meta.rows ~= nil and (#records - 1) < meta.rows then
    return nil
  end

  return {
    sessions = sessions,
    counts = counts,
    attention = meta.attention > 0 and meta.attention or ((counts.needs or 0) + (counts.error or 0)),
    new = meta.new,
    projects = meta.projects,
    providers = meta.providers,
    generated = meta.generated,
    bridge = meta.bridge,
    schema = meta.schema,
    acked = meta.acked,
    rejected = rejected,
    error = meta.error,
    notice = meta.notice,
    hosts = meta.hosts,
  }
end

--- What the UI should render right now, and how old it is.
---
--- Returns a snapshot plus ``stale`` (the published file was unusable, so this
--- came from SavedVariables) and ``missing`` (there is nothing at all yet, which
--- is the first-run case the onboarding card exists for).
function ns:LoadSnapshot()
  local parsed = self:ParsePayload(AgentBoardData)

  if parsed and not parsed.incompatible then
    AgentBoardLastGood = AgentBoardData
    AgentBoardLastGoodAt = time()
    return { snapshot = parsed, stale = false, missing = false }
  end

  if parsed and parsed.incompatible then
    return { snapshot = { sessions = {}, counts = {}, attention = 0, new = {}, projects = {}, providers = {} },
             stale = true, missing = false, incompatible = true,
             bridge = parsed.bridge, schema = parsed.schema }
  end

  local fallback = self:ParsePayload(AgentBoardLastGood)
  if fallback and not fallback.incompatible then
    return { snapshot = fallback, stale = true, missing = false }
  end

  return { snapshot = { sessions = {}, counts = {}, attention = 0, new = {}, projects = {}, providers = {} }, stale = false, missing = true }
end

--- Age of the snapshot in seconds, clamped: a bad clock (offline client, PTR)
--- must not produce a nonsense age.
function ns:SnapshotAge(loaded)
  loaded = loaded or self.loaded

  if loaded and loaded.stale then
    local marked = tonumber(AgentBoardLastGoodAt)
    if marked then
      return clamp(time() - marked, 0, 604800)
    end
    return nil
  end

  local generated = tonumber((self.snapshot or {}).generated) or 0
  if generated <= 0 then
    return nil
  end
  return clamp(time() - generated, 0, 604800)
end

--- Age thresholds live here once. Three copies of this (the bridge CLI, the
--- preview and the addon) is how "last activity now ago" reached a screenshot.
local AGE_STEPS = {
  { limit = 90, unit = nil },
  { limit = 3600, unit = 60, suffix = "m" },
  { limit = 86400, unit = 3600, suffix = "h" },
}

--- Compact age for a row: "now", "14m", "3h", "2d". "?" when unreadable.
function ns:ShortAge(seconds)
  seconds = tonumber(seconds)
  if not seconds then
    return "?"
  end
  for _, step in ipairs(AGE_STEPS) do
    if seconds < step.limit then
      if not step.unit then
        return L["now"]
      end
      return math.floor(seconds / step.unit) .. step.suffix
    end
  end
  return math.floor(seconds / 86400) .. "d"
end

--- The same age inside a sentence: "just now", "14m ago", "never synced".
function ns:AgeLabel(seconds)
  if not seconds then
    return L["never synced"]
  end
  local compact = self:ShortAge(seconds)
  if compact == "?" then
    return L["at an unknown time"]
  end
  if compact == L["now"] then
    return L["just now"]
  end
  return ns.Lf("%s ago", compact)
end
