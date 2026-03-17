/* My Characters dashboard — character selector + stat panel */

const STALE_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function timeAgo(ms) {
  if (!ms) return "Unknown";
  const diff = Date.now() - ms;
  const days = Math.floor(diff / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks} week${weeks > 1 ? "s" : ""} ago`;
  const months = Math.floor(days / 30);
  return `${months} month${months > 1 ? "s" : ""} ago`;
}

function realmDisplay(char) {
  return char.realm_display || char.realm_slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function parseSyncDate(isoStr) {
  if (!isoStr) return null;
  try {
    return new Date(isoStr).getTime();
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderSelector(chars, selectedId) {
  const sel = document.getElementById("mc-selector");
  sel.innerHTML = "";
  chars.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = `${c.character_name} \u2014 ${realmDisplay(c)}`;
    if (c.id === selectedId) opt.selected = true;
    sel.appendChild(opt);
  });
}

function renderSelectorMeta(char) {
  const meta = document.getElementById("mc-selector-meta");
  const parts = [];
  if (char.class_name) parts.push(char.class_name);
  if (char.spec_name) parts.push(char.spec_name);
  meta.textContent = parts.join(" \u2014 ");
}

function renderPanel(char) {
  const panel = document.getElementById("mc-panel");

  const badges = [];
  if (char.is_main) badges.push('<span class="mc-badge mc-badge--main">Main</span>');
  if (char.is_offspec) badges.push('<span class="mc-badge mc-badge--offspec">Offspec</span>');
  if (char.link_source === "battlenet_oauth") badges.push('<span class="mc-badge mc-badge--bnet">\uD83D\uDD12 Battle.net</span>');

  const classColor = char.class_color || "var(--color-accent)";

  const links = [];
  links.push(`<a href="${char.armory_url}" target="_blank" rel="noopener noreferrer" class="mc-link">Armory</a>`);
  if (char.raiderio_url) {
    links.push(`<a href="${char.raiderio_url}" target="_blank" rel="noopener noreferrer" class="mc-link">Raider.IO</a>`);
  }
  links.push(`<a href="${char.wcl_url}" target="_blank" rel="noopener noreferrer" class="mc-link">WarcraftLogs</a>`);

  const ilvl = char.avg_item_level != null ? char.avg_item_level : "\u2014";
  const lastLogin = char.last_login_ms ? timeAgo(char.last_login_ms) : "\u2014";

  panel.style.setProperty("--mc-class-color", classColor);

  panel.innerHTML = `
    <div class="mc-panel__header">
      <span class="mc-panel__emoji" aria-hidden="true">${char.class_emoji || "?"}</span>
      <div class="mc-panel__name-block">
        <span class="mc-panel__name" style="color:${classColor}">${char.character_name}</span>
        <span class="mc-panel__realm">${realmDisplay(char)}</span>
      </div>
      <div class="mc-panel__badges">${badges.join("")}</div>
    </div>
    <div class="mc-panel__body">
      <div class="mc-stat-grid">
        <div class="mc-stat-field">
          <span class="mc-stat-field__label">Class</span>
          <span class="mc-stat-field__value" style="color:${classColor}">${char.class_name || "\u2014"}</span>
        </div>
        <div class="mc-stat-field">
          <span class="mc-stat-field__label">Spec</span>
          <span class="mc-stat-field__value">${char.spec_name || "\u2014"}</span>
        </div>
        <div class="mc-stat-field">
          <span class="mc-stat-field__label">Item Level</span>
          <span class="mc-stat-field__value mc-stat-field__value--accent">${ilvl}</span>
        </div>
        <div class="mc-stat-field">
          <span class="mc-stat-field__label">Last Login</span>
          <span class="mc-stat-field__value">${lastLogin}</span>
        </div>
      </div>
      <div class="mc-links">
        ${links.join("")}
      </div>
    </div>
  `;
}

function showStaleNotice(char) {
  const notice = document.getElementById("mc-stale-notice");
  const syncMs = parseSyncDate(char.last_synced_at);
  if (syncMs && (Date.now() - syncMs) > STALE_THRESHOLD_MS) {
    notice.hidden = false;
  } else {
    notice.hidden = true;
  }
}

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

let _allChars = [];

function selectCharacter(charId) {
  const char = _allChars.find(c => c.id === charId);
  if (!char) return;

  // Update URL without full reload
  const url = new URL(window.location.href);
  url.searchParams.set("char", charId);
  history.replaceState(null, "", url.toString());

  renderSelectorMeta(char);
  renderPanel(char);
  showStaleNotice(char);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  const loading = document.getElementById("mc-loading");
  const errorEl = document.getElementById("mc-error");
  const selectorBar = document.getElementById("mc-selector-bar");
  const panel = document.getElementById("mc-panel");
  const emptyEl = document.getElementById("mc-empty");

  try {
    const resp = await fetch("/api/v1/me/characters", { credentials: "include" });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error || "API error");

    const { characters, default_character_id } = json.data;
    loading.hidden = true;

    if (!characters || characters.length === 0) {
      emptyEl.hidden = false;
      return;
    }

    _allChars = characters;

    // Check URL for ?char= param
    const urlParams = new URLSearchParams(window.location.search);
    const urlCharId = parseInt(urlParams.get("char"), 10);
    const validUrlId = _allChars.find(c => c.id === urlCharId) ? urlCharId : null;
    const initialId = validUrlId || default_character_id || _allChars[0].id;

    renderSelector(_allChars, initialId);
    selectorBar.hidden = false;
    panel.hidden = false;
    selectCharacter(initialId);

    document.getElementById("mc-selector").addEventListener("change", e => {
      selectCharacter(parseInt(e.target.value, 10));
    });

  } catch (err) {
    loading.hidden = true;
    errorEl.hidden = false;
    console.error("My Characters load failed:", err);
  }
}

document.addEventListener("DOMContentLoaded", init);
