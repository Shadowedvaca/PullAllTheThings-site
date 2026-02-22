-- PATTSync: Guild Roster Exporter for Pull All The Things
-- Exports guild roster data including notes to SavedVariables
-- A companion app on the PC watches for changes and uploads to the API
--
-- Usage:
--   - Automatically exports when you open the guild roster (up to 4x per day)
--   - Type /pattsync to manually trigger an export
--   - Type /pattsync status to see last export time and cooldown
--   - Type /pattsync force to export regardless of cooldown
--

-- SavedVariables storage
PATTSyncDB = PATTSyncDB or {}

-- Constants
local ADDON_NAME = "PATTSync"
local ADDON_VERSION = "1.0.0"
local COOLDOWN_SECONDS = 6 * 60 * 60  -- 6 hours = 4x per day max
local MAX_RETRIES = 3
local RETRY_DELAY = 2  -- seconds between retries

-- State
local isExporting = false
local exportRetries = 0

-- Colors for chat output
local GOLD = "|cFFD4A84B"
local GREEN = "|cFF4ADE80"
local RED = "|cFFF87171"
local WHITE = "|cFFE8E8E8"
local RESET = "|r"

-- ============================================================
-- Utility Functions
-- ============================================================

local function Print(msg)
    DEFAULT_CHAT_FRAME:AddMessage(GOLD .. "[PATTSync]" .. RESET .. " " .. msg)
end

local function GetTimestamp()
    return time()
end

local function FormatTimeDiff(seconds)
    if seconds < 60 then
        return string.format("%d seconds", seconds)
    elseif seconds < 3600 then
        return string.format("%d minutes", math.floor(seconds / 60))
    else
        local hours = math.floor(seconds / 3600)
        local mins = math.floor((seconds % 3600) / 60)
        return string.format("%dh %dm", hours, mins)
    end
end

local function IsCooldownActive()
    local lastExport = PATTSyncDB.lastExportTime or 0
    local elapsed = GetTimestamp() - lastExport
    return elapsed < COOLDOWN_SECONDS, COOLDOWN_SECONDS - elapsed
end

-- ============================================================
-- Guild Roster Export
-- ============================================================

local function ExportGuildRoster(forcedExport)
    -- Check if we're in a guild
    if not IsInGuild() then
        Print(RED .. "You are not in a guild!" .. RESET)
        return
    end

    -- Check cooldown (unless forced)
    if not forcedExport then
        local onCooldown, remaining = IsCooldownActive()
        if onCooldown then
            Print(WHITE .. "Export on cooldown. Next export available in " ..
                  GREEN .. FormatTimeDiff(remaining) .. RESET)
            return
        end
    end

    -- Prevent concurrent exports
    if isExporting then
        Print(WHITE .. "Export already in progress..." .. RESET)
        return
    end

    isExporting = true
    exportRetries = 0

    -- Request guild roster data from the server
    -- This triggers GUILD_ROSTER_UPDATE when data is ready
    C_GuildInfo.GuildRoster()

    Print(WHITE .. "Requesting guild roster data..." .. RESET)
end

