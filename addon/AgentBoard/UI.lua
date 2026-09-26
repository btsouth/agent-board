-- UI: the agent board. A badge, a panel, a detail pane, a settings pane, and a
-- minimap button.
--
-- Drawn by the game, not laid over it, so nothing can cover it and full screen
-- is fine. Frames are plain and NON-secure: nothing here acts on the game world,
-- so combat lockdown never applies to it.
--
-- Rules this file follows, because the client punishes the alternatives:
--   * fonts are referenced by NAME (passing the font object is a client error)
--   * frames that take a backdrop ask for BackdropTemplate
--   * OnUpdate only while dragging the minimap button or fading a toast;
--     toast delays use C_Timer.After and fading removes its own handler
--   * widgets are built once and reused, so a refresh writes text and toggles
--     what already exists instead of allocating
--   * text that must fit is measured with GetStringWidth, never guessed at by
--     character count: a variable-width font makes "how many letters fit" a
--     lie, and the client clips rather than ellipsises

local addonName, ns = ...

local L = ns.L

local ROW_HEIGHT = 34
local ROW_COUNT = 12
local PANEL_WIDTH = 560
local PANEL_HEIGHT = 540
local ROW_TOP = -96
local MINIMAP_SIZE = 32
-- How far a minimap button sits from the minimap's centre belongs to the
-- minimap, not to a constant: the client's own UI scale and any minimap addon
-- can resize the frame, and a fixed radius buries the button in the middle of the
-- map on anything larger than the 140 it was written against. `minimapRadius`
-- derives it from the frame instead; this is only how far the button overlaps
-- the edge, which is how the client's own minimap buttons look.
local MINIMAP_EDGE_OVERLAP = 6
local SEP = " · "

-- Room the row's right-hand columns take: age, chevron, and the panel padding.
-- The measured widths below are the arbiter; these only bound the search.
local ROW_TEXT_WIDTH = PANEL_WIDTH - 20 - 16 - 84
local DETAIL_TEXT_WIDTH = PANEL_WIDTH - 20 - 24 - 24

-- The detail pane's action row, left to right: the composer, `Hand off`, `Send`.
-- The widths live together because the composer's right inset is their sum, and
-- reserving room for `Send` alone left the last 82px of the box underneath
-- `Hand off` - where the player's own typing disappeared behind an opaque button.
local DETAIL_INSET = 12
local ACTION_GAP = 6
local SEND_WIDTH = 72
local HANDOFF_WIDTH = 118
local COMPOSER_RIGHT_INSET = DETAIL_INSET + SEND_WIDTH + ACTION_GAP + HANDOFF_WIDTH + ACTION_GAP

-- The footer is one row with two ends: a notice or the dot legend on the left, and
-- the window range and down hosts on the right. Both are capped, and the caps have
-- to fit the row together - at 380 + 230 they did not, so a stale snapshot with a
-- long host list drew the trailer straight over the legend's tail.
local FOOTER_WIDTH = PANEL_WIDTH - 24
local FOOTER_TRAILER_WIDTH = 230
local FOOTER_GAP = 24
-- Wide enough for the whole phrase. At 110 the badge cut the commonest state
-- there is - "5 need you  just now" - down to an ellipsis.
local BADGE_TEXT_WIDTH = 150

-- Font object names every client family registers; "muted" is done with colour
-- instead of the disable faces, which do vary.
local FONT_TITLE = "GameFontNormal"
local FONT_BODY = "GameFontHighlightSmall"
local FONT_SMALL = "GameFontNormalSmall"

local MUTED = { 0.58, 0.62, 0.72 }
-- Lifted from 0.44/0.47/0.56, which measured about 4.2:1 against the dark skin's
-- panel - under the 4.5:1 bar for small text, and this is the colour of the age,
-- the footer, the hint and the fact keys. ~5:1 now, and still clearly quieter than
-- MUTED.
local DIM = { 0.52, 0.55, 0.63 }
local TEXT = { 0.91, 0.93, 0.97 }
local GOLD = { 0.78, 0.64, 0.35 }
local ELLIPSIS = "..."

-- The dark T3 skin from the spec, and the plain alternative. Both are colour
-- textures, so there is no shipped art to break at an odd UI scale.
local THEMES = {
  dark = {
    name = "dark",
    bg = { 0.051, 0.071, 0.125, 0.94 },
    edge = { 0.184, 0.420, 0.847, 1 },
    row = { 0.078, 0.106, 0.176, 0.55 },
    rowAlt = { 0.063, 0.086, 0.145, 0.55 },
    highlight = { 0.184, 0.420, 0.847, 0.30 },
    button = { 0.086, 0.118, 0.196, 0.95 },
    buttonHover = { 0.145, 0.204, 0.333, 0.95 },
    buttonActive = { 0.184, 0.420, 0.847, 0.55 },
    buttonEdge = { 0.235, 0.290, 0.400, 1 },
    buttonText = { 0.82, 0.86, 0.94 },
    field = { 0.031, 0.043, 0.078, 0.85 },
  },
  classic = {
    name = "classic",
    bg = { 0.05, 0.05, 0.05, 0.90 },
    edge = { 0.60, 0.60, 0.60, 1 },
    row = { 0.11, 0.11, 0.11, 0.55 },
    rowAlt = { 0.08, 0.08, 0.08, 0.55 },
    highlight = { 0.30, 0.30, 0.30, 0.60 },
    button = { 0.16, 0.16, 0.16, 0.95 },
    buttonHover = { 0.24, 0.24, 0.24, 0.95 },
    buttonActive = { 0.36, 0.36, 0.36, 0.95 },
    buttonEdge = { 0.42, 0.42, 0.42, 1 },
    buttonText = { 0.86, 0.86, 0.86 },
    field = { 0.02, 0.02, 0.02, 0.85 },
  },
}

local BACKDROP = {
  bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
  edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
  tile = true,
  tileSize = 16,
  edgeSize = 12,
  insets = { left = 3, right = 3, top = 3, bottom = 3 },
}

local function theme()
  return THEMES[(AgentBoardDB and AgentBoardDB.theme) or "dark"] or THEMES.dark
end

--- CreateFontString with a fallback: a client that does not register the
--- preferred face gets a plain label instead of an error frame.
local function fontString(parent, layer, template)
  local ok, label = pcall(parent.CreateFontString, parent, nil, layer, template)
  if ok and label then
    return label
  end
  return parent:CreateFontString(nil, layer, "GameFontNormalSmall")
end

local function colorize(label, color)
  label:SetTextColor(color[1], color[2], color[3])
  return label
end

--- A colour texture behind a frame that wants a backdrop, when it has none.
---
--- On a client without the BackdropTemplate mixin the frame simply has no
--- SetBackdrop, and an error frame on login is a worse outcome than a panel
--- without its border. The art is drawn with colour textures, so nothing else
--- depends on the template.
local function backdropFrame(kind, name, parent, template)
  local ok, frame = pcall(CreateFrame, kind, name, parent, template)
  if ok and frame then
    return frame
  end
  return CreateFrame(kind, name, parent)
end

local function applyBackdrop(frame, alpha, background, edge)
  if not frame or not frame.SetBackdrop then
    return false
  end

  frame:SetBackdrop(BACKDROP)
  local palette = theme()
  local fill = background or palette.bg
  frame:SetBackdropColor(fill[1], fill[2], fill[3], alpha or fill[4])
  local line = edge or palette.edge
  frame:SetBackdropBorderColor(line[1], line[2], line[3], line[4] or 1)
  return true
end

--- The tooltip border, sized for a 22px field instead of a tooltip.
---
--- `BACKDROP` is built for tooltips and for the panel; its 12px edge and 3px
--- insets are most of the height of an input box, so its corners meet in the
--- middle and the field reads as two nested rectangles rather than one box. Small
--- chrome gets the same art at a size that suits it.
local FIELD_BACKDROP = {
  bgFile = BACKDROP.bgFile,
  edgeFile = BACKDROP.edgeFile,
  tile = true,
  tileSize = 16,
  edgeSize = 6,
  insets = { left = 2, right = 2, top = 2, bottom = 2 },
}

local function applyFieldBackdrop(frame, fill, edge)
  if not frame or not frame.SetBackdrop then
    return false
  end

  frame:SetBackdrop(FIELD_BACKDROP)
  frame:SetBackdropColor(fill[1], fill[2], fill[3], fill[4])
  frame:SetBackdropBorderColor(edge[1], edge[2], edge[3], edge[4] or 1)
  return true
end

--- A border colour, on the frames that have a backdrop and quietly on the ones
--- that do not.
---
--- `backdropFrame` falls back to a plain frame on a client without the
--- BackdropTemplate mixin, which is why `applyBackdrop` asks before it paints.
--- Anything else that paints a border by hand has to ask too, or the error frame
--- the fallback exists to prevent arrives at login instead.
local function setBorder(frame, colour)
  if frame and frame.SetBackdropBorderColor then
    frame:SetBackdropBorderColor(colour[1], colour[2], colour[3], colour[4] or 1)
  end
end

--- A saved position, or the default.
---
--- These values come back from SavedVariables, which is a file the player can
--- edit and a patch can leave stale: a string or a short table here would raise
--- "Usage: SetPoint(...)" during BuildBadge and take the whole addon with it.
local ANCHORS = {
  TOPLEFT = true, TOP = true, TOPRIGHT = true,
  LEFT = true, CENTER = true, RIGHT = true,
  BOTTOMLEFT = true, BOTTOM = true, BOTTOMRIGHT = true,
}

local function savedPoint(value, default)
  if type(value) == "table" and ANCHORS[value[1]] and tonumber(value[2]) and tonumber(value[3]) then
    return value
  end
  return default
end

--- Where a drag left a frame, in the shape `savedPoint` reads back.
---
--- `GetPoint` returns point, relativeTo, relativePoint, x, y: the offsets are the
--- fourth and fifth values, not the third and fourth. Reading `x, y` off a
--- four-name list puts the *relativePoint* string ("CENTER") into `x`, and the
--- arithmetic below then raises "attempt to perform arithmetic on local 'x' (a
--- string value)" on the way out of every drag.
local function draggedPoint(frame)
  local point, _, _, x, y = frame:GetPoint()
  return { point, math.floor(x + 0.5), math.floor(y + 0.5) }
end

