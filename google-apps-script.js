// ============================================
// PULL ALL THE THINGS - GUILD ROSTER v3
// Google Apps Script Backend with Discord Validation
// + Mito's Corner (Quotes & Titles)
// ============================================
//
// This script manages five sheets:
// 1. Availability - One row per Discord user (1:1 relationship)
// 2. Characters - Multiple rows per Discord user (1:many relationship)
// 3. DiscordIDs - Maps Discord names to Discord User IDs (for API integration)
// 4. MitoQuotes - Mito's legendary quotes for the landing page
// 5. MitoTitles - Mito's glorious titles/nicknames
//
// SETUP INSTRUCTIONS:
// 1. Create a new Google Sheet
// 2. Go to Extensions > Apps Script
// 3. Delete any existing code and paste this entire file
// 4. Click "Deploy" > "New deployment"
// 5. Select type: "Web app"
// 6. Set "Execute as": Me
// 7. Set "Who has access": Anyone
// 8. Click "Deploy" and authorize when prompted
// 9. Copy the Web app URL and paste it into your admin tool
// ============================================

const AVAILABILITY_SHEET = 'Availability';
const CHARACTERS_SHEET = 'Characters';
const DISCORD_IDS_SHEET = 'DiscordIDs';
const MITO_QUOTES_SHEET = 'MitoQuotes';
const MITO_TITLES_SHEET = 'MitoTitles';

// ============================================
// MAIN HANDLERS
// ============================================

