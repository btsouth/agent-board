-- Core: state, the outbox, the sync, and the chat commands. No UI here.
--
-- The shape of this addon is dictated by the client sandbox: an addon cannot
-- read a file or open a socket, so the snapshot in Data.lua is fixed for the
-- life of a UI session, and replies leave the same way data arrives, on the
-- client's own write at /reload. Everything below is bookkeeping around those
-- two moments.

local addonName, ns = ...

ns.ADDON = addonName
ns.VERSION = "0.2.2"

local L = ns.L

-- Bindings.xml calls into the addon by name, so the namespace has to be
-- reachable as a global. This is the only global the addon creates.
_G[addonName] = ns

BINDING_HEADER_AGENTBOARD = L["Agent Board"]
BINDING_NAME_AGENTBOARD_TOGGLE = L["Toggle the agent board"]
BINDING_NAME_AGENTBOARD_SYNC = L["Sync agents (reloads the UI)"]
BINDING_NAME_AGENTBOARD_REPLY = L["Reply to the selected agent"]
BINDING_NAME_AGENTBOARD_PAGE_UP = L["Page the agent board up"]
BINDING_NAME_AGENTBOARD_PAGE_DOWN = L["Page the agent board down"]

-- The vocabulary the bridge speaks, in display order. Everything else in this
-- file and in UI.lua is derived from these three tables, so a new status is one
-- entry here plus its name in wowmode/roster.py.
ns.STATUS_ORDER = { "needs", "error", "working", "waiting", "reply", "idle", "finished" }

ns.STATUS_COLORS = {
  needs = { 1.00, 0.66, 0.26 },
  error = { 0.82, 0.36, 0.30 },
  working = { 0.36, 0.63, 0.87 },
  -- Waiting on the agent: nothing for the player to do yet, so it reads as a
  -- soft violet rather than a colour that competes with "needs you".
  waiting = { 0.63, 0.55, 0.80 },
  reply = { 0.54, 0.72, 0.44 },
  idle = { 0.60, 0.57, 0.50 },
  finished = { 0.45, 0.43, 0.38 },
}

ns.STATUS_LABELS = {
  needs = L["Needs you"],
  error = L["Error"],
  working = L["Working"],
  waiting = L["Waiting"],
  reply = L["New reply"],
  idle = L["Idle"],
  finished = L["Finished"],
}

--- Tabs, and the bucket each one shows. "All" is not a bucket, it is everything.
ns.TABS = {
  { key = "all", label = L["All"], filter = nil },
  { key = "needs", label = L["Needs you"], filter = "needs" },
  { key = "working", label = L["Working"], filter = "working" },
  { key = "waiting", label = L["Waiting"], filter = "waiting" },
  { key = "reply", label = L["Replies"], filter = "reply" },
  { key = "finished", label = L["Finished"], filter = "finished" },
}

-- The board's default position. The badge's default is not a coordinate at all:
-- it anchors to the BOTTOM of the minimap cluster, so it lands under the map
-- whatever size the client's UI scale or another addon has made it. A fixed
-- offset from the screen corner has to guess that size, and guessing 140 put the
-- badge across the map itself. This is the last resort for a client with no
-- Minimap frame, and the board's real default.
ns.DEFAULT_BADGE_POINT = { "TOPRIGHT", -160, -140 }
ns.DEFAULT_PANEL_POINT = { "CENTER", 0, 0 }

ns.DEFAULT_DB = {
  point = nil,
  panelPoint = nil,
  seq = 0,
  uiVersion = 2,
  sendOnSync = false,
  -- Refresh policy. Opportunistic refreshes only happen at moments the player
  -- was already waiting (loading screens), never in combat or instanced content.
  opportunistic = false,
  staleAfter = 300,
  minInterval = 600,
  boardOpen = false,
  tab = "all",
  theme = "dark",
  sound = true,
  toasts = true,
  badge = true,
  minimap = true,
  minimapAngle = 250,
  newProject = 1,
}

-- ------------------------------------------------------------------ utils --

function ns:Print(message)
  DEFAULT_CHAT_FRAME:AddMessage("|cffc8a45cT3|r " .. tostring(message))
end

local function copy(defaults, into)
  for key, value in pairs(defaults) do
    if into[key] == nil then
      if type(value) == "table" then
        local nested = {}
        copy(value, nested)
        into[key] = nested
      else
        into[key] = value
      end
    end
  end
  return into
end

--- One field's worth of a delimited record.
---
--- Separators are STRIPPED, never escaped: the Python side then needs no Lua
--- parser and no un-escaper, and a field can never forge a record boundary. All
--- control characters go too, because the value is written into a Lua string
--- literal by the client and a stray newline would corrupt the file it reads.
local function sanitise(value, replacement)
  local clean = tostring(value or ""):gsub("[%c|;\"\\]", replacement or "/")
  return strtrim and strtrim(clean) or clean