--- Step back off the middle of a multi-byte character.
---
--- Titles come from the session store and are routinely non-ASCII. Cutting one
--- between the bytes of a character renders a replacement glyph, which looks like
--- corruption rather than like a trim.
local function byteSafe(text, index)
  while index > 0 do
    local byte = string.byte(text, index)
    if not byte or byte < 0x80 then
      return index
    end
    if byte >= 0xC0 then
      -- A lead byte with its continuation bytes on the far side of the cut. The
      -- last byte of any multi-byte character is a continuation byte, so walking
      -- back lands here for every title that ends in one - and returning this
      -- index kept the lead byte on its own, which is the replacement glyph this
      -- function exists to avoid.
      return index - 1
    end
    index = index - 1
  end
  return 0
end

--- The longest prefix of `text` that fits `maxWidth`, with an ellipsis when it
--- had to be cut. One measurement for text that fits, a handful for text that
--- does not, and never a guess from a character count.
local function fitText(label, text, maxWidth)
  text = tostring(text or "")
  label:SetText(text)

  if maxWidth <= 0 or not label.GetStringWidth then
    return text
  end
  if label:GetStringWidth() <= maxWidth then
    return text
  end

  local best = 1
  local low, high = 1, #text
  while low <= high do
    local middle = math.floor((low + high) / 2)
    label:SetText(string.sub(text, 1, middle) .. ELLIPSIS)
    if label:GetStringWidth() <= maxWidth then
      best = middle
      low = middle + 1
    else
      high = middle - 1
    end
  end

  -- Prefer cutting at a space, a slash or a hyphen: "Review the Lua addon
  -- code..." reads as trimmed, and so does a long project path -- whereas
  -- ".../hom" reads as data loss.
  local withBoundary = string.sub(text, 1, best):match("^(.*)[%s/%-]%S*$")
  if withBoundary and #withBoundary >= math.floor(best * 0.6) then
    best = #withBoundary
  end

  local fitted = string.sub(text, 1, byteSafe(text, best)) .. ELLIPSIS
  label:SetText(fitted)
  return fitted
end

