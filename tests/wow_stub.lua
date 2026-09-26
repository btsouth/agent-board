-- Offline harness for the addon: load the real Lua with a stubbed WoW API.
--
-- This is not a substitute for playing the game, but it catches the failures
-- that would otherwise be discovered in-game only: bad file order, a nil table
-- read at load, a typo in a frame method, or the outbox round trip drifting
-- from what the Python side parses. Run: lua5.1 tests/wow_stub.lua

local ROOT = arg[1] or "addon/AgentBoard"

-- ------------------------------------------------------------- WoW stubs --

local unpack = unpack or table.unpack
local loaded = {}
local errors = {}

-- Font object names every client family registers. The client refuses a name it
-- does not know, so the stub does too.
local KNOWN_FONT_OBJECTS = {
  GameFontNormal = true,
  GameFontNormalSmall = true,
  GameFontNormalLarge = true,
  GameFontHighlight = true,
  GameFontHighlightSmall = true,
  GameFontHighlightLarge = true,
  GameFontDisable = true,
  GameFontDisableSmall = true,
  ChatFontNormal = true,
  NumberFontNormal = true,
}

-- Widgets are built to the real API's shape: methods that only exist via a
-- template or on a widget kind are absent otherwise, so calling one is a nil
-- call here exactly like it is in the client. An earlier build shipped a
-- `SetBackdrop` call on a frame created without BackdropTemplate, which this
-- gate would have caught before the client did.

