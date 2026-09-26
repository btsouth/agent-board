-- Locale: one indirection between the code and the words a player reads.
--
-- Every user-visible string is looked up through `L`, and an entry that has no
-- translation is its own label, so English needs no file at all and a translator
-- only ships the strings they actually change. That is the standard addon
-- pattern, and it is what makes the addon translatable at all: a hard-coded
-- string in this codebase is a string nobody outside English can read.
--
-- Adding a language:
--   1. create `localization/xxYY.lua` in this folder
--   2. start it with `local _, ns = ...` and then, for each key you want to
--      change, `ns.L["Needs you"] = "Braucht dich"`
--   3. add the filename to AgentBoard.toc after Locale.lua, guarded the way every
--      localized addon does it, e.g.
--      `localization/xxYY.lua` only loaded when `GetLocale()` matches, which the
--      toc cannot express; ship it as its own optional folder instead, or use a
--      metatable-based lazy load
--
-- The keys are the English source strings on purpose: a diff that adds a
-- translation shows the string it translates, and a missing key degrades to
-- readable English instead of an empty label.

local addonName, ns = ...

local L = setmetatable({}, {
  __index = function(table, key)
    -- Cache the miss, so a lookup costs one hash after the first call.
    rawset(table, key, key)
    return key
  end,
})

ns.L = L

--- A translated string with values filled in: `ns.Lf("%d need you", 3)`.
--- Kept next to `L` because the two are always used together.
function ns.Lf(key, ...)
  local format = L[key]
  if select("#", ...) == 0 then
    return format
  end
  return string.format(format, ...)
end
