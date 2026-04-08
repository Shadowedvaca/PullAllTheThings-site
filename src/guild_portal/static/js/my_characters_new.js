/* My Characters (New) — character selector + persistent header
 * Phase UI-1A: foundation + header rendering with WoW icons
 */

// ---------------------------------------------------------------------------
// WoW Icon helpers — Wowhead CDN
// ---------------------------------------------------------------------------

const WOWHEAD_ICON_BASE = "https://wow.zamimg.com/images/wow/icons/medium/";

function wowIcon(slug, alt, extraClass) {
  if (!slug) return "";
  const cls = extraClass ? ` ${extraClass}` : "";
  return `<img src="${WOWHEAD_ICON_BASE}${slug}.jpg" alt="${alt || ""}" title="${alt || ""}" class="mcn-wow-icon${cls}" loading="lazy">`;
}

// ── Class icons ──────────────────────────────────────────────────────────

const CLASS_ICONS = {
  "Death Knight":  "classicon_deathknight",
  "Demon Hunter":  "classicon_demonhunter",
  "Druid":         "classicon_druid",
  "Evoker":        "classicon_evoker",
  "Hunter":        "classicon_hunter",
  "Mage":          "classicon_mage",
  "Monk":          "classicon_monk",
  "Paladin":       "classicon_paladin",
  "Priest":        "classicon_priest",
  "Rogue":         "classicon_rogue",
  "Shaman":        "classicon_shaman",
  "Warlock":       "classicon_warlock",
  "Warrior":       "classicon_warrior",
};

function classIcon(className) {
  const slug = CLASS_ICONS[className];
  return slug ? wowIcon(slug, className) : "";
}

// ── Spec icons (nested by class to handle name collisions) ───────────────

const SPEC_ICONS = {
  "Death Knight": {
    "Blood":  "spell_deathknight_bloodpresence",
    "Frost":  "spell_deathknight_frostpresence",
    "Unholy": "spell_deathknight_unholypresence",
  },
  "Demon Hunter": {
    "Havoc":      "ability_demonhunter_specdps",
    "Vengeance":  "ability_demonhunter_spectank",
  },
  "Druid": {
    "Balance":     "spell_nature_starfall",
    "Feral":       "ability_druid_catform",
    "Guardian":    "ability_racial_bearform",
    "Restoration": "spell_nature_healingtouch",
  },
  "Evoker": {
    "Augmentation": "classicon_evoker_augmentation",
    "Devastation":  "classicon_evoker_devastation",
    "Preservation": "classicon_evoker_preservation",
  },
  "Hunter": {
    "Beast Mastery": "ability_hunter_bestialdiscipline",
    "Marksmanship":  "ability_hunter_focusedaim",
    "Survival":      "ability_hunter_camouflage",
  },
  "Mage": {
    "Arcane": "spell_holy_magicalsentry",
    "Fire":   "spell_fire_firebolt02",
    "Frost":  "spell_frost_frostbolt02",
  },
  "Monk": {
    "Brewmaster":  "monk_stance_drunkenox",
    "Mistweaver":  "monk_stance_wiseserpent",
    "Windwalker":  "monk_stance_whitetiger",
  },
  "Paladin": {
    "Holy":        "spell_holy_holybolt",
    "Protection":  "ability_paladin_shieldofthetemplar",
    "Retribution": "spell_holy_auraoflight",
  },
  "Priest": {
    "Discipline": "spell_holy_powerwordshield",
    "Holy":       "spell_holy_guardianspirit",
    "Shadow":     "spell_shadow_shadowwordpain",
  },
  "Rogue": {
    "Assassination": "ability_rogue_deadlybrew",
    "Outlaw":        "ability_rogue_waylay",
    "Subtlety":      "ability_stealth",
  },
  "Shaman": {
    "Elemental":   "spell_nature_lightning",
    "Enhancement": "spell_shaman_improvedstormstrike",
    "Restoration": "spell_nature_magicimmunity",
  },
  "Warlock": {
    "Affliction":  "spell_shadow_deathcoil",
    "Demonology":  "spell_shadow_metamorphosis",
    "Destruction": "spell_shadow_rainoffire",
  },
  "Warrior": {
    "Arms":       "ability_warrior_savageblow",
    "Fury":       "ability_warrior_innerrage",
    "Protection": "ability_warrior_defensivestance",
  },
};