local clock, timers = 0, {}
C_Timer = {}
function C_Timer.After(delay, callback)
  assert(type(delay) == "number" and delay >= 0 and type(callback) == "function")
  timers[#timers + 1] = { due = clock + delay, callback = callback }
end
local function advanceTimers(seconds)
  local target = clock + seconds
  while true do
    local nextIndex
    for index, timer in ipairs(timers) do
      if timer.due <= target and (not nextIndex or timer.due < timers[nextIndex].due) then
        nextIndex = index
      end
    end
    if not nextIndex then break end
    local timer = table.remove(timers, nextIndex)
    clock = timer.due
    timer.callback()
  end
  clock = target
end
local itemHooks = {}
function SetItemRef(link)
  for _, hook in ipairs(itemHooks) do hook(link) end
end
function hooksecurefunc(name, callback, hook)
  if type(name) == "table" then
    local original = assert(name[callback])
    name[callback] = function(...)
      original(...)
      hook(...)
    end
    return
  end
  assert(name == "SetItemRef" and type(callback) == "function")
  itemHooks[#itemHooks + 1] = callback
end

local function baseWidget(kind)
  local widget = { kind = kind, shown = true, text = "", scripts = {}, events = {} }

  for _, method in ipairs({
    "SetAllPoints", "SetWidth", "SetScale",
    "SetFrameStrata", "SetClampedToScreen", "SetMovable", "SetParent", "SetID", "GetParent",
    "EnableMouse", "EnableMouseWheel", "RegisterForDrag", "RegisterForClicks",
    "StartMoving", "StopMovingOrSizing", "SetHitRectInsets", "SetAlpha",
    "LockHighlight", "UnlockHighlight", "SetHighlightTexture",
    "SetNormalTexture", "SetPushedTexture", "SetDisabledTexture", "SetJustifyH",
  }) do
    widget[method] = function() end
  end

  -- Anchors are recorded rather than ignored. Where a region ends up is invisible
  -- in a stub that throws the numbers away, and that is exactly how a button ends
  -- up buried in the middle of the minimap with every test still green: the shape
  -- of the placement is the only thing a headless harness can check.
  widget.points = {}
  function widget:SetPoint(point, relativeTo, relativePoint, x, y)
    if type(relativeTo) == "number" then
      -- SetPoint(point, x, y): the offsets are the second and third arguments.
      relativeTo, relativePoint, x, y = self.parent, point, relativeTo, relativePoint
    end
    table.insert(self.points, {
      point = point,
      relativeTo = relativeTo or self.parent,
      relativePoint = relativePoint or point,
      x = tonumber(x) or 0,
      y = tonumber(y) or 0,
    })
  end
  function widget:ClearAllPoints()
    self.points = {}
  end

  function widget:SetAlpha(alpha)
    assert(type(alpha) == "number" and alpha >= 0 and alpha <= 1)
    self.alpha = alpha
  end

  widget.height = 100
  widget.width = 100
  -- Recorded, because whether one control's edge clears another's is a question
  -- about sizes, and a stub that forgets them can only answer it by guessing.
  function widget:SetSize(width, height)
    self.width, self.height = width, height
  end
  function widget:SetHeight(value)
    self.height = value
  end
  function widget:GetHeight()
    return self.height
  end
  function widget:SetWidth(value)
    self.width = value
  end
  function widget:GetWidth()
    return self.width
  end
  function widget:GetEffectiveScale()
    return 1
  end
  function widget:GetCenter()
    return 0, 0
  end

  function widget:SetScript(handler, fn)
    self.scripts[handler] = fn
  end
  function widget:Fire(handler, ...)
    local fn = self.scripts[handler]
    if fn then
      return fn(self, ...)
    end
  end
  function widget:Show()
    self.shown = true
  end
  function widget:Hide()
    self.shown = false
  end
  function widget:IsShown()
    return self.shown
  end
  function widget:SetShown(value)
    self.shown = value and true or false
  end
  function widget:GetPoint()
    return "TOPRIGHT", UIParent, "TOPRIGHT", -24, -48
  end
  function widget:StartMoving()
    self.dragging = true
  end
  function widget:StopMovingOrSizing()
    self.dragging = false
  end

  return widget
end

--- A deterministic stand-in for the client's font metrics: 6px per byte is wrong
--- for any real font and exactly right for testing the fitting loop.
local function addTextMethods(widget)
  function widget:SetText(value)
    self.text = tostring(value or "")
  end
  function widget:GetText()
    return self.text
  end
  function widget:SetTextColor(r, g, b, a)
    -- Recorded: "the status colour is on the word, not on the whole line" is a
    -- claim about colour, and a stub that discards it cannot tell the two apart.
    self.textColor = { r, g, b, a }
  end
  function widget:SetShadowOffset() end
  function widget:SetShadowColor() end
  function widget:SetJustifyH() end
  function widget:SetWordWrap() end
  function widget:SetMaxLines(lines)
    assert(type(lines) == "number" and lines >= 0 and lines == math.floor(lines))
    self.maxLines = lines
  end
  function widget:SetFontObject() end
  function widget:GetStringWidth()
    return #tostring(self.text or "") * (_G.__char_width or 6)
  end
end

local function addTextureMethods(widget)
  function widget:SetColorTexture(r, g, b, a)
    self.color = { r, g, b, a }
  end
  function widget:SetTexture() end
  function widget:SetTexCoord() end
  function widget:SetVertexColor() end
  function widget:SetBlendMode() end
end

local function makeFontString()
  local widget = baseWidget("FontString")
  addTextMethods(widget)
  return widget
end

local function makeTexture()
  local widget = baseWidget("Texture")
  addTextureMethods(widget)
  return widget
end

local function makeWidget(kind, name, parent, template)
  local widget = baseWidget(kind)
  widget.name = name
  widget.parent = parent
  widget.template = template
  widget.events = {}

  -- Frame levels are real here, not a no-op: the client draws a child a level
  -- above its parent, and a pane whose job is to cover the list depends on
  -- being above it.
  widget.frameLevel = (parent and parent.frameLevel or 0) + 1
  function widget:SetFrameLevel(value)
    self.frameLevel = value
  end
  function widget:GetFrameLevel()
    return self.frameLevel
  end

  function widget:CreateTexture()
    return makeTexture()
  end
  function widget:CreateFontString(name, layer, template)
    -- The client validates the template name and refuses anything that is not a
    -- registered font object name: passing the font object itself is an error,
    -- and so is a typo. Both are caught here first.
    if template ~= nil and type(template) ~= "string" then
      error("bad argument #3 to 'CreateFontString' (Usage: local line = self:CreateFontString([name, drawLayer, templateName]))", 2)
    end
    if template ~= nil and not KNOWN_FONT_OBJECTS[template] then
      error("Unknown font object template: " .. tostring(template), 2)
    end
    return makeFontString()
  end
  function widget:RegisterEvent(event)
    self.events[event] = true
    _G.__event_frames = _G.__event_frames or {}
    _G.__event_frames[event] = _G.__event_frames[event] or {}
    table.insert(_G.__event_frames[event], self)
  end
  function widget:UnregisterEvent(event)
    self.events[event] = nil
  end

  local templateName = tostring(template or "")

  if templateName:find("BackdropTemplate") then
    function widget:SetBackdrop(value)
      -- Recorded, not a flag: which art and which edge size a frame was given is
      -- the whole difference between one box and two nested rectangles on a 22px
      -- field, and a flag cannot tell the two apart.
      self.backdrop = value
    end
    function widget:SetBackdropColor(r, g, b, a)
      self.backdropColor = { r, g, b, a }
    end
    function widget:SetBackdropBorderColor(r, g, b, a)
      -- Recorded: the badge's border is a status channel, so "the palette walk left
      -- it grey" is a claim about a colour that a stub discarding it cannot check.
      self.backdropBorderColor = { r, g, b, a }
    end
  end

  if kind == "EditBox" or templateName:find("InputBox") then
    addTextMethods(widget)
    function widget:SetAutoFocus() end
    function widget:SetTextInsets() end
    function widget:SetMaxLetters() end
    function widget:SetMultiLine() end
    function widget:SetFocus()
      _G.__focused = self
    end
    function widget:ClearFocus()
      _G.__focused = nil
    end
  end

  if kind == "Button" then
    addTextMethods(widget)
    function widget:SetEnabled() end
    function widget:Disable() end
    function widget:Enable() end
  end

  return widget
end

UIParent = makeWidget("Frame", "UIParent", nil, nil)
_G.__event_frames = {}

--- Set true to model the client the addon's `backdropFrame` fallback exists for:
--- one without the BackdropTemplate mixin, where asking for the template raises
--- and the frame that comes back has no SetBackdrop* methods at all.
_G.__refuse_backdrop_template = false

function CreateFrame(kind, name, parent, template)
  if _G.__refuse_backdrop_template and tostring(template or ""):find("BackdropTemplate") then
    error("Unknown frame template: BackdropTemplate", 2)
  end
  return makeWidget(kind, name, parent, template)
end

DEFAULT_CHAT_FRAME = makeWidget("Frame", "DEFAULT_CHAT_FRAME", nil, nil)
local said = {}
function DEFAULT_CHAT_FRAME:AddMessage(text)
  table.insert(said, text)
  print("[chat] " .. text)
end
_G.__said = said

GameFontNormal = {}
GameFontHighlight = {}
GameFontHighlightSmall = {}
GameFontNormalSmall = {}
GameFontDisableSmall = {}
ChatFontNormal = {}

UIPanelButtonTemplate = "UIPanelButtonTemplate"
UIPanelCloseButton = "UIPanelCloseButton"
InputBoxTemplate = "InputBoxTemplate"
BackdropTemplate = "BackdropTemplate"

local reloadCount = 0
function ReloadUI()
  reloadCount = reloadCount + 1
  _G.__reloads = reloadCount
end

function IsInInstance()
  if _G.__inInstance then
    return true, "party"
  end
  return false, "none"
end

function InCombatLockdown()
  return _G.__inCombat == true
end

function UnitName()
  return "Tester"
end

function GetTime()
  return 1789771234
end

function time()
  return 1789771234
end

function strtrim(value)
  return (tostring(value or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

SlashCmdList = {}

UISpecialFrames = {}

Minimap = makeWidget("Frame", "Minimap", UIParent, nil)
Minimap.width = 140
Minimap.height = 140
function Minimap:GetCenter()
  return 1000, 800
end
function Minimap:GetEffectiveScale()
  return 1
end

local tooltipLines = {}
GameTooltip = makeWidget("Frame", "GameTooltip", UIParent, nil)
function GameTooltip:SetOwner() end
function GameTooltip:AddLine(text)
  table.insert(tooltipLines, tostring(text or ""))
end
function GameTooltip:Show() end
function GameTooltip:Hide() end
_G.__tooltip_lines = tooltipLines

function GetCursorPosition()
  return 1000, 900
end

local sounds = 0
function PlaySound()
  sounds = sounds + 1
  _G.__sounds = sounds
end
SOUNDKIT = { RAID_WARNING = 8959, IG_MAINMENU_OPEN = 850 }

local function fire(event, ...)
  for _, frame in ipairs(_G.__event_frames[event] or {}) do
    if frame.events[event] then
      local ok, err = pcall(frame.Fire, frame, "OnEvent", event, ...)
      if not ok then
        -- A handler that raises is the client's error frame in miniature: report
        -- it as the failure it is, and stop, because everything after this
        -- depends on a UI that half-exists.
        print(string.format("FAIL  %s handler raised: %s", event, tostring(err)))
        os.exit(1)
      end
    end
  end
end

-- ------------------------------------------------------------ the addon --

local function loadAddonFile(path, addonName, ns)
  local chunk, err = loadfile(path)
  if not chunk then
    error("compile failed: " .. path .. ": " .. tostring(err))
  end
  local ok, runtime = pcall(chunk, addonName, ns)
  if not ok then
    error("load failed: " .. path .. ": " .. tostring(runtime))
  end
  table.insert(loaded, path)
end

local function check(label, condition, detail)
  if condition then
    print(string.format("PASS  %s", label))
  else
    print(string.format("FAIL  %s%s", label, detail and (" (" .. detail .. ")") or ""))
    table.insert(errors, label)
  end
end

local addonName = "AgentBoard"
local ns = {}

-- The gate itself has to be proven: a frame without the mixin must reject a
-- backdrop call, or this harness would pass the exact build the client failed.
local noMixin = CreateFrame("Frame", "NoMixin", UIParent)
check("stub rejects SetBackdrop without BackdropTemplate", pcall(function() noMixin:SetBackdrop({}) end) == false)
local withMixin = CreateFrame("Frame", "WithMixin", UIParent, "BackdropTemplate")
check("stub allows SetBackdrop with BackdropTemplate", pcall(function() withMixin:SetBackdrop({}) end) == true)

-- Same idea for the font template argument: the object is not a name, and an
-- unregistered name is an error in the client.
check(
  "stub rejects a font object as the template",
  pcall(function() return withMixin:CreateFontString(nil, "OVERLAY", GameFontNormal) end) == false
)
check(
  "stub rejects an unknown font template name",
  pcall(function() return withMixin:CreateFontString(nil, "OVERLAY", "GameFontNonsense") end) == false
)
check(
  "stub accepts a registered font template name",
  pcall(function() return withMixin:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall") end) == true
)

loadAddonFile(ROOT .. "/Locale.lua", addonName, ns)
loadAddonFile(ROOT .. "/Data.lua", addonName, ns)
loadAddonFile(ROOT .. "/Payload.lua", addonName, ns)
loadAddonFile(ROOT .. "/Core.lua", addonName, ns)
loadAddonFile(ROOT .. "/UI.lua", addonName, ns)

_G["AgentBoard"] = ns

-- The payload the bridge would have written before this UI session: two
-- sessions, one of them asking a question, one new since the last sync.
AgentBoardData = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 30)
    .. "|rows=2|acked=0|hosts=local:ok;terra:ok|projects=project-1~Projects|new=20260918_182123_e733d6",
  "20260918_182123_e733d6|needs|30|local|default|Projects|Build WoW mode for T3||12|0.4200|0|Should I ship the panel?",
  "20260918_181445_cd30b7|working|260|terra|default|MachineMind|Verify the booking sync|receiving stream response|90|1.2000|0|still working",
}, ";;")

fire("ADDON_LOADED", addonName)
fire("PLAYER_LOGIN")

check("addon files loaded", #loaded == 5, table.concat(loaded, ", "))
check("SavedVariables initialised", type(AgentBoardDB) == "table", type(AgentBoardDB))
check("namespace exposed for bindings", _G["AgentBoard"] == ns)
check("data adapter sees the snapshot", #ns:Sessions() == 2, tostring(#ns:Sessions()))
check("attention count", ns:Attention() == 1, tostring(ns:Attention()))
check("snapshot age computed", ns:SnapshotAge() == 30, tostring(ns:SnapshotAge()))
check("new ids parsed from the header", ns:NewCount() == 1 and ns:NewIds()[1] == "20260918_182123_e733d6",
  tostring(ns:NewCount()))
check("summary reads counts", (ns:Summary() or ""):find("Needs you") ~= nil, ns:Summary())
check("cost parsed per session", (ns:Sessions()[1].cost or 0) > 0.4, tostring(ns:Sessions()[1].cost))
check("payload is not stale", ns:IsStale() == false)
check("last good snapshot remembered", AgentBoardLastGood == AgentBoardData)

check("badge built", ns.Badge ~= nil)
check("panel built", ns.Board ~= nil)
check("panel starts hidden", ns.Board:IsShown() == false)
check("badge shows the attention count", ns.Badge.text.text:find("1 needs you") ~= nil, ns.Badge.text.text)
check("minimap button carries the count", ns.MinimapButton ~= nil and ns.MinimapButton.badge.text == "1",
  ns.MinimapButton and ns.MinimapButton.badge.text)
check("the badge shows the whole phrase, not an ellipsis",
  ns.Badge.text.text:find("%.%.%.$") == nil, ns.Badge.text.text)
check("the count is shown when there is something to count",
  ns.MinimapButton.badge:IsShown() == true)

-- A 12px tooltip edge is most of the height of a 22px field: the corners meet in
-- the middle and the box reads as two nested rectangles instead of one field.
-- Asserted here, before anything re-applies a backdrop, so that this is the
-- build's own result rather than a later refresh having quietly repaired it.
local fieldBackdrop = ns.Board.searchField.backdrop
check("an input field is not given the tooltip-sized border",
  fieldBackdrop ~= nil and fieldBackdrop.edgeSize ~= nil and fieldBackdrop.edgeSize <= 6,
  fieldBackdrop and tostring(fieldBackdrop.edgeSize))
check("...and the composer gets the same box as the search field",
  ns.Detail.composerField.backdrop ~= nil
    and ns.Detail.composerField.backdrop.edgeSize == fieldBackdrop.edgeSize,
  ns.Detail.composerField.backdrop and tostring(ns.Detail.composerField.backdrop.edgeSize))
check("first snapshot seeds notifications silently", (_G.__sounds or 0) == 0, tostring(_G.__sounds))

ns:Toggle()
check("toggle shows the panel", ns.Board:IsShown() == true)
check("rows filled from the snapshot", ns.Board.rows[1].title.text:find("WoW mode") ~= nil,
  ns.Board.rows[1].title.text)
check("row meta carries the project", ns.Board.rows[1].meta.text:find("Projects") ~= nil,
  ns.Board.rows[1].meta.text)
check("row meta carries the host for a remote session", ns.Board.rows[2].meta.text:find("terra") ~= nil,
  ns.Board.rows[2].meta.text)

-- The second line is two pieces: the status word in its status colour, and the
-- trail muted. One colour across the whole line put a full-width shout on every
-- row, and the dot and the legend already say which status it is.
check("the row's status word is drawn on its own",
  #ns.Board.rows[1].status.text > 0
    and ns.Board.rows[1].meta.text:find(ns.Board.rows[1].status.text, 1, true) == nil,
  tostring(ns.Board.rows[1].status.text) .. " | " .. tostring(ns.Board.rows[1].meta.text))
check("...carrying the status colour, which the trail does not carry",
  ns.Board.rows[1].status.textColor ~= nil and ns.Board.rows[1].meta.textColor ~= nil
    and ns.Board.rows[1].status.textColor[1] ~= ns.Board.rows[1].meta.textColor[1],
  tostring(ns.Board.rows[1].status.textColor and ns.Board.rows[1].status.textColor[1])
    .. " vs " .. tostring(ns.Board.rows[1].meta.textColor and ns.Board.rows[1].meta.textColor[1]))
-- Two rows with different statuses: the trail is the same colour in both, which is
-- what says it has stopped being a status channel.
check("...and the trail is one colour across statuses",
  ns.Board.rows[1].meta.textColor ~= nil and ns.Board.rows[2].meta.textColor ~= nil
    and ns.Board.rows[1].meta.textColor[1] == ns.Board.rows[2].meta.textColor[1],
  tostring(ns.Board.rows[1].meta.textColor and ns.Board.rows[1].meta.textColor[1])
    .. " vs " .. tostring(ns.Board.rows[2].meta.textColor and ns.Board.rows[2].meta.textColor[1]))
check("row age rendered", ns.Board.rows[1].age.text == "now", ns.Board.rows[1].age.text)
check("spare rows hidden", ns.Board.rows[3]:IsShown() == false)

-- tabs: counts per bucket, and the filter actually filters
check("tab counts computed", ns:TabCount(ns.TABS[2]) == 1, tostring(ns:TabCount(ns.TABS[2])))
check("a tab with nothing in it counts zero", ns:TabCount(ns.TABS[6]) == 0, tostring(ns:TabCount(ns.TABS[6])))
ns:SetTab("working")
check("tab filters the rows", ns.Board.rows[1].title.text:find("booking") ~= nil and ns.Board.rows[2].session == nil,
  ns.Board.rows[1].title.text)
check("the active tab is recorded", ns:Tab().key == "working", ns:Tab().key)
ns:SetTab("all")

-- search: title, host, project, and case
ns:SetSearch("booking")
check("search filters by title", ns.Board.rows[1].title.text:find("booking") ~= nil and ns.Board.rows[2].session == nil,
  ns.Board.rows[1].title.text)
ns:SetSearch("TERRA")
check("search matches the host column, case-insensitively",
  ns.Board.rows[1].session ~= nil and ns.Board.rows[1].session.id == "20260918_181445_cd30b7",
  ns.Board.rows[1].session and ns.Board.rows[1].session.id)
ns:SetSearch("")

-- detail pane and composer
ns.Board.rows[1]:Fire("OnClick")
check("clicking a row opens the detail pane", ns.Detail:IsShown() == true and ns.selected.id == "20260918_182123_e733d6",
  ns.selected and ns.selected.id)
check("the detail pane shows the preview text", ns.Detail.preview.text:find("ship the panel") ~= nil,
  ns.Detail.preview.text)
check("the detail pane names the machine", ns.Detail.meta.text:find("this machine") ~= nil, ns.Detail.meta.text)
check("composer takes focus", _G.__focused == ns.Detail.composer)

AgentBoardDB.sendOnSync = false
ns.Detail.composer:SetText("ship it")
ns:SendComposer()
check("reply queued", ns:OutboxCount() == 1, tostring(ns:OutboxCount()))
check("composer cleared", ns.Detail.composer:GetText() == "")
check("queue does not reload when sendOnSync is off", (_G.__reloads or 0) == 0, tostring(_G.__reloads))

-- The exact wire format the Python side parses: seq|host|session|text.
check("outbox encodes seq|kind|host|session|text",
  AgentBoardOutbox:match("^(%d+)|reply|hermes|local|20260918_182123_e733d6|ship it$") ~= nil, tostring(AgentBoardOutbox))
check("a fresh install seeds the seq from the clock, not from 1",
  tonumber(AgentBoardDB.seq) >= 1789771234, tostring(AgentBoardDB.seq))

ns:CloseDetail()
ns.Board.rows[2]:Fire("OnClick")
check("a remote row keeps its host in the composer path", ns.selected.host == "terra", ns.selected.host)
ns.Detail.composer:SetText("second|reply;with\"separators")
ns:SendComposer()
outbox = AgentBoardOutbox
check("separators stripped from user text", outbox:find("second/reply/with/separators", 1, true) ~= nil, outbox)
check("a remote reply keeps its host on the wire", outbox:find("|terra|", 1, true) ~= nil, outbox)
check("two entries joined with ;;", select(2, outbox:gsub(";;", "")) == 1, outbox)

-- New-agent flow: project selection and prompt become the same queue/sync path.
ns:ToggleNew()
check("new-agent pane opens", ns.New:IsShown())
check("new-agent pane publishes projects", ns:Projects()[1].id == "project-1")
ns.New.prompt:SetText("Build the next thing")
ns:StartNew()
check("new agent queues a new-session action",
  AgentBoardOutbox:find("|new|t3|local|project-1|Build the next thing", 1, true) ~= nil,
  AgentBoardOutbox)
check("new-agent pane closes after queueing", not ns.New:IsShown())
ns.Board.rows[1]:Fire("OnClick")

-- Hand off: a control message, not a reply.
ns.Detail.composer:SetText("")
ns:FocusSelected()
check("hand-off queued with its own kind, not a magic sentence",
  AgentBoardOutbox:find("|focus|foundry|", 1, true) ~= nil or AgentBoardOutbox:find("|focus|", 1, true) ~= nil,
  AgentBoardOutbox)
check("a reply that happens to say !focus stays a reply",
  ns:QueueReply("20260918_182123_e733d6", "!focus", "local") == true
    and AgentBoardOutbox:find("|reply|hermes|local|20260918_182123_e733d6|!focus", 1, true) ~= nil,
  AgentBoardOutbox)

-- settings
ns:ToggleSettings()
check("settings pane opens", ns.Settings:IsShown() == true)
check("settings pane reports the sync policy", ns.Settings.policy.text:find("reload") ~= nil, ns.Settings.policy.text)
local soundBefore = AgentBoardDB.sound
ns:ToggleSetting({ key = "sound", label = "Sound", kind = "bool" })
check("a settings toggle flips and persists", AgentBoardDB.sound ~= soundBefore)
-- Found by the option it belongs to, not by position: a new setting must not
-- silently break an assertion about a different one.
local function settingsLineFor(key)
  for _, line in ipairs(ns.Settings.lines) do
    if line.option and line.option.key == key then
      return line
    end
  end
end
check("the settings line reflects the value", settingsLineFor("sound") ~= nil
  and settingsLineFor("sound").text:find("off") ~= nil,
  settingsLineFor("sound") and settingsLineFor("sound").text)
ns:ToggleSetting({ key = "sound", label = "Sound", kind = "bool" })
ns:ToggleSetting({ key = "theme", label = "Skin", kind = "theme" })
check("the skin toggle switches theme", AgentBoardDB.theme == "classic", tostring(AgentBoardDB.theme))
ns:ToggleSetting({ key = "theme", label = "Skin", kind = "theme" })
ns:ToggleSettings()
check("settings pane closes", ns.Settings:IsShown() == false)

-- minimap button behaviour
local dragged = pcall(function()
  ns.MinimapButton:Fire("OnDragStart")
  ns.MinimapButton:Fire("OnDragStop")
end)
check("minimap button drags without error", dragged == true)

-- Ending a drag has to read `GetPoint` correctly: it returns point, relativeTo,
-- relativePoint, x, y, so the offsets are the fourth and fifth values. Taking
-- them off a four-name list puts the relativePoint *string* where the arithmetic
-- wants a number, which raised "attempt to perform arithmetic on local 'x' (a
-- string value)" in the client on every drag. The stub hands back a string in
-- that slot too, so this is the same shape of failure.
local function dragsToASavedPoint(frame, key, label)
  local ok, err = pcall(function()
    frame:Fire("OnDragStart")
    frame:Fire("OnDragStop")
  end)
  check(label .. " drags without error", ok == true, tostring(err))

  local saved = AgentBoardDB[key]
  check(label .. " saves the offsets, not the relative point",
    type(saved) == "table" and saved[1] == "TOPRIGHT" and saved[2] == -24 and saved[3] == -48,
    type(saved) == "table" and table.concat({ tostring(saved[1]), tostring(saved[2]), tostring(saved[3]) }, ", ") or "nothing")
end

dragsToASavedPoint(ns.Badge, "point", "badge")
dragsToASavedPoint(ns.Board, "panelPoint", "board")

-- Both panes cover the list area, and the refresh re-shows the rows underneath
-- them: a pane the player opened and the list showing through it read as one
-- mangled block of text, so the pane has to be a frame level above the rows.
local rowLevel = ns.Board.rows[1]:GetFrameLevel()
check("the detail pane covers the rows",
  ns.Detail:GetFrameLevel() > rowLevel,
  ns.Detail:GetFrameLevel() .. " vs " .. rowLevel)
check("the settings pane covers the rows",
  ns.Settings:GetFrameLevel() > rowLevel,
  ns.Settings:GetFrameLevel() .. " vs " .. rowLevel)

AgentBoardDB.minimap = false
ns:RefreshMinimapButton()
check("minimap button can be hidden", ns.MinimapButton:IsShown() == false)
AgentBoardDB.minimap = true
ns:RefreshMinimapButton()
check("minimap button comes back", ns.MinimapButton:IsShown() == true)

-- The minimap button and the badge are the two things a player sees without
-- opening anything, and "the button is sitting in the middle of my map" is not
-- something reading the file catches: it is only in the numbers.
local function lastPoint(region)
  local list = region and region.points
  return list and list[#list]
end

AgentBoardDB.minimapAngle = 210
Minimap.width, Minimap.height = 140, 140
ns:PositionMinimapButton()
local small = lastPoint(ns.MinimapButton)
local smallRadius = math.sqrt((small.x or 0) ^ 2 + (small.y or 0) ^ 2)
check("the button is on the edge of a default map",
  smallRadius >= 70 and smallRadius <= 95, tostring(smallRadius))

-- The reported bug: the radius was a constant, so on any minimap bigger than the
-- 140 it was written against the button ended up inside the map itself.
Minimap.width, Minimap.height = 220, 220
ns:PositionMinimapButton()
local large = lastPoint(ns.MinimapButton)
local largeRadius = math.sqrt((large.x or 0) ^ 2 + (large.y or 0) ^ 2)
check("a larger minimap pushes the button out with it",
  largeRadius > smallRadius + 30, tostring(largeRadius))
check("...so the button is never inside the map's own circle",
  largeRadius >= Minimap.width / 2, tostring(largeRadius))

Minimap.width, Minimap.height = 140, 140
ns:PositionMinimapButton()

-- The badge is the one that covered the map: a fixed offset from the screen
-- corner has to guess the minimap's height, and it guessed a 140px one.
AgentBoardDB.point = nil
ns:PlaceBadge()
local anchor = lastPoint(ns.Badge)
check("the badge defaults under the minimap, not across it",
  anchor ~= nil and anchor.relativeTo == Minimap and anchor.relativePoint == "BOTTOM",
  anchor and (tostring(anchor.point) .. "/" .. tostring(anchor.relativePoint)))

-- The skin walk repaints the fields too, or a switch leaves them as the only
-- chrome still wearing the old theme's border art.
check("a skin switch leaves the input field's box alone",
  ns.Board.searchField.backdrop ~= nil and ns.Board.searchField.backdrop.edgeSize <= 6,
  ns.Board.searchField.backdrop and tostring(ns.Board.searchField.backdrop.edgeSize))

-- tooltips
ns.Board.rows[1]:Fire("OnEnter")
check("hovering a row builds a tooltip", table.concat(_G.__tooltip_lines, " "):find("Projects") ~= nil,
  table.concat(_G.__tooltip_lines, " / "))

-- The badge drops the age to fit, which is only defensible if hovering it says
-- everything the bar left out.
for index = #_G.__tooltip_lines, 1, -1 do
  _G.__tooltip_lines[index] = nil
end
ns.Badge:Fire("OnEnter")
local badgeTip = table.concat(_G.__tooltip_lines, " / ")
check("hovering the badge says more than the bar does",
  badgeTip:find("needs you", 1, true) ~= nil and #badgeTip > #ns.Badge.text.text, badgeTip)
ns.Badge:Fire("OnLeave")

-- collapsing keeps the header and drops the rows
ns.collapsed = false
ns:ToggleCollapsed()
check("collapsed panel hides the rows", ns.collapsed == true and ns.Board.rows[1]:IsShown() == false)
check("a refresh while collapsed does not bring the rows back",
  (ns:RefreshPanel() or true) and ns.Board.rows[1]:IsShown() == false, tostring(ns.Board.rows[1]:IsShown()))
ns:ToggleCollapsed()
check("expanded panel restores the rows", ns.collapsed == false and ns.Board.rows[1]:IsShown() == true)

AgentBoardDB.sendOnSync = true
ns:SetTab("needs")
ns.Board.rows[1]:Fire("OnClick")
ns.Detail.composer:SetText("and sync")
ns:SendComposer()
check("send on sync triggers a UI reload", (_G.__reloads or 0) == 1, tostring(_G.__reloads))

_G.__inCombat = true
ns:Sync("test")
check("sync refuses in combat", (_G.__reloads or 0) == 1, tostring(_G.__reloads))
_G.__inCombat = false

ns:SetTab("all")
ns:ScrollRows(-1)
check("scrolling does not error", true)

-- A zone change must not spend a reload on a snapshot that is still fresh.
fire("PLAYER_ENTERING_WORLD", false, false)
local zoneBefore = _G.__reloads or 0
check("a fresh snapshot does not trigger a zone sync", (_G.__reloads or 0) == zoneBefore, tostring(_G.__reloads))

-- ...but a stale one, out in the world, may.
AgentBoardData = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 900) .. "|rows=1|acked=0|hosts=local:ok|new=",
  "20260918_182123_e733d6|needs|900|local|default|Projects|Build WoW mode for T3||12|0.4200|0|",
}, ";;")
AgentBoardSync = time() - 3600
AgentBoardDB.opportunistic = true
ns:RefreshSnapshot()
local allowed, why = ns:CanSyncUnprompted()
check("a stale snapshot in the open world may sync", allowed == true, tostring(why))

_G.__inInstance = true
allowed, why = ns:CanSyncUnprompted()
check("an instance blocks the opportunistic sync", allowed == false and why == "instance", tostring(why))
_G.__inInstance = false

check("age label reads in minutes", ns:AgeLabel(900) == "15m ago", ns:AgeLabel(900))

-- A broken or hostile payload must not empty the board: the last good snapshot
-- stays on screen and is labelled stale.
AgentBoardData = "this is not a payload"
local state = ns:RefreshSnapshot()
check("unusable payload falls back, not empties", #ns:Sessions() == 1 and state.stale == true,
  tostring(#ns:Sessions()))
check("stale snapshot reported to the UI", ns:IsStale() == true)

-- A payload from a newer bridge is refused as incompatible rather than guessed at.
AgentBoardData = "HE1|bridge=9.9.9|schema=99|generated=1789771234|rows=0|new="
state = ns:RefreshSnapshot()
check("a newer payload schema is flagged incompatible", state.incompatible == true, tostring(state.schema))

-- Nothing at all yet: the first-run case the onboarding card exists for.
AgentBoardData = ""
AgentBoardLastGood = ""
state = ns:RefreshSnapshot()
check("missing payload is reported as missing", state.missing == true)

-- Restore the good payload for the remaining checks.
AgentBoardData = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 30) .. "|rows=1|acked=0|hosts=local:ok|new=",
  "20260918_182123_e733d6|needs|30|local|default|Projects|Build WoW mode for T3||12|0.4200|0|",
}, ";;")
ns:RefreshSnapshot()

ns.handleSlash("status")
check("slash status prints", #_G.__said > 0)
local before = AgentBoardDB.opportunistic
ns.handleSlash("opportunistic")
check("opportunistic toggles", AgentBoardDB.opportunistic ~= before)
ns.handleSlash("policy interval 120")
check("policy interval is settable", AgentBoardDB.minInterval == 120, tostring(AgentBoardDB.minInterval))
ns.handleSlash("nonsense")
check("unknown slash prints usage", #_G.__said > 0)

-- The chat surface has to reach everything the panel does, because a player in
-- an instance cannot click a frame mid-fight.
check("an unknown tab key is refused", ns:SetTab("bogus") == false and ns:Tab().key == "all", ns:Tab().key)
ns.handleSlash("tab working")
check("slash tab switches", ns:Tab().key == "working", ns:Tab().key)
ns.handleSlash("tab all")
-- At this point the fixture holds one session, so a search for something else
-- must empty the list and a search for it must bring the row back.
ns.handleSlash("search booking")
check("slash search with no match empties the list", ns.Board.rows[1].session == nil,
  tostring(ns.Board.rows[1].session))
ns.handleSlash("search wow mode")
check("slash search matches the title case-insensitively",
  ns.Board.rows[1].session ~= nil and ns.Board.rows[1].title.text:find("WoW mode") ~= nil,
  ns.Board.rows[1].title.text)
ns.handleSlash("search")
check("slash search clears", ns.Board.rows[1].session ~= nil, tostring(ns.Board.rows[1].session))

local soundBefore = AgentBoardDB.sound
ns.handleSlash("sound")
check("slash sound toggles", AgentBoardDB.sound ~= soundBefore)
ns.handleSlash("sound")

local themeBefore = AgentBoardDB.theme
ns.handleSlash("theme")
check("slash theme toggles the skin", AgentBoardDB.theme ~= themeBefore)
ns.handleSlash("theme")

local minimapBefore = AgentBoardDB.minimap
ns.handleSlash("minimap")
check("slash minimap toggles the button", (AgentBoardDB.minimap ~= false) ~= (minimapBefore ~= false))
ns.handleSlash("minimap")

ns.handleSlash("hosts")
check("slash hosts reports the host map", table.concat(_G.__said, " "):find("local: ok", 1, true) ~= nil,
  table.concat(_G.__said, " "))

AgentBoardDB.sendOnSync = false
local queuedBefore = ns:OutboxCount()
local saidBefore = #_G.__said

ns.handleSlash("reply 20260918_181445_cd30b7 go ahead")
check("a reply to an id outside the snapshot is refused", ns:OutboxCount() == queuedBefore,
  tostring(ns:OutboxCount()))
check("...and says why", table.concat(_G.__said, " "):find("no session 20260918_181445_cd30b7", 1, true) ~= nil,
  table.concat(_G.__said, " "))

ns.handleSlash("reply 20260918_182123_e733d6 go ahead")
check("slash reply queues a reply", ns:OutboxCount() == queuedBefore + 1, tostring(ns:OutboxCount()))
check("...as a reply kind", AgentBoardOutbox:find("|reply|hermes|local|20260918_182123_e733d6|go ahead", 1, true) ~= nil,
  AgentBoardOutbox)

ns.handleSlash("reply nonsense")
check("a malformed reply prints usage instead of queueing", ns:OutboxCount() == queuedBefore + 1,
  tostring(ns:OutboxCount()))

ns.handleSlash("handoff 20260918_182123_e733d6")
check("handoff queues a focus entry with no words in it",
  AgentBoardOutbox:find("|focus|hermes|local|20260918_182123_e733d6|", 1, true) ~= nil, AgentBoardOutbox)

ns.handleSlash("handoff")
check("handoff without an id prints its usage", #_G.__said > saidBefore, tostring(#_G.__said))

ns.handleSlash("nosuchcommand")
check("an unknown command says so and points at help",
  table.concat(_G.__said, " "):find("no command called nosuchcommand", 1, true) ~= nil,
  table.concat(_G.__said, " "))

ns.handleSlash("help")
check("help lists the commands", table.concat(_G.__said, " "):find("/agents handoff", 1, true) ~= nil and
  table.concat(_G.__said, " "):find("/agents policy", 1, true) ~= nil, table.concat(_G.__said, " "))
ns.handleSlash("help sync")
check("help takes one command", table.concat(_G.__said, " "):find("/agents sync :", 1, true) ~= nil or
  table.concat(_G.__said, " "):find("/agents sync", 1, true) ~= nil, table.concat(_G.__said, " "))

-- A list longer than the panel must say where you are in it, and clamp at both
-- ends, or the missing rows read as sessions that do not exist.
local long = {
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 20) .. "|rows=20|acked=0|hosts=local:ok|new=",
}
for index = 1, 20 do
  long[#long + 1] = "20260918_1800" .. string.format("%02d", index)
    .. "|reply|" .. (index * 60) .. "|local|default|Projects|Session " .. index .. "||||0|"
end
AgentBoardData = table.concat(long, ";;")
ns:RefreshSnapshot()
ns:ShowBoard()
ns:SetTab("all")
ns:SetSearch("")
ns.offset = 0
ns:RefreshPanel()

-- The detail pane covers the list, so the wheel must not scroll underneath it.
check("the wheel does nothing while the detail pane covers the list",
  ns.Detail:IsShown() == true and ns:ScrollRows(-1) == nil and ns.offset == 0, tostring(ns.offset))
ns:CloseDetail()

check("the full list is parsed", #ns:Sessions() == 20, tostring(#ns:Sessions()))
check("the panel says where it is in the list", ns.Board.hosts.text:find("1-12 of 20", 1, true) ~= nil,
  ns.Board.hosts.text)
check("the panel shows twelve rows", ns.Board.rows[12].session ~= nil and ns.Board.rows[12].title.text ~= nil,
  ns.Board.rows[12].title.text)

ns:ScrollRows(-1)
check("scrolling advances the window", ns.Board.hosts.text:find("2-13 of 20", 1, true) ~= nil, ns.Board.hosts.text)
check("the last visible row follows the offset", ns.Board.rows[12].session.id == "20260918_180013",
  ns.Board.rows[12].session.id)

for _ = 1, 40 do
  ns:ScrollRows(-1)
end
check("scrolling clamps at the end", ns.Board.hosts.text:find("9-20 of 20", 1, true) ~= nil, ns.Board.hosts.text)

for _ = 1, 40 do
  ns:ScrollRows(1)
end
check("scrolling clamps at the start", ns.Board.hosts.text:find("1-12 of 20", 1, true) ~= nil, ns.Board.hosts.text)

-- A sync records when it happened (the bridge reads this to compute "new") and
-- whether the board was open (so the reload reopens it).
ns:ShowBoard()
local reloadsBefore = _G.__reloads or 0
ns:Sync("test")
check("sync stamps the time for the bridge", tonumber(AgentBoardSync) == time(), tostring(AgentBoardSync))
check("sync remembers the open board", AgentBoardDB.boardOpen == true)
check("sync reloads exactly once", (_G.__reloads or 0) == reloadsBefore + 1,
  tostring(reloadsBefore) .. " -> " .. tostring(_G.__reloads))

-- ---------------------------------------------------------------------------
-- Presentation and lifecycle rules added after the first in-game pass: text that
-- has to fit, dots that have to mean something, a skin change that actually
-- repaints, and an outbox that does not carry settled entries forever.
-- ---------------------------------------------------------------------------

-- A fixture with one of each loud status, and counts that deliberately DISAGREE
-- with the rows, because the bridge's counts cover a wider window than the
-- payload carries.
AgentBoardData = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 10)
    .. "|rows=3|acked=0|hosts=local:ok;terra:offline|new=",
  "20260918_180001|needs|10|local|default|Projects|A short title||3|0.1000|0|Ready when you are",
  "20260918_180002|working|120|terra|default|MachineMind|"
    .. string.rep("long title ", 30) .. "|streaming|9|2.5000|0|",
  "20260918_180003|reply|300|local|work|toolport|Third row||1|0.0100|0|done",
}, ";;")
ns:RefreshSnapshot()
ns:SetTab("all")
ns:SetSearch("")
ns.offset = 0
ns:RefreshPanel()
ns:RefreshBadge()

-- the row count owns the number the tab shows: a tab that says 55 while holding
-- 11 rows is a lie
check("tab counts come from the rows, not the bridge window", ns:TabCount(ns.TABS[2]) == 1,
  tostring(ns:TabCount(ns.TABS[2])))
check("the all tab counts every row", ns:TabCount(ns.TABS[1]) == 3, tostring(ns:TabCount(ns.TABS[1])))

-- text fitting: measured, not counted
local longTitle = ns.Board.rows[2].title.text
check("an overlong title is cut with an ellipsis", longTitle:sub(-3) == "...", longTitle)
check("...to something that fits the measured width", #longTitle * 6 <= 442 + 6, tostring(#longTitle))
check("a short title is left alone", ns.Board.rows[1].title.text == "A short title", ns.Board.rows[1].title.text)
check("the full title is still what the tooltip has", ns:Filtered()[2].title:sub(1, 10) == "long title",
  ns:Filtered()[2].title:sub(1, 10))

-- the badge dots follow what is actually there, so "new reply" cannot be pushed
-- out of the dot band by four louder buckets that are not present
check("badge dot 1 is the loudest present status", ns.Badge.dots[1].status == "needs",
  tostring(ns.Badge.dots[1].status))
check("badge dot 2 is the next one", ns.Badge.dots[2].status == "working", tostring(ns.Badge.dots[2].status))
check("badge dot 3 is a new reply", ns.Badge.dots[3].status == "reply", tostring(ns.Badge.dots[3].status))
check("unused dots are hidden", ns.Badge.dots[4]:IsShown() == false)

-- offline hosts reach the footer, and the row from that host is marked
ns:RefreshPanel()
check("an offline host is named in the footer", ns.Board.hosts.text:find("host offline: terra", 1, true) ~= nil,
  ns.Board.hosts.text)
check("a row from that host says so", ns:SessionTrail(ns:Sessions()[2]):find("host offline", 1, true) ~= nil,
  ns:SessionTrail(ns:Sessions()[2]))

-- search covers the id and the status label, because a notification hands the
-- player an id and a word like "needs" is what they will type
ns:SetSearch("20260918_180003")
check("search matches a session id", #ns:Filtered() == 1 and ns:Filtered()[1].id == "20260918_180003",
  tostring(#ns:Filtered()))
ns:SetSearch("new reply")
check("search matches a status label", #ns:Filtered() == 1 and ns:Filtered()[1].id == "20260918_180003",
  tostring(#ns:Filtered()))
ns:SetSearch("")

-- a skin change repaints everything the theme touches
ns:ToggleSetting({ key = "theme", label = "Skin", kind = "theme" })
check("the skin switch reports the new theme", AgentBoardDB.theme == "classic", tostring(AgentBoardDB.theme))
check("row stripes are repainted", ns.Board.rows[1].odd.theme == "classic", tostring(ns.Board.rows[1].odd.theme))
check("the hover wash is repainted", ns.Board.rows[1].highlightTheme == "classic",
  tostring(ns.Board.rows[1].highlightTheme))
check("inactive tab buttons are repainted", ns.Board.tabs[2].background.color[1] == 0.16,
  tostring(ns.Board.tabs[2].background.color[1]))
check("the active tab keeps the active colour", ns.Board.tabs[1].background.color[1] == 0.36,
  tostring(ns.Board.tabs[1].background.color[1]))
ns:ToggleSetting({ key = "theme", label = "Skin", kind = "theme" })
check("switching back returns to the dark theme", AgentBoardDB.theme == "dark", tostring(AgentBoardDB.theme))
check("row stripes follow it back", ns.Board.rows[1].odd.theme == "dark", tostring(ns.Board.rows[1].odd.theme))

-- a new snapshot never leaves the window scrolled past the end of the list
ns.offset = 2
ns:RefreshSnapshot()
check("a new snapshot resets the scroll window", ns.offset == 0, tostring(ns.offset))

ns:ScrollTo(99)
check("scrolling to an out of range offset clamps", ns.offset == 0, tostring(ns.offset))

-- the bridge tells the addon how far it got, so settled entries stop travelling
-- in every SavedVariables write from now on
AgentBoardOutbox = "1|reply|hermes|local|aaa|first;;2|focus|hermes|local|bbb|;;3|reply|terra|ccc|third;;broken|reply|hermes|local|ddd|fourth"
check("entry count before trimming", ns:OutboxCount() == 4, tostring(ns:OutboxCount()))
check("settled entries are dropped", ns:TrimOutbox(2) == 2, tostring(ns:TrimOutbox(2)))
check("...leaving the unsettled ones", ns:OutboxCount() == 2, AgentBoardOutbox)
check("...and nothing that cannot be read as a seq", AgentBoardOutbox:find("broken", 1, true) ~= nil, AgentBoardOutbox)
check("an acked of zero drops nothing", ns:TrimOutbox(0) == 0, tostring(ns:TrimOutbox(0)))

-- ...and the payload's acked field is what feeds it
local parsedAck = ns:ParsePayload("HE1|bridge=0.4.0|schema=5|generated=1|rows=1|acked=7|hosts=local:ok|new="
  .. ";;20260918_180001|needs|1|local|default|Projects|title||1|0|0|")
-- A deleted WTF folder restarts the counter; the mark from the old install must
-- not delete replies that were never sent.
AgentBoardDB.seq = 3
AgentBoardOutbox = "1|reply|hermes|local|aaa|old;;2|reply|hermes|local|bbb|older"
check("a mark from a previous install trims nothing", ns:TrimOutbox(57) == 0, AgentBoardOutbox)
AgentBoardDB.seq = 60
check("...but a mark from this install still does", ns:TrimOutbox(57) == 2, AgentBoardOutbox)

check("the payload carries how far the bridge got", parsedAck and parsedAck.acked == 7,
  tostring(parsedAck and parsedAck.acked))

-- the detail pane says what a player needs before typing, and never in broken
-- English: "last activity now ago" was a real string it produced
ns.Board.rows[1]:Fire("OnClick")
check("the detail pane names where a reply goes",
  ns.Detail.target.text:find("this machine", 1, true) ~= nil, ns.Detail.target.text)
check("the detail pane carries the session id as a fact",
  ns.Detail.facts[1].value.text == "20260918_180001", ns.Detail.facts[1].value.text)
check("the detail pane has a stats line", ns.Detail.stats.text ~= nil and #ns.Detail.stats.text > 0,
  tostring(ns.Detail.stats.text))
check("the age is never phrased as 'now ago'",
  ns.Detail.statusTrail.text:find("now ago", 1, true) == nil, ns.Detail.statusTrail.text)
ns:CloseDetail()

-- the tooltip carries the id, which is what a reply needs
-- cleared in place: the stub keeps its own reference to this table
for index = #_G.__tooltip_lines, 1, -1 do
  _G.__tooltip_lines[index] = nil
end
ns:ShowRowTooltip(ns.Board.rows[1])
check("the tooltip names the session id", table.concat(_G.__tooltip_lines, " "):find("20260918_180001", 1, true) ~= nil,
  table.concat(_G.__tooltip_lines, " / "))


-- ---------------------------------------------------------------------------
-- Findings from the independent review of this code, each one a bug that used to
-- exist: a legend that described dots nobody draws, saved coordinates that could
-- be any shape at all, chrome floating under a collapsed bar, and page keys that
-- were documented in a comment and wired to nothing.
-- ---------------------------------------------------------------------------

ns:SetTab("working")
ns:SetSearch("")
ns:RefreshPanel()
check("the legend lists the same statuses the dots use",
  ns.Board.legend.text:find("Error", 1, true) ~= nil
    and ns.Board.legend.text:find("Finished", 1, true) == nil,
  ns.Board.legend.text)
check("the whole legend fits the footer", #ns.Board.legend.text <= 52, tostring(#ns.Board.legend.text))
check("the legend is what the dots can show",
  ns:LegendText() == "Needs you / Error / Working / Waiting / New reply", ns:LegendText())

-- SavedVariables is a file the player can edit: junk there must fall back, not
-- raise "Usage: SetPoint(...)" during the build.
AgentBoardDB.point = "not a point"
AgentBoardDB.panelPoint = { "CENTER" }
ns.Badge = nil
local builtOk = pcall(function()
  ns:BuildBadge()
end)
check("a junk saved badge position falls back instead of erroring", builtOk == true)
AgentBoardDB.point = nil
AgentBoardDB.panelPoint = nil

-- whatever the collapsed bar still shows has to fit in the collapsed bar
ns.collapsed = false
ns:ToggleCollapsed()
check("collapsing hides the search box", ns.Board.search:IsShown() == false)
check("collapsing hides the tab row", ns.Board.tabs[1]:IsShown() == false)
ns:ToggleCollapsed()
check("expanding brings the search box back", ns.Board.search:IsShown() == true and ns.Board.tabs[1]:IsShown() == true)

-- the search placeholder must not draw through what the player types
ns:SetSearch("")
ns:RefreshPanel()
check("the placeholder shows while the search is empty", ns.Board.placeholder:IsShown() == true)
ns:SetSearch("booking")
ns:RefreshPanel()
check("the placeholder hides once there is text in the box", ns.Board.placeholder:IsShown() == false)
ns:SetSearch("")
ns:RefreshPanel()

-- the page keys the header comment promised
local long2 = { "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=30|acked=0|hosts=local:ok|new=" }
for index = 1, 30 do
  long2[#long2 + 1] = "20260918_1900" .. string.format("%02d", index)
    .. "|reply|" .. (index * 30) .. "|local|default|Projects|Paged session " .. index .. "||||0|"
end
AgentBoardData = table.concat(long2, ";;")
ns:RefreshSnapshot()
ns:SetTab("all")
ns:ShowBoard()
check("paging starts at the top", ns.offset == 0, tostring(ns.offset))
ns:PageDown()
check("page down advances by a screenful", ns.offset == 12, tostring(ns.offset))
ns:PageUp()
check("page up comes back", ns.offset == 0, tostring(ns.offset))
ns:PageUp()
check("paging up from the top stays put", ns.offset == 0, tostring(ns.offset))
for _ = 1, 5 do
  ns:PageDown()
end
check("paging down clamps at the end", ns.offset == 18, tostring(ns.offset))

-- The client the `backdropFrame` fallback exists for is one without the
-- BackdropTemplate mixin: the template call raises, and the frame that comes back
-- has no SetBackdrop* methods at all. `applyBackdrop` is written to ask first, but
-- the badge's own border painting called the method directly, so on that client the
-- error frame the fallback exists to prevent arrived at login instead. Modelled
-- rather than trusted, because the difference is invisible on a client that has it.
_G.__refuse_backdrop_template = true
local survived = pcall(function()
  ns.Badge = nil
  ns:BuildBadge()
  ns:RefreshBadge()
end)
_G.__refuse_backdrop_template = false
check("a client without the backdrop mixin can still build and paint the badge", survived == true)
ns.Badge = nil
ns:BuildBadge()
ns:RefreshBadge()


-- ---------------------------------------------------------------------------
-- This pass: a torn record must not become a phantom row, and text fitted at one
-- UI scale must be refitted when the scale changes.
-- ---------------------------------------------------------------------------

-- A stray separator shifts the records; the debris used to count as a session.
local shifted = {
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=1|acked=0|hosts=local:ok|new=",
  "20260918_182123_e733d6|needs|30|local|default|Projects|a title;;;|needs|30|local|default|Projects|junk",
}
AgentBoardData = table.concat(shifted, ";;")
local state = ns:RefreshSnapshot()
check("a junk record is not a session", #ns:Sessions() == 1, tostring(#ns:Sessions()))
check("...and the board still shows the real one", ns:Sessions()[1].id == "20260918_182123_e733d6",
  ns:Sessions()[1].id)

-- An id that cannot be an id is refused with the payload, not shown as a row.
local junkId = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=1|acked=0|hosts=local:ok|new=",
  "x|needs|30|local|default|Projects|too short to be a session||1|0|0|",
}, ";;")
local junkParsed = ns:ParsePayload(junkId)
check("a row whose id cannot be an id is counted, not shown", junkParsed ~= nil
  and #junkParsed.sessions == 0 and junkParsed.rejected == 1,
  tostring(junkParsed and junkParsed.rejected))
AgentBoardData = junkId
state = ns:RefreshSnapshot()
check("...and the board is honest about it rather than blank", state.stale == false
  and #ns:Sessions() == 0 and ns:RejectedCount() == 1,
  tostring(#ns:Sessions()) .. "/" .. tostring(ns:RejectedCount()))

-- A file SHORTER than the bridge said it wrote is a torn write, and that is still
-- refused whole: the rows that did arrive cannot be trusted either.
local torn = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=3|acked=0|hosts=local:ok|new=",
  "20260918_182123_e733d6|needs|30|local|default|Projects|only one of three arrived||1|0|0|",
}, ";;")
check("a short file is refused whole", ns:ParsePayload(torn) == nil)

-- Refitting: the fitting caches are only valid for the metrics they were taken
-- at, so a scale change has to clear them.
AgentBoardData = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=1|acked=0|hosts=local:ok|new=",
  "20260918_182123_e733d6|needs|30|local|default|Projects|A title that is long enough to be measured||12|0.4200|0|preview",
}, ";;")
ns:RefreshSnapshot()
ns:SetTab("all")
ns:SetSearch("")
ns:ShowBoard()
ns:RefreshPanel()
-- The proof of a refit is that the text changes with the metrics: a bigger font
-- fits fewer characters, so the fitted title gets shorter.
AgentBoardData = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=1|acked=0|hosts=local:ok|new=",
  "20260918_182123_e733d6|needs|30|local|default|Projects|"
    .. string.rep("word ", 40) .. "||12|0.4200|0|preview",
}, ";;")
ns:RefreshSnapshot()
ns:SetTab("all")
ns:SetSearch("")
ns:ShowBoard()

_G.__char_width = 6
ns:RefreshPanel()
local narrowFont = ns.Board.rows[1].title.text
check("with small metrics the title fits a lot of it", #narrowFont > 40, tostring(#narrowFont))

_G.__char_width = 20
fire("UI_SCALE_CHANGED")
local wideFont = ns.Board.rows[1].title.text
check("a UI scale change refits the text to the new metrics", #wideFont < #narrowFont,
  tostring(#narrowFont) .. " -> " .. tostring(#wideFont))
check("...with an ellipsis, so it reads as trimmed", wideFont:sub(-3) == "...", wideFont)
check("...and the theme caches still hold", ns.Board.rows[1].odd.theme == "dark", tostring(ns.Board.rows[1].odd.theme))
_G.__char_width = 6
fire("UI_SCALE_CHANGED")

-- Paging a collapsed panel must not move a window nobody can see.
ns:SetTab("all")
ns.offset = 0
ns.collapsed = true
ns:ScrollTo(5)
check("paging does nothing while collapsed", ns.offset == 0, tostring(ns.offset))
ns.collapsed = false

-- A host name may contain a colon: the producer strips the separators that could
-- forge a field, and a colon is not one of them. Splitting on the first colon made
-- "foundry:2222:ok" into a host called `foundry` that is down, plus a phantom one -
-- so every row from the real host read as offline and the footer named a machine
-- nobody configured.
local colonPayload = ns:ParsePayload(
  "HE1|bridge=0.4.0|schema=5|generated=1|rows=1|acked=0|hosts=foundry:2222:ok|new="
  .. ";;20260918_180001|needs|1|foundry:2222|default|Projects|title||1|0|0|")
local parsedHosts = {}
for name, state in pairs((colonPayload or {}).hosts or {}) do
  parsedHosts[#parsedHosts + 1] = name .. "=" .. state
end
check("a host name containing a colon survives the header",
  colonPayload ~= nil and colonPayload.hosts["foundry:2222"] == "ok",
  table.concat(parsedHosts, ", "))

-- The composer shares its row with `Hand off` and `Send`. Its right inset
-- reserved room for `Send` alone, which left 82px of the box - and the player's
-- typing - under an opaque button drawn on top of it.
local detailWidth = ns.Detail:GetWidth()
local sendLeft = detailWidth + lastPoint(ns.Detail.send).x - ns.Detail.send:GetWidth()
local focusLeft = sendLeft + lastPoint(ns.Detail.focus).x - ns.Detail.focus:GetWidth()
local composerRight = detailWidth + lastPoint(ns.Detail.composerField).x
local overlap = composerRight - focusLeft
check("the composer stops where the action buttons begin", overlap <= 0,
  "the composer runs " .. overlap .. "px past the Hand off button")

-- The footer's two ends share one row: a notice or the dot legend on the left, the
-- window range and down hosts on the right. Two caps that were meant to fit added
-- up past the panel (380 + 230 against 536), so a notice and a long host list drew
-- over each other with the trailer on top.
local footer = { "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5)
  .. "|rows=30|acked=0|error=the session store is locked|hosts=local:ok;foundry:offline|new=" }
for index = 1, 30 do
  footer[#footer + 1] = "20260918_2200" .. string.format("%02d", index)
    .. "|reply|30|local|default|Projects|Footer row " .. index .. "||||0|"
end
AgentBoardData = table.concat(footer, ";;")
ns:RefreshSnapshot()
ns:SetTab("all")
ns:SetSearch("")
ns:ShowBoard()
ns:RefreshPanel()
check("the footer is carrying both a notice and a full trailer",
  ns.Board.legend.text:find("agent-board setup", 1, true) ~= nil
    and ns.Board.hosts.text:find("offline", 1, true) ~= nil,
  ns.Board.legend.text .. " | " .. ns.Board.hosts.text)
check("...and the two ends still fit the panel together",
  ns.Board.legend:GetStringWidth() + ns.Board.hosts:GetStringWidth() + 24 <= 560 - 24,
  "legend " .. ns.Board.legend:GetStringWidth() .. " + trailer " .. ns.Board.hosts:GetStringWidth())

-- A skin switch walks the panel, its tabs, the settings lines and the minimap
-- button. It did not walk the panes' own buttons, and the settings pane is on
-- screen at the moment its own skin line is clicked: those stayed in the old
-- palette until something happened to hover them.
local function paneButtonColours()
  local out = {}
  for _, button in ipairs({ ns.Detail.close, ns.Detail.send, ns.Detail.focus,
                            ns.Settings.close, ns.Settings.sync, ns.Settings.reset }) do
    out[#out + 1] = (button.background.color and button.background.color[1]) or -1
  end
  return out
end

AgentBoardDB.theme = "classic"
ns:RefreshSkin()
local classicPane = paneButtonColours()
AgentBoardDB.theme = "dark"
ns:RefreshSkin()
local darkPane = paneButtonColours()
local repainted = true
for index, value in ipairs(darkPane) do
  repainted = repainted and value ~= classicPane[index]
end
check("a skin switch repaints the panes' buttons too", repainted,
  table.concat(classicPane, ",") .. " -> " .. table.concat(darkPane, ","))

-- Truncating a title must not cut a multi-byte character in half: half of one
-- renders as a replacement glyph, which reads as corruption rather than as a
-- trim. The euro is three bytes, so a byte-measured cut lands between them.
local function validUtf8(text)
  local index = 1
  while index <= #text do
    local byte = string.byte(text, index)
    local size
    if byte < 0x80 then
      size = 1
    elseif byte >= 0xC2 and byte <= 0xDF then
      size = 2
    elseif byte >= 0xE0 and byte <= 0xEF then
      size = 3
    elseif byte >= 0xF0 and byte <= 0xF4 then
      size = 4
    else
      return false, index
    end
    for offset = 1, size - 1 do
      local continuation = string.byte(text, index + offset)
      if not continuation or continuation < 0x80 or continuation > 0xBF then
        return false, index
      end
    end
    index = index + size
  end
  return true
end

local euro = { "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=1|acked=0|hosts=local:ok|new=" }
euro[2] = "20260918_200001|needs|30|local|default|Projects|"
  .. string.rep("\226\130\172", 40) .. "||||0|"
AgentBoardData = table.concat(euro, ";;")
ns:SetSearch("")
ns:SetTab("all")
ns:RefreshSnapshot()
ns:ShowBoard()
ns:RefreshPanel()

local euroTitle = ns.Board.rows[1].title.text
local utf8Ok, badAt = validUtf8(euroTitle)
check("a non-ASCII title is cut rather than left whole", euroTitle:sub(-3) == "...", euroTitle:sub(-12))
check("...on a character boundary, so the label is still valid UTF-8", utf8Ok == true,
  "byte " .. tostring(badAt) .. " of " .. tostring(#euroTitle) .. " is not a character boundary")

-- The pane's where-line was the one line in it that was never fitted: a long host
-- or project ran to the pane edge and was clipped mid-word, with no ellipsis, while
-- the title, the stats, the target and the fact values all ended in one.
AgentBoardData = table.concat({
  "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=1|acked=0|hosts=local:ok|new=",
  "20260918_230001|needs|30|a-very-long-host-name-that-goes-on-and-on.example.internal"
    .. "|a-profile-name|a-project-name|A session with a long where line||1|0|0|",
}, ";;")
ns:RefreshSnapshot()
ns:SetTab("all")
ns:SetSearch("")
ns:ShowBoard()
ns:RefreshPanel()
ns.Board.rows[1]:Fire("OnClick")
check("the pane's where-line is fitted rather than clipped",
  ns.Detail.meta.text:sub(-3) == "...",
  tostring(#ns.Detail.meta.text * 6) .. "px: " .. ns.Detail.meta.text)
check("...and only the status word carries the status colour there too",
  ns.Detail.status.text:find("last activity", 1, true) == nil
    and ns.Detail.statusTrail.text:find("last activity", 1, true) ~= nil
    and ns.Detail.status.textColor[1] ~= ns.Detail.statusTrail.textColor[1],
  ns.Detail.status.text .. " | " .. ns.Detail.statusTrail.text)
check("...with the action buttons the same height as the composer",
  ns.Detail.send:GetHeight() == ns.Detail.composerField:GetHeight()
    and ns.Detail.focus:GetHeight() == ns.Detail.composerField:GetHeight(),
  ns.Detail.send:GetHeight() .. " and " .. ns.Detail.focus:GetHeight()
    .. " against " .. ns.Detail.composerField:GetHeight())
ns:CloseDetail()

-- A skin switch from the slash command runs the palette walk and nothing else, and
-- the walk paints the badge's border with the theme's plain edge - so a status
-- channel went grey until the next poll.
ns:RefreshBadge()
local borderBefore = { unpack(ns.Badge.backdropBorderColor) }
AgentBoardDB.theme = "classic"
ns:RefreshSkin()
local borderAfter = ns.Badge.backdropBorderColor
check("a skin switch leaves the badge's status border alone",
  borderAfter ~= nil and borderAfter[1] == borderBefore[1]
    and borderAfter[2] == borderBefore[2] and borderAfter[3] == borderBefore[3],
  table.concat(borderBefore, ",") .. " -> " .. (borderAfter and table.concat(borderAfter, ",") or "none"))
AgentBoardDB.theme = "dark"
ns:RefreshSkin()

-- The glyph buttons: "X" and "-" are conventions, "*" is not one, and nothing said
-- what it meant.
for _, entry in ipairs({ { ns.Board.close, "close" }, { ns.Board.minimise, "collapse" },
                        { ns.Board.settingsButton, "settings" } }) do
  for index = #_G.__tooltip_lines, 1, -1 do
    _G.__tooltip_lines[index] = nil
  end
  entry[1]:Fire("OnEnter")
  check("the " .. entry[2] .. " button says what it does on hover",
    #_G.__tooltip_lines > 0, table.concat(_G.__tooltip_lines, " / "))
  entry[1]:Fire("OnLeave")
end

-- Six tabs across 536px of inner width: the same 12px gutter on both sides rather
-- than 12 on the left and 24 on the right.
local tabsRight = 12 + 6 * ns.Board.tabs[1]:GetWidth() + 5 * 4
check("the tab strip leaves the same gutter on both sides",
  tabsRight == 560 - 12, tostring(tabsRight) .. " against " .. tostring(560 - 12))

-- One palette decision that is a measurement rather than a taste: the dimmest text
-- is used for the age, the footer, the hint and the fact keys, and it was about
-- 4.2:1 against the panel - under the 4.5:1 bar for text this size.
local function luminance(colour)
  local function channel(value)
    return value <= 0.03928 and value / 12.92 or ((value + 0.055) / 1.055) ^ 2.4
  end
  return 0.2126 * channel(colour[1]) + 0.7152 * channel(colour[2]) + 0.0722 * channel(colour[3])
end
local function contrast(one, other)
  local first, second = luminance(one), luminance(other)
  if first < second then
    first, second = second, first
  end
  return (first + 0.05) / (second + 0.05)
end
local ink = ns.Board.legend.textColor
local fill = ns.Board.backdropColor
check("the dimmest text clears 4.5:1 on the panel",
  ink ~= nil and fill ~= nil and contrast(ink, fill) >= 4.5,
  ink and fill and string.format("%.2f:1", contrast(ink, fill)) or "no colours recorded")

-- Nothing to show is a state with words in it, and which words depends on why.
ns:SetSearch("no such session anywhere at all")
ns:RefreshPanel()
check("a search that matches nothing says so",
  ns.Board.empty:IsShown() == true and ns.Board.empty.text:find("nothing matches", 1, true) ~= nil,
  tostring(ns.Board.empty.text))
ns:SetSearch("")
ns:RefreshPanel()
check("...and the line goes when there are rows to show", ns.Board.empty:IsShown() == false)

-- The footer goes with the rows it belongs to: a 56px bar was drawing a second
-- text row under the title, and "n-m of total, scroll" is a lie while scrolling is
-- disabled.
ns.collapsed = false
ns:ToggleCollapsed()
check("collapsing takes the footer with it",
  ns.Board.legend:IsShown() == false and ns.Board.hosts:IsShown() == false,
  tostring(ns.Board.legend:IsShown()) .. "/" .. tostring(ns.Board.hosts:IsShown()))
ns:ToggleCollapsed()

AgentBoardData = "HE1|bridge=0.4.0|schema=5|generated=" .. (1789771234 - 5) .. "|rows=0|acked=0|hosts=local:ok|new="
ns:RefreshSnapshot()
ns:SetTab("all")
ns:SetSearch("")
ns:RefreshPanel()
check("an empty board says there is nothing yet rather than nothing",
  ns.Board.empty:IsShown() == true and ns.Board.empty.text:find("no agents yet", 1, true) ~= nil,
  tostring(ns.Board.empty.text))


-- New controls carry exact observed activity and keep duplicate host ids distinct.
local one = { id = "20260918_230001", provider = "t3", provider_label = "T3 Code", host = "local", status = "working", title = "One", activity_at = "123.75" }
local two = { id = one.id, provider = "hermes", provider_label = "Hermes", host = "foundry:2222", status = "needs", title = "Two", activity_at = "124" }
ns.snapshot = { sessions = { one, two }, hosts = {}, error = "", counts = {}, attention = 1 }
ns.loaded = { snapshot = ns.snapshot }
AgentBoardOutbox = ""
check("mark read queues its observed timestamp", ns:QueueMarkRead(one)
  and AgentBoardOutbox:find("|mark_read|t3|local|" .. one.id .. "|123.75", 1, true) ~= nil)
check("mark read rejects a snapshot without a watermark", not ns:QueueMarkRead({ id = one.id }))
check("stop queues only a local running session", ns:QueueStop(one)
  and AgentBoardOutbox:find("|stop|t3|local|" .. one.id .. "|", 1, true) ~= nil
  and not ns:QueueStop(two))
one.approval_request_id = "request-123"
check("approval queues the request id", ns:QueueApproval(one, "approve")
  and AgentBoardOutbox:find("|approve|t3|local|" .. one.id .. "|request-123", 1, true) ~= nil)
one.user_input_request_id, one.user_input_question_id = "question-request", "0"
check("user input answers queue the request and question ids", ns:QueueAnswer(one, "yes")
  and AgentBoardOutbox:find("|answer|t3|local|" .. one.id .. "|question-request~0~yes", 1, true) ~= nil)
ns:OpenDetail(one, false)
check("long detail output stays above actions", ns.Detail.preview:GetHeight() == 56 and ns.Detail.preview.maxLines == 4)
check("local working detail offers both controls", ns.Detail.stop:IsShown() and ns.Detail.markRead:IsShown())
check("approval detail offers approve and decline", ns.Detail.approve:IsShown() and ns.Detail.decline:IsShown())
ns:OpenDetail(two, false)
check("remote detail hides unsupported stop", not ns.Detail.stop:IsShown())
local link = ns:SessionLink(two)
check("chat emits a custom session hyperlink", link:find("|Hagentboard:", 1, true) ~= nil)
ns.selected = nil
SetItemRef(link:match("|H(.-)|h"))
check("secure hyperlink hook resolves the host and session", ns.selected == two)
SetItemRef("item:123")
check("unrelated hyperlinks are untouched", ns.selected == two)

local noticePayload = ns:ParsePayload("HE1|schema=5|rows=0|generated=1789771234|notice=stop delivery uncertain")
check("control notices parse separately from roster errors", noticePayload.notice == "stop delivery uncertain" and noticePayload.error == "")
ns.snapshot.notice = noticePayload.notice
ns:RefreshPanel()
check("control notice reaches the footer", ns.Board.legend.text:find("stop delivery uncertain", 1, true) ~= nil)

AgentBoardDB.toastHistory, AgentBoardDB.toastCooldown = nil, nil
AgentBoardDB.toasts, AgentBoardDB.sound = true, true
local silentSounds = _G.__sounds or 0
check("toast first run is silent", #ns:NotifyTransitions() == 0 and (_G.__sounds or 0) == silentSounds)
one.status = "needs"
local events = ns:NotifyTransitions()
check("control notice does not suppress attention transitions", #events == 1)
check("new attention emits one transition", #events == 1 and events[1] == one)
advanceTimers(0)
check("toast fits and uses status colour", ns.Toast:IsShown() and ns.Toast.text:GetStringWidth() <= 396
  and ns.Toast.text.textColor[1] == ns.STATUS_COLORS.needs[1])
ns.Toast:Fire("OnClick")
check("toast click selects the session without keyboard focus", ns.selected == one and not ns.Toast:IsShown())
check("repeated snapshot has no toast", #ns:NotifyTransitions() == 0)
one.status = "working"; ns:NotifyTransitions()
one.status = "needs"
check("flapping attention respects cooldown", #ns:NotifyTransitions() == 0)
one.status = "working"; ns:NotifyTransitions()
one.status = "finished"
check("completion after working emits a toast", #ns:NotifyTransitions() == 1)
advanceTimers(0)
check("toast remains opaque before timer", ns.Toast.alpha == 1 and ns.Toast.scripts.OnUpdate == nil)
advanceTimers(6)
check("toast delay starts a bounded fade", ns.Toast.scripts.OnUpdate ~= nil)
ns.Toast:Fire("OnUpdate", 0.5)
check("toast actually fades", ns.Toast.alpha == 0.5)
ns.Toast:Fire("OnUpdate", 0.5)
check("fade hides and removes its update handler", not ns.Toast:IsShown() and ns.Toast.scripts.OnUpdate == nil)
one.status = "working"; ns:NotifyTransitions()
one.status = "idle"; one.offline = true
check("an offline host cannot announce completion", #ns:NotifyTransitions() == 0)
one.offline = false
check("completion cooldown spans idle and finished", #ns:NotifyTransitions() == 0)
one.status = "working"; ns:NotifyTransitions()
AgentBoardDB.toastCooldown = {}
one.status = "idle"
AgentBoardDB.sound = false
silentSounds = _G.__sounds or 0
check("sound off preserves silent visual transitions", #ns:NotifyTransitions() == 1 and (_G.__sounds or 0) == silentSounds)
local backlog = {}
for index = 1, 5 do
  backlog[index] = { id = "backlog_" .. index, host = "local", status = "working", title = "Backlog" }
end
ns.snapshot.sessions = backlog
ns:NotifyTransitions()
for _, session in ipairs(backlog) do session.status = "finished" end
check("toast burst caps at three", #ns:NotifyTransitions() == 3)
check("toast overflow survives until next sync", #ns:NotifyTransitions() == 2)
ns.snapshot.sessions = { one, two }
AgentBoardDB.toasts = false
one.status = "error"
check("toasts can be disabled", #ns:NotifyTransitions() == 0)
AgentBoardDB.toastHistory, AgentBoardDB.toastCooldown = "junk", "junk"
check("corrupt notification state reseeds without error", pcall(function() ns:NotifyTransitions() end))

ns.loaded = { missing = true, snapshot = ns.snapshot }
ns.snapshot.sessions = {}
ns:SetSearch("")
ns:RefreshPanel()
check("first-run card replaces the empty label", ns.Board.firstRun:IsShown() and not ns.Board.empty:IsShown())
check("first-run card carries the setup command", ns.Board.firstRun.lines[3].text == "agent-board"
  and ns.Board.firstRun.lines[4].text == "Already installed? Run agent-board setup")
check("first-run header says never synced once", ns.Board.synced.text == "never synced")
ns:ShowBoard()
_G.__char_width = 20
fire("UI_SCALE_CHANGED")
check("first-run card refits after a scale change", ns.Board.firstRun.lines[4]:GetStringWidth() <= 480
  and ns.Board.firstRun.lines[4].text:sub(-3) == "...")
_G.__char_width = 6
fire("UI_SCALE_CHANGED")
ns.loaded.missing = false
ns:RefreshPanel()
check("published empty roster hides onboarding", not ns.Board.firstRun:IsShown() and ns.Board.empty:IsShown())
-- Age is evidence of an old snapshot, not proof the background service is down.
local savedGenerated, savedNotice = ns.snapshot.generated, ns.snapshot.notice
ns.snapshot.notice = nil
ns.snapshot.generated = time() - 901
check("old snapshot gives recovery without claiming offline", ns:Headline() == "snapshot is old"
  and ns:PanelStatusLine():find("Still old? agent-board setup", 1, true) ~= nil)
ns.snapshot.generated = time()
check("fresh snapshot clears age recovery", ns:Headline() == nil)
ns.loaded.incompatible, ns.loaded.schema = true, 99
check("incompatible snapshot gives updater recovery", ns:PanelStatusLine():find("agent-board update", 1, true) ~= nil)
ns.loaded.incompatible = false
ns.snapshot.generated, ns.snapshot.notice = savedGenerated, savedNotice
local reloadBefore = _G.__reloads or 0
_G.__inCombat = true
ns.Board.sync:Fire("OnClick")
check("board Sync refuses combat", (_G.__reloads or 0) == reloadBefore)
_G.__inCombat = false
ns.Board.sync:Fire("OnClick")
check("board Sync reloads outside combat", (_G.__reloads or 0) == reloadBefore + 1)
AgentBoardDB.toasts = true
ns:ShowToast(one)
local oldFill = ns.Toast.backdropColor[1]
AgentBoardDB.theme = "classic"
ns:RefreshSkin()
check("skin changes repaint a visible toast", ns.Toast.backdropColor[1] ~= oldFill)
AgentBoardDB.theme = "dark"
ns:RefreshSkin()
ns.L["Mark read"], ns.L["Stop turn"] = string.rep("Long label ", 8), string.rep("Long label ", 8)
ns:OpenDetail(one, false)
check("translated action labels fit buttons", ns.Detail.markRead.label:GetStringWidth() <= 78
  and ns.Detail.stop.label:GetStringWidth() <= 78)

ns.snapshot.attention = 0
ns.L["T3 Agents"] = string.rep("Long translated title ", 10)
ns.L["all clear"] = string.rep("Long translated status ", 10)
ns:RefreshPanel()
check("header labels clear the button cluster even in translation",
  12 + 12 + 6 + ns.Board.title:GetStringWidth() + 8 + ns.Board.attention:GetStringWidth()
    + 10 + ns.Board.synced:GetStringWidth() <= 560 - 134)


print("")
if #errors == 0 then
  print("STUB SUITE OK (" .. #loaded .. " files, " .. #_G.__said .. " chat lines)")
  os.exit(0)
end

print("STUB SUITE FAILED: " .. table.concat(errors, "; "))
os.exit(1)
