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
// Progression panel helpers
// ---------------------------------------------------------------------------

const DIFF_ORDER = ["mythic", "heroic", "normal"];

function mplusScoreTier(score) {
  if (score >= 2500) return "pink";
  if (score >= 2000) return "orange";
  if (score >= 1500) return "purple";
  if (score >= 1000) return "blue";
  if (score >= 500)  return "green";
  return "gray";
}

function renderRaidDiffRow(diff, data) {
  const label = diff.charAt(0).toUpperCase() + diff.slice(1);
  if (!data) {
    return `
      <div class="mc-raid-row">
        <span class="mc-raid-row__diff">${label}</span>
        <span class="mc-raid-row__fraction mc-raid-row__fraction--none">&mdash;</span>
      </div>`;
  }
  const { killed, total } = data;
  const pct = total > 0 ? Math.round((killed / total) * 100) : 0;
  const isClear = killed === total && total > 0;
  const isMythic = diff === "mythic";
  const isZero = killed === 0;

  let fractionClass = "mc-raid-row__fraction";
  let barClass = "mc-raid-row__bar-fill";
  let icon = "";
  if (isMythic && killed > 0) {
    fractionClass += " mc-raid-row__fraction--mythic";
    barClass += " mc-raid-row__bar-fill--mythic";
    icon = "🔥 ";
  } else if (isClear) {
    fractionClass += " mc-raid-row__fraction--clear";
    icon = "✅ ";
  } else if (isZero) {
    fractionClass += " mc-raid-row__fraction--zero";
  } else {
    fractionClass += " mc-raid-row__fraction--partial";
  }

  return `
    <div class="mc-raid-row">
      <span class="mc-raid-row__diff">${label}</span>
      <div class="mc-raid-row__bar-wrap">
        <div class="${barClass}" style="width:${pct}%"></div>
      </div>
      <span class="${fractionClass}">${icon}${killed}/${total}</span>
    </div>`;
}

function renderProgressionPanel(data) {
  const panel = document.getElementById("mc-progression");

  const { raid_progress, mythic_plus } = data;

  let html = '<div class="mc-progression">';

  // ── Raid progress card ──
  if (raid_progress && raid_progress.length > 0) {
    html += '<div class="mc-prog-card">';
    html += '<div class="mc-prog-card__title">Raid Progression</div>';
    html += '<div class="mc-prog-card__body">';
    for (const tier of raid_progress) {
      html += `<div class="mc-raid-name">${tier.raid_name}</div>`;
      for (const diff of DIFF_ORDER) {
        html += renderRaidDiffRow(diff, tier.difficulties[diff] || null);
      }
    }
    html += '</div></div>';
  }

  // ── M+ score card ──
  html += '<div class="mc-prog-card">';
  html += '<div class="mc-prog-card__title">Mythic+</div>';
  html += '<div class="mc-prog-card__body">';

  if (mythic_plus && mythic_plus.overall_score > 0) {
    const tier = mplusScoreTier(mythic_plus.overall_score);
    const scoreDisplay = Math.round(mythic_plus.overall_score);
    html += `
      <div class="mc-mplus-row">
        <span class="mc-mplus-season">${mythic_plus.season_name}</span>
        <span class="mc-mplus-score-badge mc-mplus-score--${tier}">${scoreDisplay}</span>
      </div>`;
    if (mythic_plus.best_run_level) {
      const dungeon = mythic_plus.best_run_dungeon || "Unknown";
      html += `<div class="mc-mplus-best">Best key: <strong>+${mythic_plus.best_run_level}</strong> &mdash; ${dungeon}</div>`;
    }
  } else {
    html += '<span class="mc-mplus-empty">No keys this season</span>';
  }

  html += '</div></div>';
  html += '</div>';

  panel.innerHTML = html;
  panel.hidden = false;
}

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

let _allChars = [];

async function selectCharacter(charId) {
  const char = _allChars.find(c => c.id === charId);
  if (!char) return;

  // Update URL without full reload
  const url = new URL(window.location.href);
  url.searchParams.set("char", charId);
  history.replaceState(null, "", url.toString());

  renderSelectorMeta(char);
  renderPanel(char);
  showStaleNotice(char);

  // Load progression panel
  const progPanel = document.getElementById("mc-progression");
  progPanel.hidden = true;
  progPanel.innerHTML = "";

  try {
    const resp = await fetch(`/api/v1/me/character/${charId}/progression`, { credentials: "include" });
    if (resp.ok) {
      const json = await resp.json();
      if (json.ok) {
        renderProgressionPanel(json.data);
      }
    }
  } catch (err) {
    // Progression is non-critical — fail silently
    console.warn("Progression load failed:", err);
  }
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