function specIcon(className, specName) {
  const classSpecs = SPEC_ICONS[className] || {};
  const slug = classSpecs[specName];
  return slug ? wowIcon(slug, specName) : "";
}

// ── Role icons ───────────────────────────────────────────────────────────

const ROLE_ICONS = {
  tank:   "ui-lfg-icon-tank",
  healer: "ui-lfg-icon-healer",
  dps:    "ui-lfg-icon-dps",
  ranged: "ui-lfg-icon-dps",
  melee:  "ui-lfg-icon-dps",
};

const ROLE_DISPLAY = {
  tank:   "Tank",
  healer: "Healer",
  dps:    "DPS",
  ranged: "Ranged",
  melee:  "Melee",
};

const ROLE_CSS_CLASS = {
  tank:   "mcn-role-label--tank",
  healer: "mcn-role-label--healer",
  dps:    "mcn-role-label--melee",
  ranged: "mcn-role-label--ranged",
  melee:  "mcn-role-label--melee",
};

function _normaliseRole(roleStr) {
  if (!roleStr) return null;
  const r = roleStr.toLowerCase();
  if (r.includes("tank"))   return "tank";
  if (r.includes("heal"))   return "healer";
  if (r.includes("ranged")) return "ranged";
  if (r.includes("melee"))  return "melee";
  return "dps";
}

function roleIcon(roleStr) {
  const key = _normaliseRole(roleStr);
  if (!key) return "";
  const slug = ROLE_ICONS[key];
  return slug ? wowIcon(slug, ROLE_DISPLAY[key] || key) : "";
}

// ── Race icons (placeholder — gender field not yet synced) ───────────────
// Will be wired in UI-1B when gender is populated. For Phase UI-1A we show
// race as text only; the raceIcon helper is stubbed for future use.
function raceIcon(race, gender) {  // eslint-disable-line no-unused-vars
  if (!race) return "";
  const g = (gender || "male").toLowerCase();
  const slug = `race_${race.toLowerCase().replace(/\s+/g, "")}_${g}`;
  return wowIcon(slug, race);
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _chars = [];           // full character list from API
let _selectedChar = null;  // currently displayed character
const _guideSpecsByChar = {};
const _summaryCache = {};  // keyed by character_id

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function _show(id)    { const el = document.getElementById(id); if (el) el.hidden = false; }
function _hide(id)    { const el = document.getElementById(id); if (el) el.hidden = true; }
function _text(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function _html(id, v) { const el = document.getElementById(id); if (el) el.innerHTML = v; }

// ---------------------------------------------------------------------------
// Character selector
// ---------------------------------------------------------------------------

function _populateSelector(chars, defaultId) {
  const sel = document.getElementById("mcn-char-select");
  if (!sel) return;
  sel.innerHTML = "";
  chars.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = `${c.character_name} \u2014 ${c.realm_display || c.realm_slug}`;
    if (c.id === defaultId) opt.selected = true;
    sel.appendChild(opt);
  });
}

// ---------------------------------------------------------------------------
// Header render
// ---------------------------------------------------------------------------

function _renderHeader(char) {
  // Row 1
  const nameEl = document.getElementById("mcn-char-name");
  if (nameEl) {
    nameEl.textContent = char.character_name;
    if (char.class_color) nameEl.style.color = char.class_color;
  }
  _text("mcn-char-realm", char.realm_display || char.realm_slug);

  const bnetBadge = document.getElementById("mcn-bnet-badge");
  if (bnetBadge) bnetBadge.hidden = !char.bnet_linked;

  // Row 2 — class icon
  _html("mcn-class-icon", classIcon(char.class_name));

  // class + spec label
  const parts = [];
  if (char.spec_name)  parts.push(char.spec_name);
  if (char.class_name) parts.push(char.class_name);
  _text("mcn-class-spec", parts.join(" "));

  // spec icon
  _html("mcn-spec-icon", specIcon(char.class_name, char.spec_name));

  // race text + its separator
  const raceEl = document.getElementById("mcn-race");
  const raceSepEl = document.getElementById("mcn-race-sep");
  if (raceEl) {
    raceEl.textContent = char.race || "";
    raceEl.hidden = !char.race;
  }
  if (raceSepEl) raceSepEl.hidden = !char.race;

  // role icon + label
  _html("mcn-role-icon", roleIcon(char.role));

  const roleLabelEl = document.getElementById("mcn-role-label");
  if (roleLabelEl) {
    const key = _normaliseRole(char.role);
    roleLabelEl.textContent = key ? (ROLE_DISPLAY[key] || char.role) : "";
    roleLabelEl.className = "mcn-role-label";
    if (key) roleLabelEl.classList.add(ROLE_CSS_CLASS[key] || "");
    roleLabelEl.hidden = !char.role;
  }

  // Main / Off badges
  const mainBadge = document.getElementById("mcn-main-badge");
  const offBadge  = document.getElementById("mcn-offspec-badge");
  if (mainBadge) mainBadge.hidden = !char.is_main;
  if (offBadge)  offBadge.hidden  = !char.is_offspec;

  _show("mcn-header");
}