--- Hard cap for text going somewhere that wraps (tooltips, the preview line):
--- wrapping handles the width, this only stops a wall of text from swallowing
--- the frame.
local function shorten(text, limit)
  text = tostring(text or "")
  if #text <= limit then
    return text
  end

  local head = string.sub(text, 1, limit - #ELLIPSIS)
  -- Cut at the last sentence or line end if one is close by: "...green on 3.10"
  -- reads as trimmed, "...green on 3.10/3.1" reads as broken.
  local boundary = head:match("^.*[%.!?%c]%s?")
  if boundary and #boundary >= math.floor(limit * 0.5) then
    head = boundary:gsub("%s+$", "")
  end
  return string.sub(head, 1, byteSafe(head, #head)) .. ELLIPSIS
end

local function makeDot(parent, size)
  local dot = parent:CreateTexture(nil, "ARTWORK")
  dot:SetSize(size or 7, size or 7)
  dot:SetColorTexture(0.5, 0.5, 0.5, 1)
  return dot
end

--- A flat button in the panel's own colours.
---
--- Deliberately NOT `UIPanelButtonTemplate`: that draws the client's beige
--- buttons, which fight a dark panel and would be the only part of this window
--- not under the theme's control. Four textures and a label are cheaper than a
--- template and stay consistent when the skin changes.
--- `tooltip` is for the buttons whose face is a glyph rather than a word: `X` and
--- `-` are conventions, `*` is not one, and nothing on hover said what it meant.
local function makeButton(parent, width, height, label, onClick, tooltip)
  local button = CreateFrame("Button", nil, parent)
  button:SetSize(width, height)
  button:EnableMouse(true)

  local background = button:CreateTexture(nil, "BACKGROUND")
  background:SetAllPoints()
  button.background = background

  button.label = colorize(fontString(button, "OVERLAY", FONT_SMALL), theme().buttonText)
  button.label:SetPoint("CENTER", 0, 0)
  button.label:SetText(label or "")

  button:SetScript("OnEnter", function(self)
    self.hovered = true
    ns:SkinButton(self)
    if tooltip and GameTooltip then
      GameTooltip:SetOwner(self, "ANCHOR_TOP")
      GameTooltip:AddLine(tooltip)
      GameTooltip:Show()
    end
  end)
  button:SetScript("OnLeave", function(self)
    self.hovered = false
    ns:SkinButton(self)
    if tooltip and GameTooltip then
      GameTooltip:Hide()
    end
  end)
  if onClick then
    button:SetScript("OnClick", onClick)
  end

  ns:SkinButton(button)
  return button
end

--- One place decides how a button looks, so hover, active and normal can never
--- disagree with each other or with the theme.
function ns:SkinButton(button)
  local palette = theme()
  local color = button.active and palette.buttonActive
    or (button.hovered and palette.buttonHover or palette.button)

  button.background:SetColorTexture(color[1], color[2], color[3], color[4] or 1)
  button.label:SetTextColor(palette.buttonText[1], palette.buttonText[2], palette.buttonText[3])
end

local function setButtonText(button, text)
  button.text = text
  button.label:SetText(text)
end

local function setButtonActive(button, active)
  if button.active ~= active then
    button.active = active
    ns:SkinButton(button)
  end
end

-- ------------------------------------------------------------------- badge --

--- Where the badge sits: the player's saved spot, or, until they drag it, under
--- the minimap cluster.
---
--- Anchored to the minimap rather than to a screen corner because a fixed offset
--- has to guess how tall the minimap is, and guessing 140 put a 230-wide badge
--- straight across the middle of the map on every larger one.
function ns:PlaceBadge(badge)
  badge = badge or self.Badge
  if not badge then
    return
  end

  badge:ClearAllPoints()
  local saved = savedPoint(AgentBoardDB.point, nil)
  if saved then
    badge:SetPoint(saved[1], UIParent, saved[1], saved[2], saved[3])
  elseif Minimap then
    badge:SetPoint("TOP", Minimap, "BOTTOM", 0, -4)
  else
    badge:SetPoint(ns.DEFAULT_BADGE_POINT[1], UIParent, ns.DEFAULT_BADGE_POINT[1],
      ns.DEFAULT_BADGE_POINT[2], ns.DEFAULT_BADGE_POINT[3])
  end
end

function ns:BuildBadge()
  if self.Badge then
    return self.Badge
  end

  local badge = backdropFrame("Frame", "AgentBoardBadge", UIParent, "BackdropTemplate")
  badge:SetSize(230, 30)
  badge:SetFrameStrata("MEDIUM")
  badge:SetClampedToScreen(true)
  badge:SetMovable(true)
  badge:EnableMouse(true)
  badge:RegisterForDrag("LeftButton")
  applyBackdrop(badge)

  ns:PlaceBadge(badge)

  badge:SetScript("OnDragStart", function(self)
    self.moved = true
    self:StartMoving()
  end)
  badge:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    AgentBoardDB.point = draggedPoint(self)
  end)
  badge:SetScript("OnMouseUp", function(self, button)
    -- A drag ends with a mouse-up on the same frame: without this, moving the
    -- badge also opens the board.
    if self.moved then
      self.moved = nil
      return
    end
    if button == "LeftButton" then
      ns:ToggleBoard()
    end
  end)

  -- The bar says the one thing worth acting on and nothing else, so the rest -
  -- the age it dropped to fit, the breakdown behind the dots, what a click and a
  -- drag do - has to be reachable by hovering it.
  badge:SetScript("OnEnter", function(self)
    if not GameTooltip then
      return
    end
    GameTooltip:SetOwner(self, "ANCHOR_TOPLEFT")
    GameTooltip:AddLine(L["agents"], TEXT[1], TEXT[2], TEXT[3])
    GameTooltip:AddLine(ns:BadgeSummary(), 0.7, 0.7, 0.7)
    GameTooltip:AddLine(L["left click: the board. drag: move."], 0.7, 0.7, 0.7)
    GameTooltip:Show()
  end)
  badge:SetScript("OnLeave", function()
    if GameTooltip then
      GameTooltip:Hide()
    end
  end)

  local crest = colorize(fontString(badge, "OVERLAY", FONT_TITLE), GOLD)
  crest:SetPoint("LEFT", 8, 0)
  crest:SetText("H")

  local text = fontString(badge, "OVERLAY", FONT_TITLE)
  text:SetPoint("LEFT", crest, "RIGHT", 6, 0)
  text:SetJustifyH("LEFT")
  badge.text = text

  -- Four dots is what fits beside the words at this size. Which four is decided
  -- per refresh from what is actually there, so a busy board shows the loudest
  -- four rather than the first four of a fixed list.
  local dots = {}
  for index = 1, 4 do
    local item = makeDot(badge, 7)
    item:SetPoint("RIGHT", badge, "RIGHT", -10 - (index - 1) * 11, 0)
    item:Hide()
    dots[index] = item
  end
  badge.dots = dots

  self.Badge = badge
  return badge
end

--- Counts per status, quiet buckets dropped: the dots and the badge words are two
--- channels for the same fact, so a colour-blind player loses nothing.
function ns:BusyStatuses()
  local counts = self:Data().counts or {}
  local out = {}
  for _, status in ipairs(self.STATUS_ORDER) do
    local count = tonumber(counts[status]) or 0
    if count > 0 and status ~= "idle" and status ~= "finished" then
      out[#out + 1] = { status = status, count = count }
    end
  end
  return out
end

--- The badge's words, without the age.
---
--- The bar is 230px and the age is the longest and least urgent of the things on
--- it, so it is the first to be cut - at which point the player is reading a
--- label that ends in an ellipsis on the most common state there is. The age
--- lives in the tooltip, where there is room for it, and in the board's header.
function ns:BadgeLabel()
  -- No snapshot is its own state, not a quiet day: the badge says which one it is
  -- rather than reporting "idle" about work it has never seen.
  local headline = self:Headline()
  if headline then
    return headline
  end

  local attention = self:Attention()
  if attention > 0 then
    local text = attention == 1 and L["1 needs you"] or ns.Lf("%d need you", attention)
    if self:NewCount() > 0 then
      text = text .. ns.Lf("  +%d new", self:NewCount())
    end
    return text
  end

  local working = tonumber((self:Data().counts or {}).working) or 0
  if working > 0 then
    return ns.Lf("%d working", working)
  end
  return L["idle"]
end

--- Everything the badge knows, for the tooltip: the label, the age it left out,
--- and the breakdown behind the dots.
function ns:BadgeSummary()
  local parts = { ns.Lf("%s  %s", self:BadgeLabel(), self:AgeLabel(self:SnapshotAge())) }
  for _, entry in ipairs(self:BusyStatuses()) do
    parts[#parts + 1] = ns.Lf("%s %d", self:StatusLabel(entry.status), entry.count)
  end
  return table.concat(parts, SEP)
end

function ns:RefreshBadge()
  local badge = self:BuildBadge()
  local attention = self:Attention()

  badge:SetShown(AgentBoardDB.badge ~= false)
  fitText(badge.text, self:BadgeLabel(), BADGE_TEXT_WIDTH)

  if self:Headline() then
    badge.text:SetTextColor(unpack(self.STATUS_COLORS.error))
    setBorder(badge, self.STATUS_COLORS.error)
    for _, dot in ipairs(badge.dots) do
      dot.status = nil
      dot:Hide()
    end
    return
  end

  if attention > 0 then
    badge.text:SetTextColor(unpack(self.STATUS_COLORS.needs))
    setBorder(badge, self.STATUS_COLORS.needs)
  else
    local working = tonumber((self:Data().counts or {}).working) or 0
    if working > 0 then
      badge.text:SetTextColor(unpack(self.STATUS_COLORS.working))
    else
      badge.text:SetTextColor(MUTED[1], MUTED[2], MUTED[3])
    end
    setBorder(badge, GOLD)
  end

  local busy = self:BusyStatuses()
  for index, dot in ipairs(badge.dots) do
    local entry = busy[index]
    if entry then
      if dot.status ~= entry.status then
        dot.status = entry.status
        dot:SetColorTexture(self:StatusColor(entry.status))
      end
      dot:Show()
    else
      dot.status = nil
      dot:Hide()
    end
  end
end

-- ------------------------------------------------------------ minimap button --

--- The ring a minimap button sits on, for the minimap as it is right now.
---
--- `Minimap:GetWidth()` is measured in the minimap's own coordinate space, which
--- is also the space a child button is offset in, so no scale correction is
--- needed. A 140px minimap gives the 80 this used to hard-code; a 220px one
--- gives 120, which is the whole point - the button keeps to the edge.
local function minimapRadius()
  local width = 140
  if Minimap and Minimap.GetWidth then
    width = tonumber(Minimap:GetWidth()) or width
  end
  return width / 2 + MINIMAP_SIZE / 2 - MINIMAP_EDGE_OVERLAP
end

function ns:BuildMinimapButton()
  if self.MinimapButton or not Minimap then
    return self.MinimapButton
  end

  local button = CreateFrame("Button", "AgentBoardMinimapButton", Minimap)
  button:SetSize(MINIMAP_SIZE, MINIMAP_SIZE)
  button:SetFrameStrata("MEDIUM")
  button:EnableMouse(true)
  button:RegisterForDrag("LeftButton")
  button:RegisterForClicks("AnyUp")
  button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")

  -- The client's own minimap-button disc, over a themed square that is small
  -- enough to sit entirely inside it. That way the button is round and matches
  -- the game's other minimap buttons where the art is, and still has a body
  -- rather than a hole where it is not.
  local background = button:CreateTexture(nil, "BACKGROUND")
  background:SetSize(20, 20)
  background:SetPoint("CENTER")
  button.background = background

  -- Opaque on purpose. The art is a soft-edged disc, so anything translucent lets
  -- the themed square underneath show its corners through it and the button reads
  -- as a blob rather than a disc.
  local disc = button:CreateTexture(nil, "BACKGROUND", nil, 1)
  disc:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
  disc:SetSize(30, 30)
  disc:SetPoint("CENTER")
  button.disc = disc

  local crest = colorize(fontString(button, "OVERLAY", FONT_TITLE), GOLD)
  crest:SetPoint("CENTER", -2, 2)
  crest:SetText("H")

  -- The count rides on the disc itself. It used to be a 15px square chip, which
  -- covered nearly half of a 32px button and set a square badge on a round one -
  -- two seams in the space of an icon. The disc is the contrast the chip was
  -- there to provide, and the shadow covers the case where the art is missing.
  local badge = colorize(fontString(button, "OVERLAY", FONT_SMALL), TEXT)
  badge:SetPoint("BOTTOMRIGHT", -3, 3)
  badge:SetText("")
  badge:SetShadowOffset(1, -1)
  badge:SetShadowColor(0, 0, 0, 0.9)
  button.badge = badge

  button:SetScript("OnClick", function(_, mouseButton)
    if mouseButton == "RightButton" then
      ns:Sync("minimap")
    else
      ns:ToggleBoard()
    end
  end)

  -- Dragging is the one place an OnUpdate is justified: nothing else can follow
  -- the cursor, and it exists only for the duration of the drag.
  button:SetScript("OnDragStart", function(self)
    self:SetScript("OnUpdate", function()
      if not Minimap or not GetCursorPosition then
        return
      end
      local mx, my = Minimap:GetCenter()
      local px, py = GetCursorPosition()
      local scale = Minimap:GetEffectiveScale()
      px, py = px / scale, py / scale
      AgentBoardDB.minimapAngle = math.deg(math.atan2(py - my, px - mx))
      ns:PositionMinimapButton()
    end)
  end)
  button:SetScript("OnDragStop", function(self)
    self:SetScript("OnUpdate", nil)
  end)
  button:SetScript("OnEnter", function(self)
    if GameTooltip then
      GameTooltip:SetOwner(self, "ANCHOR_LEFT")
      GameTooltip:AddLine(L["agents"], TEXT[1], TEXT[2], TEXT[3])
      GameTooltip:AddLine(L["left click: the board. right click: sync. drag: move."], 0.7, 0.7, 0.7)
      GameTooltip:Show()
    end
  end)
  button:SetScript("OnLeave", function()
    if GameTooltip then
      GameTooltip:Hide()
    end
  end)

  -- The client's UI scale and other minimap addons both resize the map, and the
  -- button's coordinates are meaningless against a frame that changed size.
  if hooksecurefunc and Minimap.SetSize then
    hooksecurefunc(Minimap, "SetSize", function()
      ns:PositionMinimapButton()
    end)
  end

  self.MinimapButton = button
  self:PositionMinimapButton()
  self:SkinMinimapButton()
  button:SetShown(AgentBoardDB.minimap ~= false)
  return button
end

function ns:PositionMinimapButton()
  local button = self.MinimapButton
  if not button or not Minimap then
    return
  end

  -- Placed on a ring around the minimap rather than at a corner, which is how
  -- every other minimap button behaves and what players expect to drag. The
  -- radius follows the map's current size, not the size it had at build time.
  local angle = math.rad(tonumber(AgentBoardDB and AgentBoardDB.minimapAngle) or 250)
  local radius = minimapRadius()
  button:ClearAllPoints()
  button:SetPoint("CENTER", Minimap, "CENTER", math.cos(angle) * radius, math.sin(angle) * radius)
end

function ns:SkinMinimapButton()
  local button = self.MinimapButton
  if not button or not button.background then
    return
  end
  local palette = theme()
  button.background:SetColorTexture(palette.bg[1], palette.bg[2], palette.bg[3], 1)
end

function ns:RefreshMinimapButton()
  local button = self:BuildMinimapButton()
  if not button then
    return
  end

  button:SetShown(AgentBoardDB.minimap ~= false)

  -- Re-placed on every refresh as well as on a resize: the minimap only reaches
  -- its final size after the client has laid out, which is later than the build.
  self:PositionMinimapButton()

  local attention = self:Attention()
  local count, colour
  if attention > 0 then
    count, colour = attention, self.STATUS_COLORS.needs
  else
    local working = tonumber((self:Data().counts or {}).working) or 0
    if working > 0 then
      count, colour = working, self.STATUS_COLORS.working
    end
  end

  -- Nothing to count hides, rather than showing a zero: a button wearing "0"
  -- reads as a state rather than as an absence.
  if count then
    button.badge:SetText(tostring(count))
    button.badge:SetTextColor(unpack(colour))
    button.badge:Show()
  else
    button.badge:SetText("")
    button.badge:Hide()
  end
end

-- ------------------------------------------------------------------- panel --

function ns:BuildPanel()
  if self.Board then
    return self.Board
  end

  local panel = backdropFrame("Frame", "AgentBoardPanel", UIParent, "BackdropTemplate")
  panel:SetSize(PANEL_WIDTH, PANEL_HEIGHT)
  panel:SetFrameStrata("DIALOG")
  panel:SetClampedToScreen(true)
  panel:SetMovable(true)
  panel:EnableMouse(true)
  panel:EnableMouseWheel(true)
  panel:RegisterForDrag("LeftButton")
  applyBackdrop(panel, 0.96)

  -- Escape closes it like any other client window, and hiding the panel hides its
  -- panes, so no stale detail pane waits behind a closed board.
  if UISpecialFrames then
    local found = false
    for _, name in ipairs(UISpecialFrames) do
      found = found or name == "AgentBoardPanel"
    end
    if not found then
      table.insert(UISpecialFrames, "AgentBoardPanel")
    end
  end

  local point = savedPoint(AgentBoardDB.panelPoint, ns.DEFAULT_PANEL_POINT)
  panel:SetPoint(point[1], UIParent, point[1], point[2], point[3])

  panel:SetScript("OnDragStart", function(self)
    self:StartMoving()
  end)
  panel:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    AgentBoardDB.panelPoint = draggedPoint(self)
  end)
  panel:SetScript("OnMouseWheel", function(_, delta)
    ns:ScrollRows(delta)
  end)
  panel:SetScript("OnHide", function()
    ns.offset = 0
    ns:CloseDetail()
    if ns.New then
      ns.New:Hide()
    end
    if ns.Settings then
      ns.Settings:Hide()
    end
  end)

  -- header
  local crest = colorize(fontString(panel, "OVERLAY", FONT_TITLE), GOLD)
  crest:SetPoint("TOPLEFT", 12, -11)
  crest:SetText("T3")

  local title = colorize(fontString(panel, "OVERLAY", FONT_TITLE), TEXT)
  title:SetPoint("LEFT", crest, "RIGHT", 6, 0)
  title:SetText(L["Agent Board"])
  panel.title = title

  local attention = colorize(fontString(panel, "OVERLAY", FONT_BODY), ns.STATUS_COLORS.needs)
  attention:SetPoint("LEFT", title, "RIGHT", 8, 0)
  panel.attention = attention

  local synced = colorize(fontString(panel, "OVERLAY", FONT_SMALL), DIM)
  synced:SetPoint("LEFT", attention, "RIGHT", 10, 0)
  synced:SetJustifyH("LEFT")
  synced:SetWordWrap(false)
  panel.synced = synced

  local close = makeButton(panel, 22, 18, "X", function()
    ns:HideBoard()
  end, L["Close the board"])
  close:SetPoint("TOPRIGHT", -8, -9)
  panel.close = close

  local minimise = makeButton(panel, 22, 18, "-", function()
    ns:ToggleCollapsed()
  end, L["Collapse the board to the title bar"])
  minimise:SetPoint("RIGHT", close, "LEFT", -4, 0)
  panel.minimise = minimise

  local settingsButton = makeButton(panel, 22, 18, "*", function()
    ns:ToggleSettings()
  end, L["Settings: skin, sound, refresh policy"])
  settingsButton:SetPoint("RIGHT", minimise, "LEFT", -4, 0)
  panel.settingsButton = settingsButton
  panel.sync = makeButton(panel, 48, 18, L["Sync"], function()
    ns:Sync("board")
  end, L["Sync T3 (reloads the UI)"])
  panel.sync:SetPoint("RIGHT", settingsButton, "LEFT", -4, 0)

  panel.new = makeButton(panel, 42, 18, L["New"], function()
    ns:ToggleNew()
  end, L["Start a new agent session"])
  panel.new:SetPoint("RIGHT", panel.sync, "LEFT", -4, 0)

  -- search: a plain field over the panel, with its own inset box so the text does
  -- not float on the panel colour
  local field = backdropFrame("Frame", nil, panel, "BackdropTemplate")
  field:SetPoint("TOPLEFT", 12, -40)
  field:SetPoint("TOPRIGHT", -12, -40)
  field:SetHeight(22)
  applyFieldBackdrop(field, theme().field, theme().buttonEdge)
  panel.searchField = field

  local search = CreateFrame("EditBox", "AgentBoardSearch", panel, "InputBoxTemplate")
  search:SetHeight(22)
  search:SetPoint("TOPLEFT", 12, -40)
  search:SetPoint("TOPRIGHT", -12, -40)
  search:SetAutoFocus(false)
  search:SetTextInsets(8, 6, 0, 0)
  search:SetScript("OnEscapePressed", function(self)
    self:SetText("")
    ns:SetSearch("")
    self:ClearFocus()
  end)
  search:SetScript("OnTextChanged", function(self, userInput)
    if userInput then
      ns:SetSearch(self:GetText())
    end
  end)
  local placeholder = colorize(fontString(panel, "OVERLAY", FONT_SMALL), DIM)
  placeholder:SetPoint("LEFT", search, "LEFT", 8, 0)
  placeholder:SetText(L["Search agents, threads, or projects..."])
  placeholder:SetShown(true)
  panel.search = search
  panel.placeholder = placeholder

  -- tabs
  panel.tabs = {}
  local previous
  -- Six tabs and five 4px gaps fill the panel's inner width exactly (6*86 + 20 =
  -- 536), so the strip is inset the same 12px on both sides instead of leaving a
  -- visibly wider gutter on the right.
  for index, tab in ipairs(ns.TABS) do
    local button = makeButton(panel, 86, 20, tab.label, nil)
    local key = tab.key
    button:SetScript("OnClick", function()
      ns:SetTab(key)
    end)
    if previous then
      button:SetPoint("LEFT", previous, "RIGHT", 4, 0)
    else
      button:SetPoint("TOPLEFT", 12, -68)
    end
    panel.tabs[index] = button
    previous = button
  end

  -- rows: built once. Two background textures exist so a refresh shows one and
  -- hides the other instead of writing a colour every time.
  panel.rows = {}
  for index = 1, ROW_COUNT do
    local row = CreateFrame("Button", nil, panel)
    row:SetHeight(ROW_HEIGHT)
    row:SetPoint("TOPLEFT", 10, ROW_TOP - (index - 1) * ROW_HEIGHT)
    row:SetPoint("RIGHT", panel, "RIGHT", -10, 0)

    row.even = row:CreateTexture(nil, "BACKGROUND")
    row.even:SetAllPoints()
    row.even:Hide()

    row.odd = row:CreateTexture(nil, "BACKGROUND")
    row.odd:SetAllPoints()

    row.highlight = row:CreateTexture(nil, "HIGHLIGHT")
    row.highlight:SetAllPoints()

    row.dot = makeDot(row, 8)
    row.dot:SetPoint("LEFT", 6, 0)

    row.title = colorize(fontString(row, "OVERLAY", FONT_BODY), TEXT)
    row.title:SetPoint("TOPLEFT", row.dot, "TOPRIGHT", 8, -3)
    row.title:SetWidth(ROW_TEXT_WIDTH)
    row.title:SetJustifyH("LEFT")
    row.title:SetWordWrap(false)

    -- Two pieces on one line: the status word under the title, and the trail hung
    -- off its right edge. The trail's room is measured from the word on every
    -- refresh, so a long status cannot push it under the age column.
    row.status = colorize(fontString(row, "OVERLAY", FONT_SMALL), MUTED)
    row.status:SetPoint("TOPLEFT", row.title, "BOTTOMLEFT", 0, -1)

    row.meta = colorize(fontString(row, "OVERLAY", FONT_SMALL), MUTED)
    row.meta:SetPoint("TOPLEFT", row.status, "TOPRIGHT", 5, 0)
    row.meta:SetJustifyH("LEFT")
    row.meta:SetWordWrap(false)

    row.age = colorize(fontString(row, "OVERLAY", FONT_SMALL), DIM)
    row.age:SetPoint("RIGHT", row, "RIGHT", -26, 0)

    row.chevron = colorize(fontString(row, "OVERLAY", FONT_BODY), MUTED)
    row.chevron:SetPoint("RIGHT", row, "RIGHT", -8, 0)
    row.chevron:SetText(">")

    row:SetScript("OnClick", function()
      ns:OpenDetail(row.session, true)
    end)
    row:SetScript("OnEnter", function()
      ns:ShowRowTooltip(row)
    end)
    row:SetScript("OnLeave", function()
      if GameTooltip then
        GameTooltip:Hide()
      end
    end)

    panel.rows[index] = row
  end

  -- footer: the legend on the left, and on the right anything the list itself has
  -- to admit to (a window range, or a machine that did not answer)
  local legend = colorize(fontString(panel, "OVERLAY", FONT_SMALL), DIM)
  legend:SetPoint("BOTTOMLEFT", 12, 8)
  legend:SetText(ns:LegendText())
  panel.legend = legend

  local hosts = colorize(fontString(panel, "OVERLAY", FONT_SMALL), DIM)
  hosts:SetPoint("BOTTOMRIGHT", -12, 8)
  hosts:SetWidth(FOOTER_TRAILER_WIDTH)
  hosts:SetJustifyH("RIGHT")
  hosts:SetWordWrap(false)
  panel.hosts = hosts

  -- An empty list is not a state, it is two: nothing has run yet, or nothing
  -- matches what the player typed. A blank rectangle under a header reading "all
  -- clear" said neither, so a filtered search looked like a working board.
  local empty = colorize(fontString(panel, "OVERLAY", FONT_BODY), MUTED)
  empty:SetPoint("CENTER", panel, "CENTER", 0, -34)
  empty:SetWidth(PANEL_WIDTH - 80)
  empty:SetJustifyH("CENTER")
  empty:Hide()
  panel.empty = empty

  local firstRun = CreateFrame("Frame", nil, panel)
  firstRun:SetPoint("TOPLEFT", 40, -150)
  firstRun:SetSize(PANEL_WIDTH - 80, 160)
  firstRun.lines = {}
  for index, text in ipairs({ L["Your agents, in Azeroth"],
      L["See what needs you, reply, and start agents from the game."], L["agent-board"],
      L["Already installed? Run agent-board setup"],
      L["Finish setup on your computer, then press Sync."] }) do
    local line = colorize(fontString(firstRun, "OVERLAY", index == 1 and FONT_TITLE or FONT_BODY), TEXT)
    line:SetPoint("TOPLEFT", 0, -(index - 1) * 32)
    line.fullText = text
    fitText(line, text, PANEL_WIDTH - 80)
    firstRun.lines[index] = line
  end
  panel.firstRun = firstRun

  panel:Hide()
  self.Board = panel
  return panel
end

--- Empty and degraded states, which must never read as "your agents are idle".
function ns:PanelStatusLine()
  if self:IsMissing() then
    return L["No snapshot: run agent-board setup on your computer."]
  end
  if self.loaded and self.loaded.incompatible then
    return ns.Lf("payload v%d / addon v%d: run agent-board update, then Sync",
      tonumber(self.loaded.schema) or 0, self.PAYLOAD_SCHEMA)
  end
  if self:IsStale() then
    return ns.Lf("Last good snapshot (%s). Run agent-board setup, then Sync.",
      self:AgeLabel(self:SnapshotAge()))
  end
  local bridgeError = self:BridgeError()
  if bridgeError ~= "" then
    return ns.Lf("Run agent-board setup: bridge cannot read the store (%s)", bridgeError)
  end
  local notice = self:Data().notice
  if notice and notice ~= "" then return notice end
  local age = self:SnapshotAge()
  if age and age > 900 then
    return L["Snapshot older than 15m: Sync. Still old? agent-board setup"]
  end
  return nil
end

--- The words for the statuses the badge dots can show, in the same order and
--- from the same filter. A legend that lists "finished" while the dots never
--- draw it, and omits "error" while they do, describes nothing.
function ns:LegendText()
  local parts = {}
  for _, status in ipairs(self.STATUS_ORDER) do
    if status ~= "idle" and status ~= "finished" then
      parts[#parts + 1] = self:StatusLabel(status)
    end
  end
  return table.concat(parts, " / ")
end

--- Is this session's machine out of reach?
---
--- Two sources can say so: the per-row flag the bridge sets while merging, and
--- the host map in the header. Either is enough, because a row that contradicts
--- the footer line about its own machine is worse than a row that is merely
--- dimmed.
function ns:IsOffline(session)
  if not session then
    return false
  end
  if session.offline then
    return true
  end
  local host = session.host
  if not host or host == "local" then
    return false
  end
  return self:HostStatus()[host] ~= "ok"
end

--- Paint the rows for the current tab, search and offset.
---
--- One pass over everything that can change, with a cheap "did this actually
--- change" guard on the textures: the panel is refreshed on every click and
--- every keypress in the search box, and re-setting the same colour 12 times per
--- keystroke is the sort of thing that shows up as input lag in a raid.
function ns:RefreshPanel()
  local panel = self:BuildPanel()
  local sessions = self:Filtered()
  local attention = self:Attention()
  local palette = theme()

  -- The list can shrink under the window (a new snapshot, a narrower search).
  local maxOffset = math.max(0, #sessions - ROW_COUNT)
  self.offset = math.min(maxOffset, math.max(0, self.offset or 0))

  local headline = self:Headline()
  local attentionText = headline or (attention > 0
    and (attention == 1 and L["1 needs you"] or ns.Lf("%d need you", attention))
    or L["all clear"])
  -- All three labels share the space before the 126px button cluster.
  fitText(panel.title, L["Agent Board"], 180)
  local headerSpace = math.max(0, PANEL_WIDTH - 166 - panel.title:GetStringWidth() - 24)
  fitText(panel.attention, attentionText, headerSpace * 0.55)
  panel.attention:SetTextColor(unpack(
    headline and self.STATUS_COLORS.error
      or (attention > 0 and self.STATUS_COLORS.needs or self.STATUS_COLORS.reply)))

  local synced = self:IsMissing() and L["never synced"]
    or ns.Lf("synced %s", self:AgeLabel(self:SnapshotAge()))
  if self:NewCount() > 0 then
    synced = ns.Lf("+%d new, %s", self:NewCount(), synced)
  end
  fitText(panel.synced, synced, math.max(0, headerSpace - panel.attention:GetStringWidth() - 10))

  -- Where you are in the list, then who is down. A list longer than the panel
  -- must say so, or the missing rows look like they do not exist.
  local trailer = {}
  if #sessions > ROW_COUNT then
    trailer[#trailer + 1] = ns.Lf("%d-%d of %d, scroll",
      self.offset + 1, math.min(self.offset + ROW_COUNT, #sessions), #sessions)
  end
  local rejected = self:RejectedCount()
  if rejected > 0 then
    -- A row the addon could not read is said out loud: silently showing fewer
    -- rows than the bridge published is how a board starts lying.
    trailer[#trailer + 1] = ns.Lf("%d row(s) unreadable", rejected)
  end
  local offline = self:OfflineHosts()
  if #offline > 0 then
    trailer[#trailer + 1] = L["host offline"] .. ": " .. table.concat(offline, ", ")
  end
  fitText(panel.hosts, table.concat(trailer, "  "), FOOTER_TRAILER_WIDTH)

  local notice = self:PanelStatusLine()
  -- Measured rather than summed from two caps that are meant to fit: the trailer
  -- is fitted first and the legend gets exactly what it left, so the two ends
  -- cannot overlap however long a notice or a host list gets.
  local trailerWidth = panel.hosts:GetStringWidth()
  fitText(panel.legend, notice or self:LegendText(),
    math.max(0, FOOTER_WIDTH - trailerWidth - FOOTER_GAP))

  local activeKey = self:Tab().key
  for index, tab in ipairs(ns.TABS) do
    local button = panel.tabs[index]
    if button then
      local count = self:TabCount(tab)
      local label = count > 0 and (tab.label .. " " .. count) or tab.label
      if button.text ~= label then
        setButtonText(button, label)
        fitText(button.label, label, 78)
      end
      setButtonActive(button, tab.key == activeKey)
    end
  end

  local collapsed = self.collapsed == true

  -- The search box and the tab row are anchored below a 56px bar when collapsed,
  -- so they have to go with the rows they filter.
  panel.searchField:SetShown(not collapsed)
  panel.search:SetShown(not collapsed)
  panel.placeholder:SetShown(not collapsed and (self.search or "") == "")
  for _, button in ipairs(panel.tabs) do
    button:SetShown(not collapsed)
  end
  -- The footer goes with them: a 56px bar was drawing a second text row under the
  -- title, and "n-m of total, scroll" is a lie while scrolling is disabled.
  panel.legend:SetShown(not collapsed)
  panel.hosts:SetShown(not collapsed)

  -- Nothing to show says which nothing it is.
  if #sessions == 0 then
    local message = (self.search or "") ~= ""
      and ns.Lf("nothing matches \"%s\"", self.search)
      or L["no agents yet"]
    if panel.emptyText ~= message then
      panel.emptyText = message
      fitText(panel.empty, message, PANEL_WIDTH - 80)
    end
  end
  panel.empty:SetShown(not collapsed and #sessions == 0 and not self:IsMissing())
  for _, line in ipairs(panel.firstRun.lines) do
    fitText(line, line.fullText, PANEL_WIDTH - 80)
  end
  panel.firstRun:SetShown(not collapsed and self:IsMissing())

  for index, row in ipairs(panel.rows) do
    -- Explicit, not `collapsed and nil or ...`: that idiom returns the session
    -- when collapsed is true, which is exactly what it looks like it prevents.
    local session
    if not collapsed then
      session = sessions[self.offset + index]
    end
    row.session = session

    if session then
      local offline = self:IsOffline(session)
      local r, g, b = self:StatusColor(session.status)
      local dotKey = session.status .. (offline and "-off" or "")
      if row.dotKey ~= dotKey then
        row.dotKey = dotKey
        row.dot:SetColorTexture(r, g, b, offline and 0.45 or 1)
      end

      local title = session.title or ""
      if row.titleText ~= title or row.titleOffline ~= offline then
        row.titleText = title
        row.titleOffline = offline
        fitText(row.title, title, ROW_TEXT_WIDTH)
        if offline then
          row.title:SetTextColor(DIM[1], DIM[2], DIM[3])
        else
          row.title:SetTextColor(TEXT[1], TEXT[2], TEXT[3])
        end
      end

      local segments = self:TrailSegments(session, offline)
      local status = segments[1]
      if row.statusText ~= status then
        row.statusText = status
        row.status:SetText(status)
        row.status:SetTextColor(r, g, b)
      end

      -- What the status word leaves is what the trail gets, and both the word and
      -- the trail can change independently, so the fit is keyed on both.
      local statusWidth = row.status:GetStringWidth()
      local meta = table.concat(segments, SEP, 2)
      if row.metaText ~= meta or row.metaWidth ~= statusWidth then
        row.metaText = meta
        row.metaWidth = statusWidth
        local room = math.max(0, ROW_TEXT_WIDTH - statusWidth - 5)
        row.meta:SetWidth(room)
        fitText(row.meta, meta, room)
      end

      local age = self:ShortAge(session.age)
      if row.ageText ~= age then
        row.ageText = age
        row.age:SetText(age)
      end

      if index % 2 == 0 then
        row.even:Show()
        row.odd:Hide()
      else
        row.even:Hide()
        row.odd:Show()
      end
      row:Show()
    else
      row.session = nil
      row:Hide()
    end
  end

  -- Row striping and the hover wash follow the theme, so a skin change is one
  -- call rather than a full rebuild.
  for index, row in ipairs(panel.rows) do
    local odd = index % 2 == 1
    local color = odd and palette.row or palette.rowAlt
    local texture = odd and row.odd or row.even
    if texture.theme ~= palette.name then
      texture:SetColorTexture(color[1], color[2], color[3], color[4] or 1)
      texture.theme = palette.name
    end
    if row.highlightTheme ~= palette.name then
      row.highlight:SetColorTexture(unpack(palette.highlight))
      row.highlightTheme = palette.name
    end
  end
end

--- The row's second line in pieces: the status word first, then where it lives,
--- then what it is doing.
---
--- In pieces because the pieces are drawn differently - the status word in its
--- status colour, the trail muted. Painting the whole line in the status colour
--- put a full-width shout on every row, and the dot and the legend already carry
--- which status it is.
function ns:TrailSegments(session, offline)
  local segments = { session.label or self:StatusLabel(session.status) }

  if session.provider_label and session.provider_label ~= "" then
    segments[#segments + 1] = session.provider_label
  end
  local trail = (session.host and session.host ~= "local") and session.host or session.profile
  if trail and trail ~= "" then
    segments[#segments + 1] = trail
  end
  if session.project and session.project ~= "" then
    segments[#segments + 1] = session.project
  end

  if offline == nil then
    offline = self:IsOffline(session)
  end
  if offline then
    segments[#segments + 1] = L["host offline"]
  elseif session.activity and session.activity ~= "" then
    segments[#segments + 1] = session.activity
  end

  return segments
end

--- The same line as one string, for the tooltip and for anything that wants the
--- whole trail at once.
function ns:SessionTrail(session, offline)
  return table.concat(self:TrailSegments(session, offline), SEP)
end

function ns:ShowRowTooltip(row)
  local session = row.session
  if not session or not GameTooltip then
    return
  end

  GameTooltip:SetOwner(row, "ANCHOR_RIGHT")
  GameTooltip:AddLine(shorten(session.title, 90), TEXT[1], TEXT[2], TEXT[3], true)
  GameTooltip:AddLine(session.label or self:StatusLabel(session.status), unpack(ns.STATUS_COLORS[session.status] or DIM))

  local where = self:WhereText(session, " / ")
  if where ~= "" then
    GameTooltip:AddLine(where, 0.7, 0.7, 0.7)
  end

  GameTooltip:AddLine(ns.Lf("last activity %s", self:AgeLabel(session.age)), 0.6, 0.6, 0.6)
  if session.id then
    GameTooltip:AddLine(session.id, 0.5, 0.5, 0.5)
  end
  if session.preview and session.preview ~= "" then
    GameTooltip:AddLine(shorten(session.preview, 140), 0.8, 0.8, 0.8, true)
  end
  GameTooltip:AddLine(L["click for detail, or /agents reply"], 0.5, 0.5, 0.5)
  GameTooltip:Show()
end

--- Where a session lives, as one string: machine, profile, project.
function ns:WhereText(session, separator)
  local where = {}
  if session.provider_label and session.provider_label ~= "" then
    where[#where + 1] = session.provider_label
  end
  where[#where + 1] = (session.host and session.host ~= "local") and session.host or L["this machine"]
  if session.profile and session.profile ~= "" then
    where[#where + 1] = session.profile
  end
  if session.project and session.project ~= "" then
    where[#where + 1] = session.project
  end
  return table.concat(where, separator or " / ")
end

-- ------------------------------------------------------------- detail pane --

function ns:BuildDetail()
  if self.Detail then
    return self.Detail
  end

  local panel = self:BuildPanel()
  local detail = backdropFrame("Frame", "AgentBoardDetail", panel, "BackdropTemplate")
  detail:SetPoint("TOPLEFT", 10, ROW_TOP)
  detail:SetPoint("BOTTOMRIGHT", -10, 28)
  detail:EnableMouse(true)
  -- The rows are re-shown underneath on every refresh; the pane the player just
  -- opened has to stay above them, or both are read at once.
  detail:SetFrameLevel(panel:GetFrameLevel() + 2)
  applyBackdrop(detail, 0.97)

  detail.title = colorize(fontString(detail, "OVERLAY", FONT_TITLE), TEXT)
  detail.title:SetPoint("TOPLEFT", 12, -10)
  detail.title:SetWidth(DETAIL_TEXT_WIDTH)
  detail.title:SetJustifyH("LEFT")
  detail.title:SetWordWrap(false)

  detail.meta = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
  detail.meta:SetPoint("TOPLEFT", detail.title, "BOTTOMLEFT", 0, -2)
  detail.meta:SetWidth(DETAIL_TEXT_WIDTH)
  detail.meta:SetJustifyH("LEFT")
  detail.meta:SetWordWrap(false)

  detail.status = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
  detail.status:SetPoint("TOPLEFT", detail.meta, "BOTTOMLEFT", 0, -8)
  detail.status:SetJustifyH("LEFT")
  detail.status:SetWordWrap(false)

  -- The rest of that line, hung off the word's right edge rather than the end of a
  -- fixed box, so the two read as one line whatever the status is called. The word
  -- carries the status colour and this does not, as on the row that opened it.
  detail.statusTrail = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
  detail.statusTrail:SetPoint("TOPLEFT", detail.status, "TOPRIGHT", 8, 0)
  detail.statusTrail:SetJustifyH("LEFT")
  detail.statusTrail:SetWordWrap(false)

  detail.preview = colorize(fontString(detail, "OVERLAY", FONT_BODY), TEXT)
  detail.preview:SetPoint("TOPLEFT", detail.status, "BOTTOMLEFT", 0, -10)
  detail.preview:SetWidth(DETAIL_TEXT_WIDTH)
  -- Long output must not move the facts through the action row below it.
  detail.preview:SetHeight(56)
  detail.preview:SetMaxLines(4)
  detail.preview:SetJustifyH("LEFT")

  detail.stats = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
  detail.stats:SetPoint("TOPLEFT", detail.preview, "BOTTOMLEFT", 0, -12)
  detail.stats:SetWidth(DETAIL_TEXT_WIDTH)
  detail.stats:SetJustifyH("LEFT")
  detail.stats:SetWordWrap(false)

  detail.target = colorize(fontString(detail, "OVERLAY", FONT_SMALL), DIM)
  detail.target:SetPoint("TOPLEFT", detail.stats, "BOTTOMLEFT", 0, -6)
  detail.target:SetWidth(DETAIL_TEXT_WIDTH)
  detail.target:SetJustifyH("LEFT")
  detail.target:SetWordWrap(false)

  -- The pane is taller than one preview line needs. Rather than leave a hole
  -- above the composer, the facts a player would otherwise have to guess at fill
  -- it: two columns of key/value pairs, built once and rewritten on open.
  detail.facts = {}
  local COLUMN_WIDTH = math.floor(DETAIL_TEXT_WIDTH / 2)
  for index = 1, 6 do
    local column = (index - 1) % 2
    local row = math.floor((index - 1) / 2)

    local key = colorize(fontString(detail, "OVERLAY", FONT_SMALL), DIM)
    key:SetPoint("TOPLEFT", detail.target, "BOTTOMLEFT", column * COLUMN_WIDTH, -12 - row * 15)
    key:SetWidth(64)
    key:SetJustifyH("LEFT")
    key:SetWordWrap(false)

    local value = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
    value:SetPoint("LEFT", key, "RIGHT", 4, 0)
    value:SetWidth(COLUMN_WIDTH - 72)
    value:SetJustifyH("LEFT")
    value:SetWordWrap(false)

    detail.facts[index] = { key = key, value = value }
  end

  local close = makeButton(detail, 22, 18, "X", function()
    ns:CloseDetail()
  end, L["Close this session"])
  close:SetPoint("TOPRIGHT", -8, -8)
  detail.close = close

  local field = backdropFrame("Frame", nil, detail, "BackdropTemplate")
  field:SetPoint("BOTTOMLEFT", DETAIL_INSET, 44)
  field:SetPoint("RIGHT", detail, "RIGHT", -COMPOSER_RIGHT_INSET, 0)
  field:SetHeight(22)
  applyFieldBackdrop(field, theme().field, theme().buttonEdge)
  detail.composerField = field

  local composer = CreateFrame("EditBox", "AgentBoardComposer", detail, "InputBoxTemplate")
  composer:SetHeight(22)
  composer:SetPoint("BOTTOMLEFT", DETAIL_INSET, 44)
  composer:SetPoint("RIGHT", detail, "RIGHT", -COMPOSER_RIGHT_INSET, 0)
  composer:SetAutoFocus(false)
  composer:SetTextInsets(8, 6, 0, 0)
  composer:SetScript("OnEscapePressed", function(self)
    self:ClearFocus()
  end)
  composer:SetScript("OnEnterPressed", function(self)
    ns:SendComposer()
    self:ClearFocus()
  end)
  detail.composer = composer

  -- 22 tall like the composer they sit beside: at 20 their tops were 2px below the
  -- box's top, which is the kind of thing that reads as sloppy without being
  -- nameable.
  local send = makeButton(detail, SEND_WIDTH, 22, L["Send"], function()
    ns:SendComposer()
  end)
  send:SetPoint("BOTTOMRIGHT", -DETAIL_INSET, 44)
  detail.send = send

  local focus = makeButton(detail, HANDOFF_WIDTH, 22, L["Open live"], function()
    ns:FocusSelected()
  end, L["Open the live gaming overlay"])
  focus:SetPoint("RIGHT", send, "LEFT", -ACTION_GAP, 0)
  detail.focus = focus

  detail.markRead = makeButton(detail, 90, 22, L["Mark read"], function()
    if ns:QueueMarkRead(ns.selected) then
      ns:Print(L["mark read queued; press Sync to apply it."])
      ns:RefreshBadge()
    end
  end, L["Dismiss this activity after the next sync. New activity appears again."])
  detail.markRead:SetPoint("BOTTOMLEFT", 12, 78)
  detail.stop = makeButton(detail, 90, 22, L["Stop turn"], function()
    if ns:QueueStop(ns.selected) then
      ns:Print(L["stop queued; press Sync. It stops the turn active on delivery and clears queued prompts and approvals."])
      ns:RefreshBadge()
    end
  end, L["Queued until Sync. Stops the turn active on delivery, including a later turn. Clears queued prompts and approvals."])
  detail.stop:SetPoint("LEFT", detail.markRead, "RIGHT", 8, 0)

  detail.approve = makeButton(detail, 74, 22, L["Approve"], function()
    if ns:QueueApproval(ns.selected, "approve") then
      ns:Print(L["approval queued; press Sync to apply it."])
      if AgentBoardDB.sendOnSync then ns:Sync("approve") end
    end
  end, L["Approve the pending request after the next sync."])
  detail.approve:SetPoint("BOTTOMLEFT", 12, 104)
  detail.decline = makeButton(detail, 74, 22, L["Decline"], function()
    if ns:QueueApproval(ns.selected, "decline") then
      ns:Print(L["decline queued; press Sync to apply it."])
      if AgentBoardDB.sendOnSync then ns:Sync("decline") end
    end
  end, L["Decline the pending request after the next sync."])
  detail.decline:SetPoint("LEFT", detail.approve, "RIGHT", 8, 0)

  local hint = colorize(fontString(detail, "OVERLAY", FONT_SMALL), DIM)
  hint:SetPoint("BOTTOMLEFT", 12, 22)
  fitText(hint, L["Enter sends and syncs. Open live starts the gaming overlay without leaving T3."], DETAIL_TEXT_WIDTH)
  detail.hint = hint

  detail:Hide()
  self.Detail = detail
  return detail
end

--- Open the detail pane. `takeFocus` is explicit because grabbing the keyboard
--- puts movement keys into the edit box until the player presses Escape.
function ns:OpenDetail(session, takeFocus)
  if not session then
    return
  end

  self.selected = session
  if self.New then self.New:Hide() end
  local detail = self:BuildDetail()

  fitText(detail.title, session.title or "", DETAIL_TEXT_WIDTH)
  -- Fitted like every other line in the pane. Unfitted, a long host or project
  -- ran to the pane edge and was clipped mid-word with no ellipsis, which reads as
  -- data loss rather than as a name that did not fit.
  fitText(detail.meta, self:WhereText(session), DETAIL_TEXT_WIDTH)

  -- Messages and cost are in the facts block below; this line is what the agent
  -- is doing right now, which is the one thing not stated anywhere else.
  fitText(detail.stats,
    (session.activity and session.activity ~= "") and session.activity or L["not reporting an activity"], DETAIL_TEXT_WIDTH)

  local facts = {
    { L["session"], session.id or "" },
    { L["machine"], (session.host and session.host ~= "local") and session.host or L["this machine"] },
    { L["project"], (session.project ~= "" and session.project) or "-" },
    { L["profile"], (session.profile ~= "" and session.profile) or "-" },
    { L["messages"], tostring(session.messages or 0) },
    { L["cost"], string.format("$%.2f", session.cost or 0) },
  }
  for index, entry in ipairs(detail.facts) do
    local fact = facts[index]
    entry.key:SetText(fact[1] .. ":")
    fitText(entry.value, fact[2], math.floor(DETAIL_TEXT_WIDTH / 2) - 72)
  end

  -- Say where the reply goes before the player types it, not after.
  fitText(detail.target,
    ns.Lf("the reply goes to %s",
      (session.host and session.host ~= "local") and session.host or L["this machine"]),
    DETAIL_TEXT_WIDTH)
  if session.user_input_request_id ~= "" then
    fitText(detail.target, L["this answer goes back to the waiting agent"], DETAIL_TEXT_WIDTH)
  end

  detail.status:SetText(session.label or self:StatusLabel(session.status))
  detail.status:SetTextColor(unpack(ns.STATUS_COLORS[session.status] or DIM))

  -- The age is measured against the word on the left, so a long status label
  -- cannot run the id off the pane.
  fitText(detail.statusTrail,
    table.concat({ ns.Lf("last activity %s", self:AgeLabel(session.age)), session.id or "" }, SEP),
    math.max(0, DETAIL_TEXT_WIDTH - detail.status:GetStringWidth() - 8))

  local body
  if session.user_input_request_id ~= "" then
    body = session.user_input_summary
    if session.user_input_options and session.user_input_options ~= "" then
      body = body .. "  [" .. session.user_input_options .. "]"
    end
  elseif session.approval_request_id ~= "" then
    body = session.approval_summary
  else
    body = session.preview
  end
  if not body or body == "" then
    body = L["no output in the last turn"]
  end
  detail.preview:SetText(shorten(body, 900))

  fitText(detail.markRead.label, L["Mark read"], detail.markRead:GetWidth() - 12)
  fitText(detail.stop.label, L["Stop turn"], detail.stop:GetWidth() - 12)
  detail.markRead:SetShown(tonumber(session.activity_at) ~= nil)
  detail.stop:SetShown(session.status == "working" and (session.host or "local") == "local")
  local approval = session.approval_request_id ~= nil and session.approval_request_id ~= ""
  detail.approve:SetShown(approval)
  detail.decline:SetShown(approval)
  detail.composer:SetText("")
  detail:Show()

  if takeFocus ~= false then
    detail.composer:SetFocus()
  end
end

function ns:CloseDetail()
  if self.Detail then
    self.Detail:Hide()
  end
  self.selected = nil
end

function ns:FocusSelected()
  local session = self.selected
  if not session then
    return
  end

  if self:QueueFocus(session) then
    self:Print(ns.Lf("queued a live-overlay request for %s; it goes out with the next sync.", session.id))
    if AgentBoardDB.sendOnSync then
      self:Sync("focus")
    end
  end
end

-- ---------------------------------------------------------------- new agent --

function ns:SelectedNewProject()
  local projects = self:Projects()
  if #projects == 0 then
    return nil, 0
  end
  local index = math.floor(tonumber(AgentBoardDB.newProject) or 1)
  index = math.max(1, math.min(#projects, index))
  AgentBoardDB.newProject = index
  return projects[index], index
end

function ns:RefreshNew()
  local frame = self.New
  if not frame then
    return
  end
  local project, index = self:SelectedNewProject()
  if project then
    setButtonText(frame.project, ns.Lf("Project: %s", project.title))
    fitText(frame.project.label, frame.project.text, frame.project:GetWidth() - 12)
    frame.projectHint:SetText(ns.Lf("%d of %d. Left click next; right click previous.", index, #self:Projects()))
    frame.start:SetAlpha(1)
  else
    setButtonText(frame.project, L["Project: none reported"])
    frame.projectHint:SetText(L["The bridge must publish at least one T3 project."])
    frame.start:SetAlpha(0.45)
  end
end

function ns:BuildNew()
  if self.New then
    return self.New
  end
  local panel = self:BuildPanel()
  local frame = backdropFrame("Frame", "AgentBoardNew", panel, "BackdropTemplate")
  frame:SetPoint("TOPLEFT", 10, ROW_TOP)
  frame:SetPoint("BOTTOMRIGHT", -10, 28)
  frame:EnableMouse(true)
  frame:SetFrameLevel(panel:GetFrameLevel() + 2)
  applyBackdrop(frame, 0.98)

  local title = colorize(fontString(frame, "OVERLAY", FONT_TITLE), TEXT)
  title:SetPoint("TOPLEFT", 12, -10)
  title:SetText(L["New agent"])

  local note = colorize(fontString(frame, "OVERLAY", FONT_SMALL), DIM)
  note:SetPoint("TOPLEFT", title, "BOTTOMLEFT", 0, -6)
  note:SetPoint("RIGHT", frame, "RIGHT", -12, 0)
  note:SetJustifyH("LEFT")
  note:SetText(L["Choose a project, describe the task, then Start & Sync. The reload sends the request and the next sync shows the new thread."])

  local project = makeButton(frame, 300, 22, L["Project: loading"], function() end)
  project:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  project:SetScript("OnClick", function(_, mouseButton)
    local projects = ns:Projects()
    if #projects == 0 then return end
    local _, index = ns:SelectedNewProject()
    local delta = mouseButton == "RightButton" and -1 or 1
    AgentBoardDB.newProject = ((index - 1 + delta) % #projects) + 1
    ns:RefreshNew()
  end)
  project:SetPoint("TOPLEFT", 12, -78)
  frame.project = project

  local projectHint = colorize(fontString(frame, "OVERLAY", FONT_SMALL), DIM)
  projectHint:SetPoint("TOPLEFT", project, "BOTTOMLEFT", 0, -5)
  projectHint:SetWidth(360)
  projectHint:SetJustifyH("LEFT")
  frame.projectHint = projectHint

  local field = backdropFrame("Frame", nil, frame, "BackdropTemplate")
  field:SetPoint("TOPLEFT", 12, -118)
  field:SetPoint("RIGHT", frame, "RIGHT", -12, 0)
  field:SetHeight(108)
  applyFieldBackdrop(field, theme().field, theme().buttonEdge)

  local prompt = CreateFrame("EditBox", "AgentBoardNewPrompt", field, "InputBoxTemplate")
  prompt:SetPoint("TOPLEFT", field, "TOPLEFT", 8, -7)
  prompt:SetPoint("BOTTOMRIGHT", field, "BOTTOMRIGHT", -8, 7)
  prompt:SetAutoFocus(false)
  prompt:SetMultiLine(true)
  prompt:SetMaxLetters(4000)
  prompt:SetTextInsets(0, 0, 0, 0)
  prompt:SetScript("OnEscapePressed", function(self)
    self:ClearFocus()
  end)
  frame.prompt = prompt

  local start = makeButton(frame, 118, 22, L["Start & Sync"], function()
    ns:StartNew()
  end, L["Queue the new session and reload the UI"])
  start:SetPoint("BOTTOMRIGHT", -12, 14)
  frame.start = start

  local cancel = makeButton(frame, 70, 22, L["Cancel"], function()
    frame:Hide()
  end)
  cancel:SetPoint("BOTTOMLEFT", 12, 14)
  frame.cancel = cancel

  local close = makeButton(frame, 22, 18, "X", function()
    frame:Hide()
  end, L["Close new agent"])
  close:SetPoint("TOPRIGHT", -8, -8)
  frame.close = close

  frame:Hide()
  self.New = frame
  return frame
end

function ns:ToggleNew()
  local frame = self:BuildNew()
  if frame:IsShown() then
    frame:Hide()
    return
  end
  self:CloseDetail()
  if self.Settings then self.Settings:Hide() end
  self:RefreshNew()
  frame:Show()
  frame.prompt:SetFocus()
end

function ns:StartNew()
  local frame = self.New
  local project = self:SelectedNewProject()
  local text = frame.prompt:GetText()
  if not project then
    self:Print(L["no T3 project is available; run Sync after the bridge connects."])
    return
  end
  if not self:QueueNew(project.id, text) then
    self:Print(L["write a prompt before starting the agent."])
    return
  end
  frame.prompt:SetText("")
  frame:Hide()
  self:Print(ns.Lf("queued a new agent in %s.", project.title))
  if AgentBoardDB.sendOnSync then
    self:Sync("new")
  else
    self:Print(L["press Sync (or /agents sync) to start it."])
  end
end

-- --------------------------------------------------------------- settings --

function ns:SettingsOptions()
  return {
    { key = "badge", label = L["Show the badge"], kind = "bool" },
    { key = "opportunistic", label = L["Refresh on loading screens"], kind = "bool" },
    { key = "toasts", label = L["Sync toasts"], kind = "bool" },
    { key = "sound", label = L["Sound when something needs you"], kind = "bool" },
    { key = "minimap", label = L["Minimap button"], kind = "bool" },
    { key = "sendOnSync", label = L["Sync when a reply is sent"], kind = "bool" },
    { key = "theme", label = L["Skin"], kind = "theme" },
  }
end

function ns:BuildSettings()
  if self.Settings then
    return self.Settings
  end

  local panel = self:BuildPanel()
  local settings = backdropFrame("Frame", "AgentBoardSettings", panel, "BackdropTemplate")
  settings:SetPoint("TOPLEFT", 10, ROW_TOP)
  settings:SetPoint("BOTTOMRIGHT", -10, 28)
  settings:EnableMouse(true)
  settings:SetFrameLevel(panel:GetFrameLevel() + 2)
  applyBackdrop(settings, 0.98)

  local title = colorize(fontString(settings, "OVERLAY", FONT_TITLE), TEXT)
  title:SetPoint("TOPLEFT", 12, -10)
  title:SetText(L["Settings"])

  local note = colorize(fontString(settings, "OVERLAY", FONT_SMALL), DIM)
  note:SetPoint("TOPLEFT", title, "BOTTOMLEFT", 0, -6)
  note:SetPoint("RIGHT", settings, "RIGHT", -12, 0)
  note:SetJustifyH("LEFT")
  note:SetText(L["Everything here is stored per account and applies on the next reload."])
  settings.note = note

  settings.lines = {}
  local previous = note
  for index, option in ipairs(self:SettingsOptions()) do
    local line = makeButton(settings, 320, 20, option.label, function()
      ns:ToggleSetting(option)
    end)
    line:SetPoint("TOPLEFT", previous, "BOTTOMLEFT", 0, index == 1 and -16 or -6)
    line.option = option
    settings.lines[index] = line
    previous = line
  end

  local policy = colorize(fontString(settings, "OVERLAY", FONT_SMALL), DIM)
  policy:SetPoint("TOPLEFT", previous, "BOTTOMLEFT", 0, -16)
  policy:SetPoint("RIGHT", settings, "RIGHT", -12, 16)
  policy:SetJustifyH("LEFT")
  policy:SetText("")
  settings.policy = policy

  local sync = makeButton(settings, 200, 20, L["Sync now (reloads the UI)"], function()
    ns:Sync("settings")
  end)
  sync:SetPoint("BOTTOMLEFT", 12, 34)
  settings.sync = sync

  local reset = makeButton(settings, 170, 20, L["Reset positions"], function()
    AgentBoardDB.panelPoint = nil
    AgentBoardDB.point = nil
    AgentBoardDB.minimapAngle = 250
    ns:PositionMinimapButton()
    local board = ns.Board
    board:ClearAllPoints()
    board:SetPoint(ns.DEFAULT_PANEL_POINT[1], UIParent, ns.DEFAULT_PANEL_POINT[1],
      ns.DEFAULT_PANEL_POINT[2], ns.DEFAULT_PANEL_POINT[3])
    ns:PlaceBadge()
    ns:Print(L["panel and badge positions reset."])
  end)
  reset:SetPoint("BOTTOMRIGHT", -12, 34)
  settings.reset = reset

  local close = makeButton(settings, 22, 18, "X", function()
    ns:ToggleSettings()
  end, L["Close settings"])
  close:SetPoint("TOPRIGHT", -8, -8)
  settings.close = close

  settings:Hide()
  self.Settings = settings
  return settings
end

function ns:RefreshSettings()
  local settings = self:BuildSettings()

  for _, line in ipairs(settings.lines) do
    local option = line.option
    local label
    if option.kind == "bool" then
      label = option.label .. ": " .. ((AgentBoardDB[option.key] ~= false) and L["on"] or L["off"])
    else
      label = option.label .. ": " .. tostring(AgentBoardDB[option.key] or "dark")
    end
    setButtonText(line, label)
    fitText(line.label, label, 300)
  end

  local policy = self:SyncPolicy()
  settings.policy:SetText(ns.Lf(
    "A sync reloads the UI, so it is spent carefully: at most once every %d minutes, only when the snapshot is "
      .. "older than %d minutes, never in combat, never inside an instance.",
    math.floor(policy.minInterval / 60), math.floor(policy.staleAfter / 60)))
end

function ns:ToggleSetting(option)
  if option.kind == "bool" then
    AgentBoardDB[option.key] = not (AgentBoardDB[option.key] ~= false)
  elseif option.kind == "theme" then
    AgentBoardDB.theme = (AgentBoardDB.theme == "dark") and "classic" or "dark"
    self:RefreshSkin()
  end

  if option.key == "minimap" then
    self:RefreshMinimapButton()
  end

  self:RefreshSettings()
  self:RefreshBadge()
  if self.Board:IsShown() then
    self:RefreshPanel()
  end
end

--- Re-apply the palette everywhere it is used.
---
--- The theme touches textures that are set once at build time (rows, buttons,
--- fields, the minimap button), so switching skins has to walk them; missing one
--- leaves a panel that is half one skin and half the other.
function ns:RefreshSkin()
  applyBackdrop(self.Board)
  applyBackdrop(self.Toast)
  applyBackdrop(self.Badge)
  if self.Detail then
    applyBackdrop(self.Detail, 0.97)
    applyFieldBackdrop(self.Detail.composerField, theme().field, theme().buttonEdge)
  end
  if self.Settings then
    applyBackdrop(self.Settings, 0.98)
  end
  applyFieldBackdrop(self.Board.searchField, theme().field, theme().buttonEdge)

  for _, button in ipairs({ self.Board.close, self.Board.minimise, self.Board.settingsButton, self.Board.sync, self.Board.new }) do
    ns:SkinButton(button)
  end
  for _, button in ipairs(self.Board.tabs) do
    ns:SkinButton(button)
  end
  -- The panes were left out of this walk, and the settings pane is on screen at
  -- the moment the skin changes: its own buttons stayed in the old palette until
  -- something happened to hover them.
  if self.Detail then
    for _, button in ipairs({ self.Detail.close, self.Detail.send, self.Detail.focus, self.Detail.markRead, self.Detail.stop, self.Detail.approve, self.Detail.decline }) do
      ns:SkinButton(button)
    end
  end
  if self.Settings then
    for _, line in ipairs(self.Settings.lines) do
      ns:SkinButton(line)
    end
    for _, button in ipairs({ self.Settings.close, self.Settings.sync, self.Settings.reset }) do
      ns:SkinButton(button)
    end
  end
  if self.New then
    for _, button in ipairs({ self.New.close, self.New.project, self.New.start, self.New.cancel }) do
      ns:SkinButton(button)
    end
    applyFieldBackdrop(self.New.prompt:GetParent(), theme().field, theme().buttonEdge)
  end

  self:SkinMinimapButton()

  -- Re-painted last because `applyBackdrop` above gives the badge the theme's
  -- plain edge colour, and the badge's border is a status channel (error, needs,
  -- idle gold). The settings path refreshed the badge after the walk; the slash
  -- command did not, so `/agents theme` left a grey border until the next poll.
  self:RefreshBadge()

  -- Textures remember the palette they were painted with; clearing the key makes
  -- the next refresh repaint them.
  for _, row in ipairs(self.Board.rows) do
    row.odd.theme = nil
    row.even.theme = nil
    row.highlightTheme = nil
  end
  self:RefreshPanel()
end

function ns:ToggleSettings()
  local settings = self:BuildSettings()
  if settings:IsShown() then
    settings:Hide()
  else
    self:RefreshSettings()
    self:CloseDetail()
    settings:Show()
  end
end

function ns:ToggleCollapsed()
  local panel = self:BuildPanel()
  self.collapsed = not self.collapsed

  if self.collapsed then
    self.expandedHeight = math.max(PANEL_HEIGHT, panel:GetHeight() or PANEL_HEIGHT)
    panel:SetHeight(56)
    self:CloseDetail()
    if self.Settings then
      self.Settings:Hide()
    end
  else
    panel:SetHeight(self.expandedHeight or PANEL_HEIGHT)
  end

  self:RefreshPanel()
end

-- ------------------------------------------------------------------ scroll --

function ns:ScrollRows(delta)
  -- The detail and settings panes cover the list: the wheel must not scroll
  -- something the player cannot see.
  if self.Detail and self.Detail:IsShown() then
    return
  end
  if self.Settings and self.Settings:IsShown() then
    return
  end
  if self.collapsed then
    return
  end

  local sessions = self:Filtered()
  local maxOffset = math.max(0, #sessions - ROW_COUNT)
  if maxOffset == 0 then
    return
  end

  self.offset = math.min(maxOffset, math.max(0, (self.offset or 0) + (delta < 0 and 1 or -1)))
  self:RefreshPanel()
end

--- Page the list, for the keybind and for a player without a wheel.
function ns:ScrollTo(offset)
  if self.collapsed then
    return
  end

  local sessions = self:Filtered()
  self.offset = math.max(0, math.min(math.max(0, #sessions - ROW_COUNT), offset or 0))
  self:RefreshPanel()
end

-- ---------------------------------------------------------------- composer --

function ns:SendComposer()
  local detail = self.Detail
  if not detail then
    return
  end

  local session = self.selected
  if not session then
    self:Print(L["pick a session first (click a row)."])
    return
  end

  local text = detail.composer:GetText()
  local queued
  if session.user_input_request_id ~= "" then
    queued = self:QueueAnswer(session, text)
  else
    queued = self:QueueReply(session.id, text, session.host, session.provider)
  end
  if not queued then
    self:Print(L["nothing to send."])
    return
  end

  detail.composer:SetText("")
  if session.user_input_request_id ~= "" then
    self:Print(ns.Lf("queued answer for %s.", session.id))
  else
    self:Print(ns.Lf("queued reply for %s.", session.id))
  end

  if AgentBoardDB.sendOnSync then
    self:Sync("reply")
  else
    self:Print(L["press Sync (or /agents sync) to send it."])
  end
end

-- ------------------------------------------------------------------ frames --

function ns:ShowBoard()
  local panel = self:BuildPanel()
  self.offset = self.offset or 0
  self.expandedHeight = PANEL_HEIGHT
  if self.collapsed then
    panel:SetHeight(56)
  end
  self:RefreshPanel()
  panel:Show()
end

function ns:HideBoard()
  if self.Board then
    self.Board:Hide()
  end
end

function ns:ToggleBoard()
  if not self.Board then
    self:BuildUI()
  end

  if self.Board:IsShown() then
    self:HideBoard()
  else
    self:ShowBoard()
  end
end

function ns:ShowToast(session)
  if AgentBoardDB.toasts == false then return end
  local toast = self.Toast
  if not toast then
    toast = backdropFrame("Button", nil, UIParent, "BackdropTemplate")
    toast:SetSize(420, 36)
    toast:SetPoint("TOP", UIParent, "TOP", 0, -110)
    toast:SetFrameStrata("DIALOG")
    toast:EnableMouse(true)
    toast.text = fontString(toast, "OVERLAY", FONT_BODY)
    toast.text:SetPoint("LEFT", 12, 0)
    self.Toast = toast
  end
  toast.generation = (toast.generation or 0) + 1
  local generation = toast.generation
  applyBackdrop(toast)
  fitText(toast.text, self:StatusLabel(session.status) .. ": " .. (session.title or session.id), 396)
  toast.text:SetTextColor(self:StatusColor(session.status))
  toast:SetScript("OnUpdate", nil)
  toast:SetAlpha(1)
  toast:Show()
  toast:SetScript("OnClick", function()
    self:ShowBoard()
    self:OpenDetail(session, false)
    toast:SetScript("OnUpdate", nil)
    toast:Hide()
  end)
  C_Timer.After(6, function()
    if toast.generation ~= generation or not toast:IsShown() then return end
    local elapsed = 0
    toast:SetScript("OnUpdate", function(frame, delta)
      elapsed = elapsed + delta
      frame:SetAlpha(math.max(0, 1 - elapsed))
      if elapsed >= 1 then
        frame:SetScript("OnUpdate", nil)
        frame:Hide()
      end
    end)
  end)
end

function ns:BuildUI()
  self.offset = self.offset or 0
  self:BuildBadge()
  self:BuildPanel()
  self:BuildDetail()
  self:BuildSettings()
  self:BuildMinimapButton()
  self:RefreshBadge()
  self:RefreshMinimapButton()
  self:RefreshPanel()
  self:RefreshSettings()

  self:NotifyTransitions()
end

--- Drop everything cached about how text fits.
---
--- Fitting is measured, and the measurement is only valid for the font metrics
--- in force when it was taken. A player who changes their UI scale (or drags the
--- window to another monitor) would otherwise keep titles truncated to a width
--- that no longer exists.
function ns:InvalidateLayout()
  if not self.Board then
    return
  end

  for _, row in ipairs(self.Board.rows) do
    row.titleText, row.metaText, row.ageText, row.dotKey, row.titleOffline = nil, nil, nil, nil, nil
    row.statusText, row.metaWidth = nil, nil
    row.odd.theme, row.even.theme, row.highlightTheme = nil, nil, nil
  end
  for _, button in ipairs(self.Board.tabs) do
    button.text = nil
  end
  self.Board.attentionText = nil

  if self.Settings then
    for _, line in ipairs(self.Settings.lines) do
      line.text = nil
    end
  end

  if self.Board:IsShown() then
    self:RefreshPanel()
  end
  self:RefreshBadge()
end

function ns:RefreshAll()
  self:RefreshBadge()
  self:RefreshMinimapButton()
  if self.Board and self.Board:IsShown() then
    self:RefreshPanel()
  end
end

--- The refresh entry point Core calls after a tab or search change.
function ns:RefreshBoard()
  self:RefreshPanel()
end

-- ----------------------------------------------------------------- binding --

function ns:Toggle()
  self:ToggleBoard()
end

--- Keybinds: page the list, for a player without a wheel and for anyone who
--- would rather not spin one through forty sessions.
function ns:PageUp()
  self:ScrollTo((self.offset or 0) - ROW_COUNT)
end

function ns:PageDown()
  self:ScrollTo((self.offset or 0) + ROW_COUNT)
end

--- Keybind: open the detail pane for the selected row (or the first one).
function ns:ReplyToSelected()
  local sessions = self:Filtered()
  local session = self.selected or sessions[1]
  if session then
    self:ShowBoard()
    self:OpenDetail(session, true)
  end
end