// Handle form submissions (POST)
function doPost(e) {
  try {
    // Parse JSON from body (works with both application/json and text/plain)
    let data;
    try {
      data = JSON.parse(e.postData.contents);
    } catch (parseError) {
      // If direct parse fails, try getting from parameter
      if (e.parameter && e.parameter.data) {
        data = JSON.parse(e.parameter.data);
      } else {
        throw new Error('Could not parse request body: ' + parseError.toString());
      }
    }
    
    // Handle different action types
    if (data.action === 'updateDiscordId') {
      return handleDiscordIdUpdate(data);
    }
    
    if (data.action === 'validateDiscord') {
      return handleDiscordValidation(data);
    }
    
    if (data.action === 'createRaidEvent') {
      return handleCreateRaidEvent(data);
    }
    
    if (data.action === 'addRaidSignups') {
      return handleAddRaidSignups(data);
    }
    
    // === MITO'S CORNER HANDLERS ===
    if (data.action === 'addMitoQuote') {
      return handleAddMitoQuote(data);
    }
    
    if (data.action === 'updateMitoQuote') {
      return handleUpdateMitoQuote(data);
    }
    
    if (data.action === 'deleteMitoQuote') {
      return handleDeleteMitoQuote(data);
    }
    
    if (data.action === 'addMitoTitle') {
      return handleAddMitoTitle(data);
    }
    
    if (data.action === 'updateMitoTitle') {
      return handleUpdateMitoTitle(data);
    }
    
    if (data.action === 'deleteMitoTitle') {
      return handleDeleteMitoTitle(data);
    }
    // === END MITO'S CORNER HANDLERS ===
    
    // Default: roster submission
    updateAvailability(data);
    
    if (data.mainAlt === 'Main') {
      demoteExistingMain(data.discordName);
    }
    
    addCharacter(data);
    validateDiscordName(data.discordName);
    
    return ContentService
      .createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);
      
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================
// RAID-HELPER API PROXY FUNCTIONS
// ============================================

function handleCreateRaidEvent(data) {
  try {
    // Correct endpoint includes channel ID in the path
    const channelId = data.eventPayload.channelId;
    const url = `https://raid-helper.dev/api/v2/servers/${data.serverId}/channels/${channelId}/event`;
    
    const options = {
      method: 'POST',
      headers: {
        'Authorization': data.apiKey,
        'Content-Type': 'application/json'
      },
      payload: JSON.stringify(data.eventPayload),
      muteHttpExceptions: true
    };
    
    Logger.log('Calling Raid-Helper API: ' + url);
    Logger.log('Payload: ' + JSON.stringify(data.eventPayload));
    
    const response = UrlFetchApp.fetch(url, options);
    const responseCode = response.getResponseCode();
    const responseText = response.getContentText();
    
    Logger.log('Response code: ' + responseCode);
    Logger.log('Response: ' + responseText);
    
    if (responseCode >= 200 && responseCode < 300) {
      const result = JSON.parse(responseText);
      // Event ID can be at different levels depending on API response
      const eventId = result.id || result.eventId || result.event?.id;
      Logger.log('Extracted event ID: ' + eventId);
      
      return ContentService
        .createTextOutput(JSON.stringify({ 
          success: true, 
          eventId: eventId,
          response: result 
        }))
        .setMimeType(ContentService.MimeType.JSON);
    } else {
      return ContentService
        .createTextOutput(JSON.stringify({ 
          success: false, 
          error: `API returned ${responseCode}: ${responseText}` 
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function handleAddRaidSignups(data) {
  try {
    const results = [];
    
    for (const signup of data.signups) {
      // Correct endpoint: /api/v2/events/EVENTID/signups (no server ID)
      const url = `https://raid-helper.dev/api/v2/events/${data.eventId}/signups`;
      
      // Build signup payload for Raid-Helper API
      const signupPayload = {
        userId: signup.userId || signup.odataid,  // API wants "userId"
        className: signup.className
      };
      
      // Only add specName if provided
      if (signup.specName) {
        signupPayload.specName = signup.specName;
      }
      
      const options = {
        method: 'POST',
        headers: {
          'Authorization': data.apiKey,
          'Content-Type': 'application/json'
        },
        payload: JSON.stringify(signupPayload),
        muteHttpExceptions: true
      };
      
      Logger.log('Signup URL: ' + url);
      Logger.log('Signup payload: ' + JSON.stringify(signupPayload));
      
      try {
        const response = UrlFetchApp.fetch(url, options);
        const code = response.getResponseCode();
        const responseText = response.getContentText();
        Logger.log('Signup response ' + code + ': ' + responseText);
        
        results.push({ 
          odataid: signupPayload.userId, 
          success: code >= 200 && code < 300,
          code: code,
          response: responseText.substring(0, 200)
        });
      } catch (e) {
        results.push({ odataid: signupPayload.odataid, success: false, error: e.toString() });
      }
      
      // Small delay to avoid rate limiting
      Utilities.sleep(200);
    }
    
    return ContentService
      .createTextOutput(JSON.stringify({ success: true, results: results }))
      .setMimeType(ContentService.MimeType.JSON);
      
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// Return roster data (GET)
function doGet(e) {
  try {
    const availability = getAvailabilityData();
    const characters = getCharactersData();
    const discordIds = getDiscordIdsData();
    const validationIssues = getValidationIssues();
    const mitoQuotes = getMitoQuotes();
    const mitoTitles = getMitoTitles();
    
    return ContentService
      .createTextOutput(JSON.stringify({ 
        success: true, 
        availability: availability,
        characters: characters,
        discordIds: discordIds,
        validationIssues: validationIssues,
        mitoQuotes: mitoQuotes,
        mitoTitles: mitoTitles
      }))
      .setMimeType(ContentService.MimeType.JSON);
      
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================
// AVAILABILITY FUNCTIONS
// ============================================

function getOrCreateAvailabilitySheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(AVAILABILITY_SHEET);
  
  if (!sheet) {
    sheet = ss.insertSheet(AVAILABILITY_SHEET);
    const headers = ['Discord', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday', 'Auto-Signup', 'Wants Reminders', 'Notes', 'Updated'];
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    formatHeaderRow(sheet, headers.length);
  }
  
  return sheet;
}

function updateAvailability(data) {
  const sheet = getOrCreateAvailabilitySheet();
  const allData = sheet.getDataRange().getValues();
  const headers = allData[0];
  
  const discordCol = headers.indexOf('Discord');
  const mondayCol = headers.indexOf('Monday');
  const tuesdayCol = headers.indexOf('Tuesday');
  const wednesdayCol = headers.indexOf('Wednesday');
  const thursdayCol = headers.indexOf('Thursday');
  const fridayCol = headers.indexOf('Friday');
  const saturdayCol = headers.indexOf('Saturday');
  const sundayCol = headers.indexOf('Sunday');
  const autoSignupCol = headers.indexOf('Auto-Signup');
  const wantsRemindersCol = headers.indexOf('Wants Reminders');
  const notesCol = headers.indexOf('Notes');
  const updatedCol = headers.indexOf('Updated');
  
  // Check if this discord user already exists
  let existingRow = -1;
  for (let i = 1; i < allData.length; i++) {
    if (allData[i][discordCol].toString().toLowerCase() === data.discordName.toLowerCase()) {
      existingRow = i + 1;
      break;
    }
  }
  
  const rowData = [];
  rowData[discordCol] = data.discordName;
  rowData[mondayCol] = data.availability?.monday || false;
  rowData[tuesdayCol] = data.availability?.tuesday || false;
  rowData[wednesdayCol] = data.availability?.wednesday || false;
  rowData[thursdayCol] = data.availability?.thursday || false;
  rowData[fridayCol] = data.availability?.friday || false;
  rowData[saturdayCol] = data.availability?.saturday || false;
  rowData[sundayCol] = data.availability?.sunday || false;
  rowData[autoSignupCol] = data.autoSignup || false;
  rowData[wantsRemindersCol] = data.wantsReminders || false;
  rowData[notesCol] = data.notes || '';
  rowData[updatedCol] = new Date();
  
  if (existingRow > 0) {
    // Update existing row
    sheet.getRange(existingRow, 1, 1, headers.length).setValues([rowData]);
  } else {
    // Add new row
    sheet.appendRow(rowData);
  }
}

function getAvailabilityData() {
  const sheet = getOrCreateAvailabilitySheet();
  const data = sheet.getDataRange().getValues();
  
  if (data.length <= 1) return [];
  
  const headers = data[0];
  const result = [];
  
  for (let i = 1; i < data.length; i++) {
    const row = {};
    headers.forEach((header, idx) => {
      // Normalize header names for consistent API
      let key = header.toLowerCase().replace(/[^a-z0-9]/g, '');
      if (key === 'autosignup') key = 'autoSignup';
      if (key === 'wantsreminders') key = 'wantsReminders';
      row[key] = data[i][idx];
    });
    result.push(row);
  }
  
  return result;
}

// ============================================
// CHARACTERS FUNCTIONS
// ============================================

function getOrCreateCharactersSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(CHARACTERS_SHEET);
  
  if (!sheet) {
    sheet = ss.insertSheet(CHARACTERS_SHEET);
    const headers = ['Discord', 'Character', 'Class', 'Spec', 'Role', 'Main/Alt', 'Updated'];
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    formatHeaderRow(sheet, headers.length);
  }
  
  return sheet;
}

function addCharacter(data) {
  const sheet = getOrCreateCharactersSheet();
  const allData = sheet.getDataRange().getValues();
  const headers = allData[0];
  
  const discordCol = headers.indexOf('Discord');
  const charCol = headers.indexOf('Character');
  const classCol = headers.indexOf('Class');
  const specCol = headers.indexOf('Spec');
  const roleCol = headers.indexOf('Role');
  const mainAltCol = headers.indexOf('Main/Alt');
  const updatedCol = headers.indexOf('Updated');
  
  // Check if this character already exists for this user
  let existingRow = -1;
  for (let i = 1; i < allData.length; i++) {
    if (allData[i][discordCol].toString().toLowerCase() === data.discordName.toLowerCase() &&
        allData[i][charCol].toString().toLowerCase() === data.characterName.toLowerCase()) {
      existingRow = i + 1;
      break;
    }
  }
  
  const rowData = [];
  rowData[discordCol] = data.discordName;
  rowData[charCol] = data.characterName;
  rowData[classCol] = data.class;
  rowData[specCol] = data.spec;
  rowData[roleCol] = data.role;
  rowData[mainAltCol] = data.mainAlt;
  rowData[updatedCol] = new Date();
  
  if (existingRow > 0) {
    sheet.getRange(existingRow, 1, 1, headers.length).setValues([rowData]);
  } else {
    sheet.appendRow(rowData);
  }
}

function demoteExistingMain(discordName) {
  const sheet = getOrCreateCharactersSheet();
  const data = sheet.getDataRange().getValues();
  const headers = data[0];
  
  const discordCol = headers.indexOf('Discord');
  const mainAltCol = headers.indexOf('Main/Alt');
  const updatedCol = headers.indexOf('Updated');
  
  for (let i = 1; i < data.length; i++) {
    if (data[i][discordCol].toString().toLowerCase() === discordName.toLowerCase() &&
        data[i][mainAltCol] === 'Main') {
      sheet.getRange(i + 1, mainAltCol + 1).setValue('Alt');
      sheet.getRange(i + 1, updatedCol + 1).setValue(new Date());
    }
  }
}

function getCharactersData() {
  const sheet = getOrCreateCharactersSheet();
  const data = sheet.getDataRange().getValues();
  
  if (data.length <= 1) return [];
  
  const headers = data[0];
  const result = [];
  
  for (let i = 1; i < data.length; i++) {
    const row = {
      discord: data[i][headers.indexOf('Discord')],
      character: data[i][headers.indexOf('Character')],
      class: data[i][headers.indexOf('Class')],
      spec: data[i][headers.indexOf('Spec')],
      role: data[i][headers.indexOf('Role')],
      mainAlt: data[i][headers.indexOf('Main/Alt')]
    };
    result.push(row);
  }
  
  return result;
}

// ============================================
// DISCORD ID FUNCTIONS
// ============================================

function getOrCreateDiscordIdsSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(DISCORD_IDS_SHEET);
  
  if (!sheet) {
    sheet = ss.insertSheet(DISCORD_IDS_SHEET);
    const headers = ['Discord Name', 'Discord User ID', 'Status', 'Updated'];
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    formatHeaderRow(sheet, headers.length);
  }
  
  return sheet;
}

function validateDiscordName(discordName) {
  if (!discordName || discordName.trim() === '') return;
  
  const sheet = getOrCreateDiscordIdsSheet();
  const data = sheet.getDataRange().getValues();
  const headers = data[0];
  
  const nameCol = headers.indexOf('Discord Name');
  const statusCol = headers.indexOf('Status');
  const updatedCol = headers.indexOf('Updated');
  
  // Check if already exists
  for (let i = 1; i < data.length; i++) {
    if (data[i][nameCol].toString().toLowerCase() === discordName.toLowerCase()) {
      return; // Already tracked
    }
  }
  
  // Determine status based on format
  let status = 'Unknown';
  if (isValidDiscordFormat(discordName)) {
    status = 'Pending ID'; // Valid format, but needs User ID
  } else {
    status = 'Invalid Format';
  }
  
  // Add new entry
  sheet.appendRow([discordName, '', status, new Date()]);
}

function isValidDiscordFormat(name) {
  if (!name || name.length < 2) return false;
  
  // Old format with discriminator
  if (name.includes('#')) {
    const parts = name.split('#');
    return parts.length === 2 && parts[1].length === 4 && /^\d{4}$/.test(parts[1]);
  }
  
  // New format - lowercase, 2-32 chars
  return name.length >= 2 && name.length <= 32;
}

function getDiscordIdsData() {
  const sheet = getOrCreateDiscordIdsSheet();
  const data = sheet.getDataRange().getValues();
  
  if (data.length <= 1) return {};
  
  const headers = data[0];
  const result = {};
  
  for (let i = 1; i < data.length; i++) {
    const name = data[i][headers.indexOf('Discord Name')];
    const id = data[i][headers.indexOf('Discord User ID')];
    if (name && id) {
      result[name] = id;
    }
  }
  
  return result;
}

function getValidationIssues() {
  const sheet = getOrCreateDiscordIdsSheet();
  const data = sheet.getDataRange().getValues();
  
  if (data.length <= 1) return [];
  
  const headers = data[0];
  const issues = [];
  
  for (let i = 1; i < data.length; i++) {
    const status = data[i][headers.indexOf('Status')];
    if (status === 'Invalid Format' || status === 'Pending ID' || status === 'Unknown') {
      issues.push({
        name: data[i][headers.indexOf('Discord Name')],
        status: status,
        hasId: !!data[i][headers.indexOf('Discord User ID')]
      });
    }
  }
  
  return issues;
}

function handleDiscordIdUpdate(data) {
  const sheet = getOrCreateDiscordIdsSheet();
  const allData = sheet.getDataRange().getValues();
  const headers = allData[0];
  
  const nameCol = headers.indexOf('Discord Name');
  const idCol = headers.indexOf('Discord User ID');
  const statusCol = headers.indexOf('Status');
  const updatedCol = headers.indexOf('Updated');
  
  for (let i = 1; i < allData.length; i++) {
    if (allData[i][nameCol].toString().toLowerCase() === data.discordName.toLowerCase()) {
      sheet.getRange(i + 1, idCol + 1).setValue(data.discordUserId);
      sheet.getRange(i + 1, statusCol + 1).setValue(data.discordUserId ? 'Verified' : 'Pending ID');
      sheet.getRange(i + 1, updatedCol + 1).setValue(new Date());
      
      return ContentService
        .createTextOutput(JSON.stringify({ success: true }))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }
  
  // Not found, add new entry
  sheet.appendRow([data.discordName, data.discordUserId, 'Verified', new Date()]);
  
  return ContentService
    .createTextOutput(JSON.stringify({ success: true }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ============================================
// MITO'S QUOTES FUNCTIONS
// ============================================

function getOrCreateMitoQuotesSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(MITO_QUOTES_SHEET);
  
  if (!sheet) {
    sheet = ss.insertSheet(MITO_QUOTES_SHEET);
    const headers = ['Quote'];
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    
    // Add starter quotes
    const starterQuotes = [
      ["Hello, raiders, look at yourself, now back to me, now back at yourself, now back to me. Sadly, you aren't me, but if you joined Pull All the Things you could raid like you're me."],
      ["Look down, back up, where are you? You're in a raid with the Paladin your main could raid like."],
      ["What's in your hand? Back at me. I have it, it's a vault with guaranteed mythics for that slot you need. Look again, the mythics are now 6/6."],
      ["Anything is possible when your character raids like a Paladin. I'm on a charger."],
      ["TWW was a great expansion with Pull All The Things, AotC every season and had a blast doing it."],
      ["Casual Heroic is that a thing? Yes, yes it is! Real life exists and this guild gets it."],
      ["Standards? Yes. Friendly? Yes. We want to enjoy the game but we want to progress through the challenge too!"]
    ];
    
    sheet.getRange(2, 1, starterQuotes.length, 1).setValues(starterQuotes);
    formatHeaderRow(sheet, 1);
  }
  
  return sheet;
}

function getMitoQuotes() {
  const sheet = getOrCreateMitoQuotesSheet();
  const data = sheet.getDataRange().getValues();
  
  if (data.length <= 1) return [];
  
  const quotes = [];
  for (let i = 1; i < data.length; i++) {
    if (data[i][0] && data[i][0].toString().trim() !== '') {
      quotes.push(data[i][0].toString().trim());
    }
  }
  
  return quotes;
}

function handleAddMitoQuote(data) {
  try {
    const sheet = getOrCreateMitoQuotesSheet();
    sheet.appendRow([data.quote]);
    
    return ContentService
      .createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function handleUpdateMitoQuote(data) {
  try {
    const sheet = getOrCreateMitoQuotesSheet();
    const rowIndex = data.index + 2; // +1 for header, +1 for 0-index
    sheet.getRange(rowIndex, 1).setValue(data.quote);
    
    return ContentService
      .createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function handleDeleteMitoQuote(data) {
  try {
    const sheet = getOrCreateMitoQuotesSheet();
    const rowIndex = data.index + 2; // +1 for header, +1 for 0-index
    sheet.deleteRow(rowIndex);
    
    return ContentService
      .createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================
// MITO'S TITLES FUNCTIONS
// ============================================

function getOrCreateMitoTitlesSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(MITO_TITLES_SHEET);
  
  if (!sheet) {
    sheet = ss.insertSheet(MITO_TITLES_SHEET);
    const headers = ['Title'];
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    
    // Add starter titles
    const starterTitles = [
      ["Healer Extraordinaire"],
      ["Paladin Philosopher"],
      ["Old Spice Spokesman"],
      ["The Man Your Main Could Raid Like"],
      ["Charger Enthusiast"],
      ["Bubble Hearth Champion"],
      ["Professional Stand-In-Fire Survivor"],
      ["Lord of the Lay on Hands"],
      ["Beacon of Wisdom"],
      ["The Unkillable"]
    ];
    
    sheet.getRange(2, 1, starterTitles.length, 1).setValues(starterTitles);
    formatHeaderRow(sheet, 1);
  }
  
  return sheet;
}

function getMitoTitles() {
  const sheet = getOrCreateMitoTitlesSheet();
  const data = sheet.getDataRange().getValues();
  
  if (data.length <= 1) return [];
  
  const titles = [];
  for (let i = 1; i < data.length; i++) {
    if (data[i][0] && data[i][0].toString().trim() !== '') {
      titles.push(data[i][0].toString().trim());
    }
  }
  
  return titles;
}

function handleAddMitoTitle(data) {
  try {
    const sheet = getOrCreateMitoTitlesSheet();
    sheet.appendRow([data.title]);
    
    return ContentService
      .createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function handleUpdateMitoTitle(data) {
  try {
    const sheet = getOrCreateMitoTitlesSheet();
    const rowIndex = data.index + 2; // +1 for header, +1 for 0-index
    sheet.getRange(rowIndex, 1).setValue(data.title);
    
    return ContentService
      .createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function handleDeleteMitoTitle(data) {
  try {
    const sheet = getOrCreateMitoTitlesSheet();
    const rowIndex = data.index + 2; // +1 for header, +1 for 0-index
    sheet.deleteRow(rowIndex);
    
    return ContentService
      .createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================
// UTILITY FUNCTIONS
// ============================================

function formatHeaderRow(sheet, numCols) {
  const headerRange = sheet.getRange(1, 1, 1, numCols);
  headerRange.setFontWeight('bold');
  headerRange.setBackground('#1a1a1a');
  headerRange.setFontColor('#d4a84b');
  sheet.setFrozenRows(1);
}

// ============================================
// OFFICER UTILITY FUNCTIONS
// Run these from the Apps Script editor
// ============================================

function getRosterSummary() {
  const chars = getCharactersData();
  const avail = getAvailabilityData();
  
  const mains = chars.filter(c => c.mainAlt === 'Main');
  const tanks = mains.filter(c => c.role === 'Tank').length;
  const healers = mains.filter(c => c.role === 'Healer').length;
  const dps = mains.filter(c => c.role === 'DPS').length;
  
  Logger.log('=== ROSTER SUMMARY ===');
  Logger.log('Total mains: ' + mains.length);
  Logger.log('Tanks: ' + tanks);
  Logger.log('Healers: ' + healers);
  Logger.log('DPS: ' + dps);
  Logger.log('');
  Logger.log('Availability entries: ' + avail.length);
  
  // Day breakdown
  const days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
  days.forEach(day => {
    const count = avail.filter(a => a[day] === true || a[day] === 'TRUE').length;
    Logger.log(day.charAt(0).toUpperCase() + day.slice(1) + ': ' + count + ' available');
  });
}

function removePlayer(discordName) {
  // Remove from Characters
  const charSheet = getOrCreateCharactersSheet();
  let charData = charSheet.getDataRange().getValues();
  let charHeaders = charData[0];
  let discordCol = charHeaders.indexOf('Discord');
  
  for (let i = charData.length - 1; i >= 1; i--) {
    if (charData[i][discordCol].toString().toLowerCase() === discordName.toLowerCase()) {
      charSheet.deleteRow(i + 1);
      Logger.log('Deleted character row: ' + i);
    }
  }
  
  // Remove from Availability
  const availSheet = getOrCreateAvailabilitySheet();
  let availData = availSheet.getDataRange().getValues();
  let availHeaders = availData[0];
  discordCol = availHeaders.indexOf('Discord');
  
  for (let i = availData.length - 1; i >= 1; i--) {
    if (availData[i][discordCol].toString().toLowerCase() === discordName.toLowerCase()) {
      availSheet.deleteRow(i + 1);
      Logger.log('Deleted availability row: ' + i);
    }
  }
  
  Logger.log('Removed player: ' + discordName);
}

function cleanupDuplicates() {
  const sheet = getOrCreateCharactersSheet();
  const data = sheet.getDataRange().getValues();
  const headers = data[0];
  
  const discordCol = headers.indexOf('Discord');
  const charCol = headers.indexOf('Character');
  const updatedCol = headers.indexOf('Updated');
  
  const seen = {};
  const toDelete = [];
  
  for (let i = 1; i < data.length; i++) {
    const key = data[i][discordCol].toString().toLowerCase() + '|' + data[i][charCol].toString().toLowerCase();
    
    if (seen[key]) {
      // Compare dates, keep newer
      const existingDate = new Date(data[seen[key]][updatedCol]);
      const thisDate = new Date(data[i][updatedCol]);
      
      if (thisDate > existingDate) {
        toDelete.push(seen[key] + 1);
        seen[key] = i;
      } else {
        toDelete.push(i + 1);
      }
    } else {
      seen[key] = i;
    }
  }
  
  // Delete in reverse order to preserve row numbers
  toDelete.sort((a, b) => b - a);
  toDelete.forEach(row => {
    sheet.deleteRow(row);
    Logger.log('Deleted duplicate row: ' + row);
  });
  
  Logger.log('Cleanup complete. Deleted ' + toDelete.length + ' duplicates.');
}

function setMainCharacter(discordName, characterName) {
  const sheet = getOrCreateCharactersSheet();
  const data = sheet.getDataRange().getValues();
  const headers = data[0];
  
  const discordCol = headers.indexOf('Discord');
  const charCol = headers.indexOf('Character');
  const mainAltCol = headers.indexOf('Main/Alt');
  const updatedCol = headers.indexOf('Updated');
  
  let foundTarget = false;
  
  for (let i = 1; i < data.length; i++) {
    if (data[i][discordCol].toString().toLowerCase() === discordName.toLowerCase()) {
      if (data[i][charCol].toString().toLowerCase() === characterName.toLowerCase()) {
        sheet.getRange(i + 1, mainAltCol + 1).setValue('Main');
        sheet.getRange(i + 1, updatedCol + 1).setValue(new Date());
        foundTarget = true;
        Logger.log('Set ' + characterName + ' as Main');
      } else if (data[i][mainAltCol] === 'Main') {
        sheet.getRange(i + 1, mainAltCol + 1).setValue('Alt');
        sheet.getRange(i + 1, updatedCol + 1).setValue(new Date());
        Logger.log('Demoted ' + data[i][charCol] + ' to Alt');
      }
    }
  }
  
  if (!foundTarget) {
    Logger.log('Character not found: ' + characterName + ' for discord: ' + discordName);
  }
}

// ============================================
// BULK IMPORT (for migrating existing roster)
// ============================================

function importFromCsv() {
  // Edit this data with your existing roster
  const members = [
    // { discord: 'username', character: 'CharName', class: 'Warrior', spec: 'Arms', role: 'DPS', mainAlt: 'Main' },
  ];
  
  members.forEach(m => {
    addCharacter({
      discordName: m.discord,
      characterName: m.character,
      class: m.class,
      spec: m.spec,
      role: m.role,
      mainAlt: m.mainAlt
    });
    validateDiscordName(m.discord);
  });
  
  Logger.log('Imported ' + members.length + ' members');
}