end

--- Does a session belong to a tab? One rule, used by the row filter and by the
--- tab counts, because two copies of it would eventually disagree and the count
--- would stop matching the list it labels.
local function matchesTab(session, tab)
  if not tab.filter then
    return true
  end

  local status = session.status or "idle"
  if tab.filter == "finished" then
    -- "Finished" is the quiet bucket: done, and never started.
    return status == "finished" or status == "idle"
  end
  return status == tab.filter
end

-- ------------------------------------------------------------------ state --

--- The snapshot currently on display: the published one when it parsed, the last
--- good one from SavedVariables when it did not. `self.loaded` carries how it was
--- obtained (stale / missing / incompatible) so the UI can say so.
function ns:RefreshSnapshot()
  self.loaded = self:LoadSnapshot()
  self.snapshot = self.loaded.snapshot
  self.offset = 0
  return self.loaded
end

function ns:Data()
  if not self.snapshot then
    self:RefreshSnapshot()
  end
  return self.snapshot
end

function ns:Sessions()
  return self:Data().sessions or {}
end

function ns:Projects()
  return self:Data().projects or {}
end

--- What the board should say before it says anything about agents.
---
--- "0 need you / all clear" is a claim about work, and there is nothing to claim
--- anything about until a payload arrives: a first run used to look identical to
--- a quiet day, which the design says must never happen.
function ns:Headline()
  if self:IsMissing() then
    return L["no snapshot"]
  end
  if self.loaded and self.loaded.incompatible then
    return L["bridge version mismatch"]
  end
  if self:IsStale() then
    return L["showing the last good snapshot"]
  end
  if self.loaded and self.loaded.snapshot and self.loaded.snapshot.error ~= "" then
    return L["T3 is not answering"]
  end
  local age = self:SnapshotAge()
  if age and age > 900 then return L["snapshot is old"] end
  return nil
end

--- Rows the bridge wrote that this addon could not read.
function ns:RejectedCount()
  return tonumber(self:Data().rejected) or 0
end

--- The bridge could not read the session store: a fact the panel has to show.
function ns:BridgeError()
  return tostring(self:Data().error or "")
end

function ns:Attention()
  return tonumber(self:Data().attention) or 0
end

--- Sessions that started needing you since the last sync, as the bridge worked
--- them out from the timestamp this addon wrote before it reloaded.
function ns:NewIds()
  return self:Data().new or {}
end

function ns:NewCount()
  return #self:NewIds()
end

function ns:IsStale()
  return self.loaded and self.loaded.stale or false
end

function ns:IsMissing()
  return self.loaded and self.loaded.missing or false
end

function ns:StatusColor(status)
  local color = self.STATUS_COLORS[status] or self.STATUS_COLORS.idle
  return color[1], color[2], color[3]
end

function ns:StatusLabel(status)
  return self.STATUS_LABELS[status] or tostring(status or "")
end

-- ----------------------------------------------------------------- filters --