// ---------------------------------------------------------------------------
// Guide section
// ---------------------------------------------------------------------------

function _renderGuideBadges(links) {
  const container = document.getElementById("mcn-guide-badges");
  if (!container) return;
  if (!links || !links.length) {
    container.innerHTML = '<span class="mcn-guide-empty">No guides configured</span>';
    return;
  }
  container.innerHTML = links.map(l =>
    `<a href="${l.url}" target="_blank" rel="noopener noreferrer"
        class="mcn-guide-badge"
        style="background:${l.badge_bg_color};color:${l.badge_text_color};border-color:${l.badge_border_color}">
      ${l.badge_label}
    </a>`
  ).join("");
}

function _renderGuides(char) {
  const guideEl = document.getElementById("mcn-guides");
  if (!guideEl) return;

  if (!char.class_specs || char.class_specs.length === 0) {
    guideEl.hidden = true;
    return;
  }

  _guideSpecsByChar[char.id] = char.class_specs;

  const specSel = document.getElementById("mcn-guide-spec");
  if (!specSel) return;

  const defaultSpec = char.spec_name || char.class_specs[0]?.name;
  specSel.innerHTML = char.class_specs
    .map(s => `<option value="${s.name}"${s.name === defaultSpec ? " selected" : ""}>${s.name}</option>`)
    .join("");

  function _showBadgesForSpec(specName) {
    const specs = _guideSpecsByChar[char.id] || [];
    const spec  = specs.find(s => s.name === specName) || specs[0];
    _renderGuideBadges(spec?.guide_links || []);
  }

  _showBadgesForSpec(defaultSpec);

  specSel.addEventListener("change", () => _showBadgesForSpec(specSel.value));

  guideEl.hidden = false;
}


// ---------------------------------------------------------------------------
// Stat strip + detail area
// ---------------------------------------------------------------------------

const _TABS = [
  { key: "gear",   label: "Gear"   },
  { key: "mplus",  label: "M+"     },
  { key: "raid",   label: "Raid"   },
  { key: "parse",  label: "Parses" },
  { key: "prof",   label: "Profs"  },
  { key: "market", label: "Market" },
];

let _activeTab = "gear";

function _tabValue(key, summary) {
  switch (key) {
    case "gear":
      return summary.avg_ilvl != null
        ? { display: String(summary.avg_ilvl), muted: false }
        : { display: "—", muted: true };
    case "mplus": {
      const score = summary.mplus_score;
      if (score && score > 0) {
        const color = summary.mplus_color || null;
        return { display: Math.round(score).toLocaleString(), muted: false, color };
      }
      return { display: "—", muted: true };
    }
    case "raid":
      return summary.raid_summary
        ? { display: summary.raid_summary, muted: false }
        : { display: "—", muted: true };
    case "parse":
      return summary.avg_parse != null
        ? { display: `${summary.avg_parse}%`, muted: false }
        : { display: "—", muted: true };
    case "prof":
      return summary.profession_count > 0
        ? { display: String(summary.profession_count), muted: false }
        : { display: "—", muted: true };
    case "market":
      return { display: "—", muted: true };
    default:
      return { display: "—", muted: true };
  }
}

function _buildTab(tabDef, summary) {
  const { key, label } = tabDef;
  const { display, muted, color } = _tabValue(key, summary);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "mcn-stat-tab" + (key === _activeTab ? " is-active" : "");
  btn.dataset.tabKey = key;

  const valueClass = muted ? "mcn-stat-tab__value mcn-stat-tab__value--muted" : "mcn-stat-tab__value";
  const colorStyle = color ? ` style="color:${color}"` : "";

  btn.innerHTML = `
    <span class="${valueClass}"${colorStyle}>${display}</span>
    <span class="mcn-stat-tab__label">${label}</span>
  `;

  btn.addEventListener("click", () => _activateTab(key));
  return btn;
}

