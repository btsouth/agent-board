-- Minimal WoW API stand-in for standalone checks (tests/roundtrip.py).
-- Only what Core.lua touches at load: enough to exercise the outbox and the
-- data adapter without a client. The fuller harness lives in tests/wow_stub.lua.

local function region()
  local item = { text = "", shown = true }
  for _, method in ipairs({
    "SetPoint", "SetAllPoints", "SetSize", "SetWidth", "SetHeight", "SetTextColor",
    "SetColorTexture", "SetTexture", "SetJustifyH", "SetWordWrap", "SetScript",
    "SetBackdrop", "SetBackdropColor", "SetBackdropBorderColor", "SetFrameStrata",
    "SetClampedToScreen", "SetMovable", "EnableMouse", "EnableMouseWheel", "RegisterForDrag",
    "RegisterEvent", "UnregisterEvent", "RegisterForClicks", "SetAutoFocus", "SetTextInsets",
    "SetFocus", "ClearFocus", "SetFrameLevel", "StartMoving", "StopMovingOrSizing", "SetShown",
  }) do
    item[method] = function() end
  end
  item.SetText = function(_, value)
    item.text = tostring(value or "")
  end
  item.GetText = function()
    return item.text
  end
  item.Show = function()
    item.shown = true
  end
  item.Hide = function()
    item.shown = false
  end
  item.IsShown = function()
    return item.shown
  end
  item.GetPoint = function()
    return "CENTER", UIParent, "CENTER", 0, 0
  end
  item.CreateTexture = function()
    return region()
  end
  item.CreateFontString = function()
    return region()
  end
  item.SetScript = function(self, handler, fn)
    self.scripts = self.scripts or {}
    self.scripts[handler] = fn
  end
  return item
end

UIParent = region()
DEFAULT_CHAT_FRAME = region()
DEFAULT_CHAT_FRAME.AddMessage = function() end

GameFontNormal, GameFontHighlight, GameFontHighlightSmall = {}, {}, {}
GameFontNormalSmall, GameFontDisableSmall, ChatFontNormal = {}, {}, {}

UIPanelButtonTemplate = "UIPanelButtonTemplate"
UIPanelCloseButton = "UIPanelCloseButton"
InputBoxTemplate = "InputBoxTemplate"

SlashCmdList = {}

function CreateFrame()
  return region()
end

function ReloadUI() end

function InCombatLockdown()
  return false
end

function UnitName()
  return "Tester"
end

function strtrim(value)
  return (tostring(value or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

function time()
  return 1789771234
end

local secureHooks = {}
function SetItemRef(link)
  for _, callback in ipairs(secureHooks) do callback(link) end
end
function hooksecurefunc(name, callback)
  assert(name == "SetItemRef" and type(callback) == "function")
  secureHooks[#secureHooks + 1] = callback
end
C_Timer = { pending = {} }
function C_Timer.After(delay, callback)
  assert(type(delay) == "number" and delay >= 0 and type(callback) == "function")
  C_Timer.pending[#C_Timer.pending + 1] = { delay = delay, callback = callback }
end