--- Rows the current tab and search box let through, in roster order.
---
--- The search covers the id as well as the visible fields: a notification, or
--- the hand-off clipboard, hands a player a session id, and the id is useless if
--- the board cannot find it.
function ns:Filtered()
  local tab = self:Tab()
  local needle = self.search and string.lower(strtrim(self.search)) or ""
  local out = {}

  for _, session in ipairs(self:Sessions()) do
    if matchesTab(session, tab) then
      if needle == "" then
        out[#out + 1] = session
      else
        local haystack = table.concat({
          session.title or "",
          session.project or "",
          session.profile or "",
          session.host or "",
          session.activity or "",
          session.id or "",
          self:StatusLabel(session.status),
        }, " ")
        if string.find(string.lower(haystack), needle, 1, true) then
          out[#out + 1] = session
        end
      end
    end
  end

  return out
end

function ns:Tab()
  local key = (AgentBoardDB and AgentBoardDB.tab) or "all"
  for _, tab in ipairs(self.TABS) do
    if tab.key == key then
      return tab
    end
  end
  return self.TABS[1]
end

function ns:SetTab(key)
  -- An unknown key would render as All and leave junk in SavedVariables, so it is
  -- refused here rather than silently swallowed by the getter.
  local valid = false
  for _, tab in ipairs(self.TABS) do
    if tab.key == key then
      valid = true
      break
    end
  end
  if not valid then
    return false
  end

  AgentBoardDB.tab = key
  self.offset = 0
  if self.RefreshBoard then
    self:RefreshBoard()
  end
  return true
end

function ns:SetSearch(text)
  self.search = text or ""
  self.offset = 0
  if self.RefreshBoard then
    self:RefreshBoard()
  end
end

--- Count per tab, so a tab can show its own number.
function ns:TabCount(tab)
  if not tab.filter then
    return #self:Sessions()
  end

  -- Counted from the rows the payload actually carried, not from the bridge's
  -- window-wide counts: a tab that says 55 while holding 11 rows is a lie.
  local total = 0
  for _, session in ipairs(self:Sessions()) do
    if matchesTab(session, tab) then
      total = total + 1
    end
  end
  return total
end

--- Host states from the payload header: which remote boxes answered.
function ns:HostStatus()
  return self:Data().hosts or {}
end

function ns:OfflineHosts()
  local offline = {}
  for name, state in pairs(self:HostStatus()) do
    if state ~= "ok" then
      offline[#offline + 1] = name
    end
  end
  table.sort(offline)
  return offline
end

-- ------------------------------------------------------------------ outbox --

--- A queued entry: `seq|kind|provider|host|session|text`, joined by ";;".
---
--- `kind` is explicit rather than inferred from the text. The first version
--- recognised a hand-off by the text being exactly "!focus", which meant a
--- player who typed that sentence got a clipboard instead of a reply. Kinds are
--- a closed set, so the alternative is a control channel that cannot collide
--- with anything a person can type.
ns.OUTBOX_KINDS = {
  reply = true,
  new = true,
  approve = true,
  decline = true,
  answer = true,
  focus = true,
  mark_read = true,
  stop = true,
}

--- Replies live in one delimited SavedVariables string. Separators are stripped
--- out of user text instead of escaped, so the Python side needs no Lua parser.
function ns:OutboxEntries()
  if type(AgentBoardOutbox) ~= "string" or AgentBoardOutbox == "" then
    return {}
  end

  local entries = {}
  -- Fields never contain ';' (they are stripped on the way in), so a plain
  -- scan is an unambiguous split on the ";;" separator.
  for chunk in string.gmatch(AgentBoardOutbox, "([^;]+)") do
    local trimmed = strtrim and strtrim(chunk) or chunk
    if trimmed ~= "" then
      table.insert(entries, trimmed)
    end
  end
  return entries
end

function ns:QueueEntry(kind, sessionId, text, host, provider)
  if not self.OUTBOX_KINDS[kind] or not sessionId or sessionId == "" then
    return false
  end

  -- Hand-off, stop and approval decisions do not need prose. Replies and new
  -- sessions do, and accepting an empty one would create a confusing no-op.
  local body = sanitise(text, "/")
  if (kind == "reply" or kind == "new") and body == "" then
    return false
  end

  -- Host travels with the entry so the bridge can route it to the machine the
  -- session actually lives on. "local" is written out rather than left empty:
  -- an empty field is indistinguishable from a half-written file.
  local target = sanitise(host, "")
  if target == "" then
    target = "local"
  end
  local route = sanitise(provider, "")
  if route == "" then
    route = "hermes"
  end

  -- Written as an integer: a float seq ("1e+15") reads back as seq 1, and an ack
  -- of 1 would then delete a reply that was never sent.
  AgentBoardDB.seq = math.floor((tonumber(AgentBoardDB.seq) or 0) + 1)
  local entries = self:OutboxEntries()
  table.insert(entries, table.concat({
    tostring(AgentBoardDB.seq),
    kind,
    route,
    target,
    sanitise(sessionId, ""),
    body,
  }, "|"))

  -- Keep the tail bounded: a dispatcher that never runs must not grow this
  -- forever, and anything this old has either been consumed or been lost.
  while #entries > 25 do
    table.remove(entries, 1)
  end

  AgentBoardOutbox = table.concat(entries, ";;")
  return true
end

function ns:QueueReply(sessionId, text, host, provider)
  return self:QueueEntry("reply", sessionId, text, host, provider)
end

function ns:QueueNew(projectId, text)
  return self:QueueEntry("new", projectId, text, "local", "t3")
end

function ns:QueueApproval(session, decision)
  if not session or not session.approval_request_id or session.approval_request_id == "" then
    return false
  end
  return self:QueueEntry(decision, session.id, session.approval_request_id, session.host, session.provider)
end

function ns:QueueAnswer(session, text)
  if not session or session.user_input_request_id == "" or session.user_input_question_id == "" then
    return false
  end
  return self:QueueEntry(
    "answer",
    session.id,
    session.user_input_request_id .. "~" .. session.user_input_question_id .. "~" .. tostring(text or ""),
    session.host,
    session.provider)
end

--- A focus request is not a reply: the bridge answers it by handing the session
--- to the desktop app rather than by sending words into it.
function ns:QueueFocus(session)
  if not session or not session.id then
    return false
  end
  return self:QueueEntry("focus", session.id, "", session.host, session.provider)
end

function ns:QueueMarkRead(session)
  if not session or not tonumber(session.activity_at) then
    return false
  end
  return self:QueueEntry("mark_read", session.id, session.activity_at, session.host, session.provider)
end

function ns:QueueStop(session)
  if not session or session.status ~= "working" or (session.host or "local") ~= "local" then
    return false
  end
  return self:QueueEntry("stop", session.id, "", session.host, session.provider)
end

-- Hex fields keep host names containing colons out of the hyperlink grammar.
local function linkEncode(value)
  return (tostring(value or ""):gsub(".", function(char)
    return string.format("%02x", string.byte(char))
  end))
end

local function linkDecode(value)
  if #value % 2 ~= 0 or value:find("[^%x]") then return nil end
  return (value:gsub("%x%x", function(pair) return string.char(tonumber(pair, 16)) end))
end

function ns:SessionLink(session)
  return "|Hagentboard:" .. linkEncode(session.provider or "hermes") .. ":" .. linkEncode(session.host or "local") .. ":" .. linkEncode(session.id)
    .. "|h[" .. tostring(session.id):gsub("|", "") .. "]|h"
end

function ns:OpenSessionLink(link)
  local provider, host, id = tostring(link):match("^agentboard:(%x+):(%x+):(%x+)$")
  if not provider then return false end
  provider, host, id = linkDecode(provider), linkDecode(host), linkDecode(id)
  for _, session in ipairs(self:Sessions()) do
    if session.id == id and (session.host or "local") == host and (session.provider or "hermes") == provider then
      self:ShowBoard()
      self:OpenDetail(session, false)
      return true
    end
  end
  self:Print(L["this session is no longer in the snapshot; press Sync."])
  return false
end

hooksecurefunc("SetItemRef", function(link)
  if type(link) == "string" and link:match("^agentboard:") then
    ns:OpenSessionLink(link)
  end
end)

-- Persist transitions rather than the bridge's 'new' list: repeated reloads of
-- one snapshot must never ring twice, nor should an offline host look finished.
function ns:NotifyTransitions()
  if self:IsMissing() or self:IsStale() or self:BridgeError() ~= "" then return {} end
  local prior = type(AgentBoardDB.toastHistory) == "table" and AgentBoardDB.toastHistory or nil
  local cooled = type(AgentBoardDB.toastCooldown) == "table" and AgentBoardDB.toastCooldown or {}
  local current, events = {}, {}
  for _, session in ipairs(self:Sessions()) do
    local key = (session.host or "local") .. "|" .. session.id
    local status = session.status
    if self:IsOffline(session) then
      current[key] = prior and prior[key] or nil
    else
      current[key] = status
      local before = prior and prior[key]
      local completed = (status == "reply" or status == "finished" or status == "idle")
        and (before == "working" or before == "waiting")
      local actionable = status == "needs" or status == "error" or status == "reply" or completed
      local cooldownKey = key .. "|" .. (completed and "finished" or status)
      local last = tonumber(cooled[cooldownKey])
      if prior and actionable and before ~= status and (not last or time() - last >= 600)
          and AgentBoardDB.toasts ~= false then
        if #events < 3 then
          events[#events + 1] = session
          cooled[cooldownKey] = time()
        else
          -- Keep overflow eligible at the next sync instead of losing it forever.
          current[key] = before
        end
      end
    end
  end
  for key, stamp in pairs(cooled) do
    if not tonumber(stamp) or time() - tonumber(stamp) >= 600 then cooled[key] = nil end
  end
  AgentBoardDB.toastHistory, AgentBoardDB.toastCooldown = current, cooled
  self:PlayAttention(#events)
  for index, session in ipairs(events) do
    local target = session
    C_Timer.After((index - 1) * 7, function() ns:ShowToast(target) end)
  end
  return events
end

function ns:OutboxCount()
  return #self:OutboxEntries()
end

--- Drop entries the bridge says it has already settled.
---
--- The addon cannot know what happened to what it sent, so without this the
--- outbox keeps every entry until the tail is trimmed at 25, and the bridge's
--- dedupe log is the only thing stopping a re-send. The payload carries how far
--- the bridge got, which makes the cleanup the addon's own business.
function ns:TrimOutbox(acked)
  acked = tonumber(acked) or 0
  if acked <= 0 or type(AgentBoardOutbox) ~= "string" or AgentBoardOutbox == "" then
    return 0
  end

  -- A mark from beyond our own counter belongs to a previous install of this
  -- addon (SavedVariables deleted, or a copied WTF folder). Honouring it would
  -- delete replies the player has just queued and never sent.
  if acked > (tonumber(AgentBoardDB.seq) or 0) then
    return 0
  end

  local kept, dropped = {}, 0
  for _, entry in ipairs(self:OutboxEntries()) do
    -- The WHOLE first field, and a plain integer: reading "1e+15" as "1" would
    -- drop every entry up to 1, including the reply just queued.
    local field = string.match(entry, "^([^|]+)|")
    local seq = tonumber(field)
    if seq and seq == math.floor(seq) and seq <= acked then
      dropped = dropped + 1
    else
      kept[#kept + 1] = entry
    end
  end

  if dropped > 0 then
    AgentBoardOutbox = table.concat(kept, ";;")
  end
  return dropped
end

function ns:PlayAttention(count)
  if not (AgentBoardDB and AgentBoardDB.sound) or (count or 0) <= 0 then
    return false
  end
  -- Guarded: sound kits differ across client families, and a missing one must
  -- never be an error in someone's raid.
  local ok = pcall(function()
    local kit = SOUNDKIT and (SOUNDKIT.RAID_WARNING or SOUNDKIT.IG_MAINMENU_OPEN)
    PlaySound(kit or 8959)
  end)
  return ok
end

-- -------------------------------------------------------------------- sync --

--- A sync is a UI reload: the client writes SavedVariables on the way out and
--- re-reads Data.lua on the way back in, so one gesture moves both directions.
---
--- The timestamp is written BEFORE the reload, which is the only moment that
--- makes it useful: the client flushes SavedVariables during the reload, so the
--- bridge reads "when did this player last sync" from the value set here. The
--- payload's `new` list is computed against it.
function ns:Sync(reason)
  if InCombatLockdown and InCombatLockdown() then
    self:Print(L["not syncing in combat: a UI reload mid-fight is a bad trade."])
    return false
  end

  AgentBoardSync = time()

  local queued = self:OutboxCount()
  if queued == 1 then
    self:Print(L["one queued item goes out with this reload."])
  elseif queued > 1 then
    self:Print(ns.Lf("%d queued items go out with this reload.", queued))
  else
    self:Print(L["syncing (a UI reload)."])
  end

  -- Reopen where the player was: a refresh should not feel like a restart.
  AgentBoardDB.boardOpen = (self.Board and self.Board:IsShown()) and true or false

  ReloadUI()
  return true
end

--- Which moments may spend a UI reload on fresh data. All conservative: the cost
--- is the player's, so the default is "only when I asked, or when the game was
--- already loading in front of me".
function ns:SyncPolicy()
  if not AgentBoardDB then
    return nil
  end
  return {
    opportunistic = AgentBoardDB.opportunistic ~= false,
    staleAfter = tonumber(AgentBoardDB.staleAfter) or 300,
    minInterval = tonumber(AgentBoardDB.minInterval) or 600,
  }
end

--- May an unprompted sync happen right now?
function ns:CanSyncUnprompted()
  local policy = self:SyncPolicy()
  if not policy or not policy.opportunistic then
    return false, "off"
  end
  if InCombatLockdown and InCombatLockdown() then
    return false, "combat"
  end
  -- Instanced content is not worth interrupting for a status refresh.
  if IsInInstance() then
    return false, "instance"
  end

  local age = self:SnapshotAge()
  if age and age < policy.staleAfter then
    return false, "fresh"
  end

  local lastSync = tonumber(AgentBoardSync) or 0
  if time() - lastSync < policy.minInterval then
    return false, "recent"
  end

  return true
end

-- ------------------------------------------------------------------ events --

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("PLAYER_ENTERING_WORLD")
-- Metrics change under the UI when the client is rescaled or the window moves to
-- another monitor; anything that was fitted to the old ones has to be refitted.
events:RegisterEvent("UI_SCALE_CHANGED")
events:RegisterEvent("DISPLAY_SIZE_CHANGED")

local sawFirstWorld = false

events:SetScript("OnEvent", function(_, event, arg1, arg2)
  if event == "ADDON_LOADED" and arg1 == addonName then
    AgentBoardDB = copy(ns.DEFAULT_DB, type(AgentBoardDB) == "table" and AgentBoardDB or {})

    -- Version 2 moved the live experience to the desktop overlay. Older
    -- SavedVariables may carry the previous reload-on-send defaults, so migrate
    -- them once instead of making an overlay user discover a hidden UI reload.
    if (tonumber(AgentBoardDB.uiVersion) or 0) < 2 then
      AgentBoardDB.uiVersion = 2
      AgentBoardDB.sendOnSync = false
      AgentBoardDB.opportunistic = false
    end

    -- The seq counter is the only thing tying a queued entry to the bridge's
    -- high-water mark, so it is seeded from the clock rather than from 1: a
    -- player who deletes their WTF folder must not restart at seq 1 under a
    -- bridge that has already acknowledged seq 57.
    if (tonumber(AgentBoardDB.seq) or 0) <= 0 then
      AgentBoardDB.seq = math.floor(time())
    end
    if type(AgentBoardOutbox) ~= "string" then
      AgentBoardOutbox = ""
    end

    -- Anything the bridge has settled is dropped before the first refresh, and
    -- the client writes the shortened string back at the next reload.
    local published = ns:ParsePayload(AgentBoardData)
    if published and not published.incompatible then
      ns:TrimOutbox(published.acked)
    end
    events:UnregisterEvent("ADDON_LOADED")
    return
  end

  if event == "PLAYER_LOGIN" then
    local loaded = ns:RefreshSnapshot()

    if ns.BuildUI then
      ns:BuildUI()
    end

    if loaded.incompatible then
      ns:Print(ns.Lf(
        "bridge speaks payload v%d, this addon speaks v%d: run agent-board update, then Sync.",
        tonumber(loaded.schema) or 0, ns.PAYLOAD_SCHEMA))
      return
    end

    if loaded.missing then
      ns:Print(L["No snapshot yet. Run agent-board setup, then Sync."])
      return
    end

    local age = ns:SnapshotAge(loaded)
    local new = ns:NewCount()
    ns:Print(
      ns.Lf("%d need you%s. synced %s%s.", ns:Attention(),
        new > 0 and ns.Lf(", %d new since you last looked", new) or "",
        ns:AgeLabel(age),
        loaded.stale and L[" (bridge payload unusable, showing the last good one)"] or "")
    )

    if AgentBoardDB.boardOpen and ns.ShowBoard then
      ns:ShowBoard()
    end
    return
  end

  if event == "UI_SCALE_CHANGED" or event == "DISPLAY_SIZE_CHANGED" then
    if ns.InvalidateLayout then
      ns:InvalidateLayout()
    end
    return
  end

  if event == "PLAYER_ENTERING_WORLD" then
    -- arg1/arg2 are isInitialLogin and isReloadingUi: the login and reload cases
    -- are this addon's own moments, and a zone change is the only other one.
    if arg1 or arg2 then
      return
    end
    -- Insurance for a client that sends neither flag on the first world entry:
    -- the first one is never spent on a sync, whatever the arguments say.
    if not sawFirstWorld then
      sawFirstWorld = true
      return
    end

    -- Opportunistic refresh: the player just sat through a loading screen, so a
    -- UI reload here is the cheapest moment in the game to spend one.
    if ns:CanSyncUnprompted() then
      ns:Sync("zone")
    end
  end
end)

ns.events = events

-- ------------------------------------------------------------------ slash --

function ns:Summary()
  local counts = self:Data().counts or {}
  local parts = {}
  for _, status in ipairs(self.STATUS_ORDER) do
    local count = tonumber(counts[status]) or 0
    if count > 0 then
      -- Not lowercased: a translated status label is a noun in some languages,
      -- and lowercasing it is how a translator's work gets mangled.
      table.insert(parts, count .. " " .. self:StatusLabel(status))
    end
  end
  return table.concat(parts, ", ")
end

--- Find a session in the snapshot by id.
function ns:SessionById(sessionId)
  for _, session in ipairs(self:Sessions()) do
    if session.id == sessionId then
      return session
    end
  end
  return nil
end

local function toggleFlag(key, label)
  AgentBoardDB[key] = not (AgentBoardDB[key] ~= false)
  ns:Print(label .. ": " .. (AgentBoardDB[key] ~= false and L["on"] or L["off"]))
  if ns.RefreshAll then
    ns:RefreshAll()
  end
end

local function printStatus()
  ns:Print(ns.Lf("synced %s; %s", ns:AgeLabel(ns:SnapshotAge()), ns:Summary() ~= "" and ns:Summary() or L["nothing"]))
  if ns:NewCount() > 0 then
    ns:Print(ns.Lf("%d new since your last sync", ns:NewCount()))
  end
  for _, session in ipairs(ns:Sessions()) do
    ns:Print(ns:SessionLink(session) .. "  " .. (session.label or "") .. "  " .. (session.title or ""))
  end
  return true
end

--- Queue words (or a hand-off) for one session. Returns an explanation when it
--- refuses, so the chat tells the player why nothing happened.
-- Player words for the wire kinds: the button says "Hand off", so the chat
-- line must not say "focus".
local KIND_WORDS = { reply = "a reply", focus = "a live overlay request", approve = "an approval", decline = "a decline" }

local function queueForSession(kind, sessionId, text)
  local session = ns:SessionById(sessionId)
  if not session then
    ns:Print(ns.Lf(
      "no session %s in this snapshot: press Sync for a fresh list, or use /agents status to see the ids.",
      sessionId))
    return
  end

  local queued
  if kind == "focus" then
    queued = ns:QueueFocus(session)
  elseif kind == "approve" or kind == "decline" then
    queued = ns:QueueApproval(session, kind)
  else
    queued = ns:QueueReply(session.id, text, session.host)
  end
  if not queued then
    ns:Print(L["nothing to send."])
    return
  end

  ns:Print(ns.Lf("queued %s for %s.", L[KIND_WORDS[kind] or kind], session.id))
  if AgentBoardDB.sendOnSync then
    ns:Sync(kind)
  else
    ns:Print(L["press Sync (or /agents sync) to send it."])
  end
end

-- Each command is one entry: what it does, what it takes, and the words that
-- stand in for it in the chat. The help text is generated from this table, so a
-- new command cannot be undocumented, and an alias cannot drift from its verb.
-- Forward-declared on purpose: the help entry below reads the table it lives in,
-- and `local COMMANDS = { ... }` does not bring the name into scope until the
-- statement ends, so the closure would capture a nil global instead.
local COMMANDS
COMMANDS = {
  {
    verb = "toggle",
    args = "",
    help = L["Open or close the board"],
    run = function()
      if ns.ToggleBoard then
        ns:ToggleBoard()
      end
    end,
  },
  {
    verb = "settings",
    args = "",
    help = L["Open or close the settings pane"],
    run = function()
      if ns.ToggleSettings then
        ns:ToggleSettings()
      end
    end,
  },
  {
    verb = "sync",
    args = "",
    help = L["Send what is queued and pull a fresh snapshot (a UI reload)"],
    run = function()
      ns:Sync("slash")
    end,
  },
  { verb = "status", aliases = { "who" }, args = "", help = L["What the snapshot holds"], run = printStatus },
  {
    verb = "tab",
    args = "[name]",
    help = L["Switch tab (all, needs, working, waiting, reply, finished)"],
    run = function(rest)
      if not rest or rest == "" then
        local names = {}
        for _, tab in ipairs(ns.TABS) do
          names[#names + 1] = tab.key
        end
        ns:Print(L["tabs: "] .. table.concat(names, ", "))
        return
      end
      -- Verbs are lowercased but an argument is the player's text: the panel
      -- shows "All", so "All" has to work.
      if ns:SetTab(string.lower(rest)) then
        ns:Print(L["tab: "] .. ns:Tab().label)
      else
        ns:Print(ns.Lf("no tab called %s. /agents tab lists them.", rest))
      end
    end,
  },
  {
    verb = "search",
    aliases = { "find" },
    args = "[text]",
    help = L["Filter the rows (empty text clears it)"],
    run = function(rest)
      ns:SetSearch(rest or "")
      ns:Print(rest and rest ~= "" and ns.Lf("searching for %s", rest) or L["search cleared"])
    end,
  },
  {
    verb = "reply",
    args = "<session id> <text>",
    help = L["Queue a reply to one session"],
    run = function(rest)
      local sessionId, text = (rest or ""):match("^(%S+)%s+(.+)$")
      if not sessionId then
        ns:Print(L["usage: /agents reply <session id> <text>"])
        return
      end
      queueForSession("reply", sessionId, text)
    end,
  },
  {
    verb = "handoff",
    aliases = { "focus", "open" },
    args = "<session id>",
    help = L["Open the live overlay for this session"],
    run = function(rest)
      local sessionId = (rest or ""):match("^(%S+)$")
      if not sessionId then
        ns:Print(L["usage: /agents open <session id>"])
        return
      end
      queueForSession("focus", sessionId, "")
    end,
  },
  {
    verb = "new",
    args = "<project id> <text>",
    help = L["Queue a new agent session"],
    run = function(rest)
      local projectId, text = (rest or ""):match("^(%S+)%s+(.+)$")
      if not projectId then
        if ns.ToggleNew then ns.ToggleNew() end
        return
      end
      if not ns:QueueNew(projectId, text) then
        ns:Print(L["write a prompt before starting the agent."])
        return
      end
      ns:Print(ns.Lf("queued a new agent in %s.", projectId))
      if AgentBoardDB.sendOnSync then ns:Sync("new") end
    end,
  },
  {
    verb = "approve",
    args = "<session id>",
    help = L["Approve the selected session's pending request"],
    run = function(rest)
      local sessionId = rest and rest:match("^(%S+)$") or ns.selected and ns.selected.id
      if not sessionId then ns:Print(L["usage: /agents approve <session id>"]) return end
      queueForSession("approve", sessionId, "")
    end,
  },
  {
    verb = "decline",
    args = "<session id>",
    help = L["Decline the selected session's pending request"],
    run = function(rest)
      local sessionId = rest and rest:match("^(%S+)$") or ns.selected and ns.selected.id
      if not sessionId then ns:Print(L["usage: /agents decline <session id>"]) return end
      queueForSession("decline", sessionId, "")
    end,
  },
  {
    verb = "hosts",
    args = "",
    help = L["Which machines answered the last publish"],
    run = function()
      local names = {}
      for name, state in pairs(ns:HostStatus()) do
        names[#names + 1] = name .. ": " .. tostring(state)
      end
      table.sort(names)
      ns:Print(#names > 0 and table.concat(names, ", ") or L["no host map in this snapshot"])
    end,
  },
  {
    verb = "policy",
    args = "[stale|interval] [seconds]",
    help = L["How eagerly an unprompted sync may spend a reload"],
    run = function(rest)
      local key, value = (rest or ""):match("^(%a+)%s+(%d+)$")
      if key == "stale" or key == "interval" then
        -- Floors, not just parses: zero would mean "reload the UI at every
        -- loading screen", which is the opposite of what this policy is for.
        local floor = key == "stale" and 30 or 60
        AgentBoardDB[key == "stale" and "staleAfter" or "minInterval"] =
          math.max(floor, tonumber(value) or floor)
      end
      local policy = ns:SyncPolicy()
      ns:Print(ns.Lf(
        "stale after %ds, at most once every %ds, loading-screen refresh %s",
        policy.staleAfter, policy.minInterval, policy.opportunistic and L["on"] or L["off"]))
    end,
  },
  {
    verb = "opportunistic",
    aliases = { "autosync" },
    args = "",
    help = L["Toggle refreshing on loading screens"],
    run = function()
      toggleFlag("opportunistic", L["Refresh on loading screens"])
    end,
  },
  {
    verb = "sendonsync",
    args = "",
    help = L["Toggle sending queued replies as soon as you send one"],
    run = function()
      toggleFlag("sendOnSync", L["send on sync"])
    end,
  },
  { verb = "sound", args = "", help = L["Toggle the attention sound"], run = function() toggleFlag("sound", L["Sound"]) end },
  { verb = "badge", args = "", help = L["Show or hide the status badge"], run = function() toggleFlag("badge", L["badge"]) end },
  {
    verb = "minimap",
    args = "",
    help = L["Toggle the minimap button"],
    run = function()
      -- toggleFlag already refreshes; a second refresh here was dead work.
      toggleFlag("minimap", L["Minimap button"])
    end,
  },
  {
    verb = "theme",
    args = "",
    help = L["Switch between the T3 skin and the plain one"],
    run = function()
      AgentBoardDB.theme = (AgentBoardDB.theme == "dark") and "classic" or "dark"
      if ns.RefreshSkin then
        ns:RefreshSkin()
      end
      ns:Print(L["skin: "] .. tostring(AgentBoardDB.theme))
    end,
  },
  {
    verb = "help",
    aliases = { "?" },
    args = "[command]",
    help = L["This list, or one command in detail"],
    run = function(rest)
      if rest and rest ~= "" then
        local entry = ns:FindCommand(rest)
        if entry then
          ns:Print(ns.Lf("/agents %s %s: %s", entry.verb, entry.args, entry.help))
          if entry.aliases then
            ns:Print(L["also: /agents "] .. table.concat(entry.aliases, ", /agents "))
          end
          return
        end
        ns:Print(ns.Lf("no command called %s", rest))
      end
      ns:Print(L["commands: "])
      for _, entry in ipairs(COMMANDS) do
        ns:Print(ns.Lf("/agents %s %s: %s", entry.verb, entry.args, entry.help))
      end
    end,
  },
}

--- verb (and alias) -> command, built once at load.
local BY_VERB = {}
for _, entry in ipairs(COMMANDS) do
  BY_VERB[entry.verb] = entry
  for _, alias in ipairs(entry.aliases or {}) do
    BY_VERB[alias] = entry
  end
end

function ns:FindCommand(verb)
  return BY_VERB[verb]
end

ns.COMMANDS = COMMANDS
ns.COMMANDS_BY_VERB = BY_VERB

local function handleSlash(input)
  local command = strtrim and strtrim(input or "") or (input or "")
  local verb = string.lower(command:match("^(%S+)") or "")
  local rest = strtrim and strtrim(command:match("^%S+%s+(.*)$") or "") or command:match("^%S+%s+(.*)$")

  if verb == "" then
    verb = "toggle"
  end

  local entry = BY_VERB[verb]
  if not entry then
    ns:Print(ns.Lf("no command called %s. /agents help for the list.", verb))
    return
  end

  entry.run(rest)
end

SLASH_AGENTBOARD1 = "/agents"
SLASH_AGENTBOARD2 = "/ag"
SlashCmdList["AGENTBOARD"] = handleSlash

ns.handleSlash = handleSlash