local function ProcessGuildRoster()
    local numMembers = GetNumGuildMembers()

    if numMembers == 0 then
        -- Data not ready yet, retry
        exportRetries = exportRetries + 1
        if exportRetries <= MAX_RETRIES then
            Print(WHITE .. "Waiting for guild data (attempt " ..
                  exportRetries .. "/" .. MAX_RETRIES .. ")..." .. RESET)
            C_Timer.After(RETRY_DELAY, function()
                C_GuildInfo.GuildRoster()
            end)
            return
        else
            Print(RED .. "Failed to get guild roster data after " ..
                  MAX_RETRIES .. " attempts." .. RESET)
            isExporting = false
            return
        end
    end

    -- Build the roster data
    local characters = {}
    local exportTime = GetTimestamp()

    for i = 1, numMembers do
        local name, rankName, rankIndex, level, classDisplayName,
              zone, note, officerNote, isOnline, status,
              classFileName, achievementPoints, achievementRank,
              isMobile, isSoREligible, standingID = GetGuildRosterInfo(i)

        if name then
            -- WoW returns names as "Name-Realm"
            local charName, realmName = strsplit("-", name)

            -- Get last online info
            local yearsOffline, monthsOffline, daysOffline, hoursOffline = GetGuildRosterLastOnline(i)
            local lastOnlineStr = ""
            if isOnline then
                lastOnlineStr = "Online"
            elseif yearsOffline then
                if yearsOffline > 0 then
                    lastOnlineStr = string.format("%dy %dm %dd", yearsOffline, monthsOffline, daysOffline)
                elseif monthsOffline > 0 then
                    lastOnlineStr = string.format("%dm %dd", monthsOffline, daysOffline)
                elseif daysOffline > 0 then
                    lastOnlineStr = string.format("%dd %dh", daysOffline, hoursOffline)
                else
                    lastOnlineStr = string.format("%dh", hoursOffline)
                end
            end

            table.insert(characters, {
                name = charName or name,
                realm = realmName or GetRealmName(),
                class = classDisplayName or "Unknown",
                classFile = classFileName or "UNKNOWN",
                level = level or 0,
                rank = rankIndex or 99,
                rankName = rankName or "Unknown",
                zone = zone or "",
                note = note or "",
                officerNote = officerNote or "",
                isOnline = isOnline or false,
                lastOnline = lastOnlineStr,
                achievementPoints = achievementPoints or 0,
            })
        end
    end

    -- Store in SavedVariables
    PATTSyncDB.lastExport = {
        exportTime = exportTime,
        exportTimeISO = date("!%Y-%m-%dT%H:%M:%SZ", exportTime),
        addonVersion = ADDON_VERSION,
        guildName = GetGuildInfo("player") or "Unknown",
        memberCount = #characters,
        characters = characters,
    }

    PATTSyncDB.lastExportTime = exportTime
    PATTSyncDB.totalExports = (PATTSyncDB.totalExports or 0) + 1

    isExporting = false

    Print(GREEN .. "Export complete! " .. RESET .. WHITE ..
          #characters .. " characters exported." .. RESET)
    Print(WHITE .. "The companion app will upload this data automatically." .. RESET)
end

-- ============================================================
-- Event Handling
-- ============================================================

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("GUILD_ROSTER_UPDATE")

-- Track if we've already exported for this guild window opening
local guildWindowExportDone = false

frame:SetScript("OnEvent", function(self, event, ...)
    if event == "ADDON_LOADED" then
        local addonName = ...
        if addonName == ADDON_NAME then
            -- Initialize DB
            PATTSyncDB = PATTSyncDB or {}
            PATTSyncDB.totalExports = PATTSyncDB.totalExports or 0

            Print(GREEN .. "v" .. ADDON_VERSION .. RESET ..
                  WHITE .. " loaded. Type " .. GOLD .. "/pattsync" ..
                  WHITE .. " for commands." .. RESET)

            -- Hook into guild frame opening to auto-export
            -- CommunitiesFrame is the modern guild UI
            if CommunitiesFrame then
                CommunitiesFrame:HookScript("OnShow", function()
                    if not guildWindowExportDone then
                        local onCooldown = IsCooldownActive()
                        if not onCooldown then
                            Print(WHITE .. "Guild window opened — auto-exporting roster..." .. RESET)
                            ExportGuildRoster(false)
                            guildWindowExportDone = true

                            -- Reset the flag after cooldown window
                            C_Timer.After(COOLDOWN_SECONDS, function()
                                guildWindowExportDone = false
                            end)
                        end
                    end
                end)
            end

            -- Also try hooking GuildFrame for classic-style UI
            hooksecurefunc("ToggleGuildFrame", function()
                if not guildWindowExportDone then
                    local onCooldown = IsCooldownActive()
                    if not onCooldown then
                        ExportGuildRoster(false)
                        guildWindowExportDone = true
                        C_Timer.After(COOLDOWN_SECONDS, function()
                            guildWindowExportDone = false
                        end)
                    end
                end
            end)
        end

    elseif event == "GUILD_ROSTER_UPDATE" then
        if isExporting then
            ProcessGuildRoster()
        end
    end
end)

-- ============================================================
-- Slash Commands
-- ============================================================

SLASH_PATTSYNC1 = "/pattsync"
SLASH_PATTSYNC2 = "/patt"

SlashCmdList["PATTSYNC"] = function(msg)
    msg = strtrim(msg):lower()

    if msg == "" or msg == "help" then
        Print(GOLD .. "PATTSync Commands:" .. RESET)
        Print(WHITE .. "  /pattsync" .. RESET .. " — Export guild roster (respects cooldown)")
        Print(WHITE .. "  /pattsync force" .. RESET .. " — Export regardless of cooldown")
        Print(WHITE .. "  /pattsync status" .. RESET .. " — Show last export info")
        Print(WHITE .. "  /pattsync help" .. RESET .. " — Show this help")

    elseif msg == "force" then
        Print(GOLD .. "Forcing export..." .. RESET)
        ExportGuildRoster(true)

    elseif msg == "status" then
        local lastTime = PATTSyncDB.lastExportTime or 0
        local totalExports = PATTSyncDB.totalExports or 0

        if lastTime > 0 then
            local elapsed = GetTimestamp() - lastTime
            Print(WHITE .. "Last export: " .. GREEN ..
                  FormatTimeDiff(elapsed) .. " ago" .. RESET)

            local lastExport = PATTSyncDB.lastExport
            if lastExport then
                Print(WHITE .. "  Members exported: " .. GREEN ..
                      (lastExport.memberCount or "?") .. RESET)
            end

            local onCooldown, remaining = IsCooldownActive()
            if onCooldown then
                Print(WHITE .. "  Next export in: " .. GOLD ..
                      FormatTimeDiff(remaining) .. RESET)
            else
                Print(WHITE .. "  Status: " .. GREEN .. "Ready to export" .. RESET)
            end
        else
            Print(WHITE .. "No exports yet this session." .. RESET)
        end

        Print(WHITE .. "  Total exports (all time): " .. GREEN ..
              totalExports .. RESET)

    else
        ExportGuildRoster(false)
    end
end