function _renderStrip(summary) {
  const strip = document.getElementById("mcn-stat-strip");
  if (!strip) return;
  strip.innerHTML = "";
  _TABS.forEach(def => strip.appendChild(_buildTab(def, summary)));
}

function _activateTab(key) {
  _activeTab = key;

  // Update active class on tabs
  document.querySelectorAll(".mcn-stat-tab").forEach(btn => {
    btn.classList.toggle("is-active", btn.dataset.tabKey === key);
  });

  _renderDetailArea(key);
}

function _tabTitle(key) {
  return _TABS.find(t => t.key === key)?.label || key;
}

function _renderDetailArea(key) {
  const area = document.getElementById("mcn-detail-area");
  if (!area) return;

  // All panels are placeholders in UI-1B; later phases fill these in.
  area.innerHTML = `
    <div class="mcn-detail-area__heading">${_tabTitle(key)}</div>
    <div class="mcn-detail-placeholder">${_tabTitle(key)} detail &mdash; coming soon</div>
  `;
}

async function _loadSummary(charId) {
  const strip = document.getElementById("mcn-stat-strip");

  if (_summaryCache[charId]) {
    _renderStrip(_summaryCache[charId]);
    _renderDetailArea(_activeTab);
    return;
  }

  if (strip) strip.innerHTML = '<span class="mcn-strip-loading">Loading&hellip;</span>';

  try {
    const resp = await fetch(`/api/v1/me/character/${charId}/summary`);
    const body = await resp.json().catch(() => ({}));
    if (body.ok) {
      _summaryCache[charId] = body.data;
      _renderStrip(body.data);
      _renderDetailArea(_activeTab);
    } else {
      if (strip) strip.innerHTML = '<span class="mcn-strip-loading">Could not load.</span>';
    }
  } catch {
    if (strip) strip.innerHTML = '<span class="mcn-strip-loading">Could not load.</span>';
  }
}

// ---------------------------------------------------------------------------
// Character selection
// ---------------------------------------------------------------------------

function _selectChar(charId) {
  const char = _chars.find(c => c.id === charId);
  if (!char) return;
  _selectedChar = char;
  _renderHeader(char);
  _renderGuides(char);
  _loadSummary(charId);
}

// ---------------------------------------------------------------------------
// Refresh button (delegates to existing /api/v1/me/bnet-sync)
// ---------------------------------------------------------------------------

function _initRefreshButton() {
  const btn = document.getElementById("mcn-btn-refresh");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Refreshing\u2026";
    try {
      const resp = await fetch("/api/v1/me/bnet-sync", { method: "POST" });
      const body = await resp.json().catch(() => ({}));
      if (body.redirect) {
        window.location.href = body.redirect;
        return;
      }
      // Reload page data
      window.location.reload();
    } catch {
      btn.disabled = false;
      btn.textContent = "Refresh";
    }
  });
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

async function _init() {
  _show("mcn-loading");

  try {
    const resp = await fetch("/api/v1/me/characters");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const body = await resp.json();
    if (!body.ok) throw new Error(body.error || "API error");

    const { characters, default_character_id, bnet_linked, bnet_token_expired } = body.data;
    _chars = characters || [];

    _hide("mcn-loading");

    if (!_chars.length) {
      _show("mcn-empty");
      return;
    }

    // Attach bnet_linked flag to each char for header rendering
    _chars.forEach(c => { c.bnet_linked = bnet_linked; });

    _populateSelector(_chars, default_character_id);

    _show("mcn-selector-bar");
    _show("mcn-body");

    const startId = default_character_id || _chars[0].id;
    _selectChar(startId);

    // Selector change handler
    const sel = document.getElementById("mcn-char-select");
    if (sel) {
      sel.addEventListener("change", () => _selectChar(parseInt(sel.value, 10)));
    }

    _initRefreshButton();

  } catch (err) {
    _hide("mcn-loading");
    _show("mcn-error");
    console.error("MCN load error:", err);
  }
}

document.addEventListener("DOMContentLoaded", _init);
