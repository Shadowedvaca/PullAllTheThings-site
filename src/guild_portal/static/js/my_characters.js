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
const _summaryCache    = {};  // keyed by character_id
const _craftingCache   = {};  // keyed by character_id
const _marketCache     = {};  // keyed by character_id

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

  // External profile links (RIO, WCL, Armory) — shown in the guides bar
  const extEl = document.getElementById("mcn-char-ext-links");
  if (extEl) {
    const links = [];
    if (char.raiderio_url) links.push({ href: char.raiderio_url, label: "Raider.IO" });
    if (char.wcl_url)      links.push({ href: char.wcl_url,      label: "Warcraft Logs" });
    if (char.armory_url)   links.push({ href: char.armory_url,   label: "Armory" });
    extEl.innerHTML = links.map(l =>
      `<a href="${l.href}" target="_blank" rel="noopener noreferrer" class="mcn-char-ext-link">${l.label} &#8599;</a>`
    ).join("");
  }

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

// ---------------------------------------------------------------------------
// Progression cache + fetch (used by Raid and M+ panels)
// ---------------------------------------------------------------------------

const _progressionCache = {};  // keyed by character_id

async function _fetchProgression(charId) {
  if (_progressionCache[charId]) return _progressionCache[charId];
  const resp = await fetch(`/api/v1/me/character/${charId}/progression`);
  const body = await resp.json().catch(() => ({}));
  if (body.ok) {
    _progressionCache[charId] = body.data;
    return body.data;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Raid detail panel
// ---------------------------------------------------------------------------

const _DIFF_ORDER = ["mythic", "heroic", "normal", "lfr"];
const _DIFF_LABELS = { mythic: "Mythic", heroic: "Heroic", normal: "Normal", lfr: "LFR" };

function _renderRaidDetail(area, data) {
  const bosses = data.raid_bosses || [];

  if (!bosses.length) {
    area.innerHTML = `
      <div class="mcn-detail-area__heading">Raid</div>
      <div class="mcn-prog-panel">
        <div class="mcn-detail-placeholder">No raid progress data yet.</div>
      </div>
    `;
    return;
  }

  // Group bosses by difficulty
  const byDiff = {};
  for (const b of bosses) {
    const d = b.difficulty;
    if (!byDiff[d]) byDiff[d] = [];
    byDiff[d].push(b);
  }
  const availDiffs = _DIFF_ORDER.filter(d => byDiff[d] && byDiff[d].length > 0);

  // Default to highest difficulty with any kills, or first available
  const hasDiffWithKill = availDiffs.find(d => byDiff[d].some(b => b.killed));
  let activeRaidDiff = hasDiffWithKill || availDiffs[0];

  function buildBossList(diff) {
    const rows = byDiff[diff] || [];
    return rows.map(b => `
      <div class="mcn-boss-row ${b.killed ? 'mcn-boss-row--killed' : 'mcn-boss-row--not-killed'}">
        <span class="mcn-boss-kill-icon">${b.killed ? '&#10003;' : '&#10007;'}</span>
        <span class="mcn-boss-name">${b.boss_name}</span>
      </div>
    `).join("");
  }

  function buildTabs(selected) {
    return availDiffs.map(d => {
      const killed = (byDiff[d] || []).filter(b => b.killed).length;
      const total  = (byDiff[d] || []).length;
      return `<button type="button"
        class="mcn-diff-tab${d === selected ? ' is-active' : ''}"
        data-diff="${d}">
        ${_DIFF_LABELS[d] || d}
        <span class="mcn-diff-tab__count">${killed}/${total}</span>
      </button>`;
    }).join("");
  }

  const raidName = bosses[0]?.raid_name || "Raid";

  area.innerHTML = `
    <div class="mcn-detail-area__heading">Raid</div>
    <div class="mcn-prog-panel">
      <div class="mcn-prog-raid-name">${raidName}</div>
      <div class="mcn-diff-tabs" id="mcn-raid-diff-tabs">${buildTabs(activeRaidDiff)}</div>
      <div class="mcn-boss-list" id="mcn-boss-list">${buildBossList(activeRaidDiff)}</div>
    </div>
  `;

  // Wire up tab clicks (re-render boss list)
  area.querySelectorAll(".mcn-diff-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      const diff = btn.dataset.diff;
      area.querySelectorAll(".mcn-diff-tab").forEach(b => b.classList.toggle("is-active", b.dataset.diff === diff));
      const list = area.querySelector("#mcn-boss-list");
      if (list) list.innerHTML = buildBossList(diff);
    });
  });
}

// ---------------------------------------------------------------------------
// M+ detail panel
// ---------------------------------------------------------------------------

function _mplusScoreTier(score) {
  if (score >= 2500) return "#ff44ff";
  if (score >= 2000) return "#ff8000";
  if (score >= 1500) return "#a335ee";
  if (score >= 1000) return "#0070dd";
  if (score >= 750)  return "#1eff00";
  return "#9d9d9d";
}

function _renderMplusDetail(area, data) {
  const mp = data.mythic_plus;

  if (!mp || !(mp.overall_score > 0)) {
    area.innerHTML = `
      <div class="mcn-detail-area__heading">M+</div>
      <div class="mcn-prog-panel">
        <div class="mcn-detail-placeholder">No M+ data yet.</div>
      </div>
    `;
    return;
  }

  const scoreColor = _mplusScoreTier(mp.overall_score);
  const dungeons = mp.dungeons || [];

  const dungeonRows = dungeons.length
    ? dungeons.map(d => `
        <tr class="mcn-mplus-row ${d.best_level > 0 ? '' : 'mcn-mplus-row--zero'}">
          <td class="mcn-mplus-dungeon">${d.dungeon_name}</td>
          <td class="mcn-mplus-level">${d.best_level > 0 ? `+${d.best_level}${d.best_timed ? ' <span class="mcn-mplus-timed" title="Timed">&#9201;</span>' : ''}` : '&mdash;'}</td>
          <td class="mcn-mplus-score">${d.best_score > 0 ? d.best_score.toFixed(1) : '&mdash;'}</td>
        </tr>
      `).join("")
    : `<tr><td colspan="3" class="mcn-mplus-empty">No dungeon runs recorded.</td></tr>`;

  area.innerHTML = `
    <div class="mcn-detail-area__heading">M+</div>
    <div class="mcn-prog-panel">
      <div class="mcn-mplus-score-row">
        <span class="mcn-mplus-score-label">Overall Score</span>
        <span class="mcn-mplus-score-value" style="color:${scoreColor}">${Math.round(mp.overall_score).toLocaleString()}</span>
        <span class="mcn-mplus-season-name">${mp.season_name}</span>
      </div>
      <table class="mcn-mplus-table">
        <thead>
          <tr>
            <th>Dungeon</th>
            <th>Best Key</th>
            <th>Score</th>
          </tr>
        </thead>
        <tbody>${dungeonRows}</tbody>
      </table>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Shared helpers (market + professions)
// ---------------------------------------------------------------------------

function goldStr(copper) {
  if (!copper) return "\u2014";
  const gold   = Math.floor(copper / 10000);
  const silver = Math.floor((copper % 10000) / 100);
  if (gold > 0) return `${gold.toLocaleString()}g ${silver}s`;
  return `${silver}s`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const PROFESSION_ICONS = {
  "Alchemy":        "trade_alchemy",
  "Blacksmithing":  "trade_blacksmithing",
  "Enchanting":     "trade_engraving",
  "Engineering":    "trade_engineering",
  "Herbalism":      "trade_herbalism",
  "Inscription":    "trade_inscription",
  "Jewelcrafting":  "trade_jewelcrafting",
  "Leatherworking": "trade_leatherworking",
  "Mining":         "trade_mining",
  "Skinning":       "trade_skinning",
  "Tailoring":      "trade_tailoring",
  "Cooking":        "inv_misc_food_15",
  "Fishing":        "trade_fishing",
};

// ---------------------------------------------------------------------------
// Parses detail panel
// ---------------------------------------------------------------------------

const _parsesCache = {};  // keyed by character_id

async function _fetchParsesDetail(charId) {
  if (_parsesCache[charId]) return _parsesCache[charId];
  const resp = await fetch(`/api/v1/me/character/${charId}/parses-detail`);
  const body = await resp.json().catch(() => ({}));
  if (body.ok) {
    _parsesCache[charId] = body.data;
    return body.data;
  }
  return null;
}

function _parseTierColor(pct) {
  if (pct == null) return "var(--color-text-muted)";
  if (pct >= 100) return "#e268a8";
  if (pct >= 99)  return "#e5cc80";
  if (pct >= 95)  return "#ff8000";
  if (pct >= 75)  return "#a335ee";
  if (pct >= 50)  return "#0070ff";
  if (pct >= 25)  return "#1eff00";
  return "var(--color-text-muted)";
}

function _renderParsesDetail(area, data) {
  const rows = data.raid || [];

  if (!rows.length) {
    area.innerHTML = `
      <div class="mcn-detail-area__heading">Parses</div>
      <div class="mcn-prog-panel">
        <div class="mcn-detail-placeholder">No parse data yet.</div>
      </div>
    `;
    return;
  }

  // ── Helpers ────────────────────────────────────────────────────────────
  function pctCell(val, bold) {
    if (val == null) return '&mdash;';
    const style = `color:${_parseTierColor(val)}${bold ? ';font-weight:700' : ''}`;
    return `<span style="${style}">${Math.round(val)}%</span>`;
  }

  // ── Per-boss detail table ──────────────────────────────────────────────
  function buildDetailTable() {
    const rowsHtml = rows.map(r => `<tr>
      <td class="mcn-parses-td mcn-parses-boss">${r.encounter_name}</td>
      <td class="mcn-parses-td mcn-parses-diff">${r.difficulty_label}</td>
      <td class="mcn-parses-td mcn-parses-pct">${pctCell(r.best_pct, true)}</td>
      <td class="mcn-parses-td mcn-parses-kills">${r.total_kills}</td>
      <td class="mcn-parses-td mcn-parses-avg">${pctCell(r.avg_pct, false)}</td>
      <td class="mcn-parses-td mcn-parses-dps">${r.best_dps != null ? Math.round(r.best_dps).toLocaleString() : '&mdash;'}</td>
    </tr>`).join('');
    return `<table class="mcn-parses-table">
      <thead><tr>
        <th class="mcn-parses-th mcn-parses-th--boss">Boss</th>
        <th class="mcn-parses-th">Difficulty</th>
        <th class="mcn-parses-th">Best %</th>
        <th class="mcn-parses-th">Kills</th>
        <th class="mcn-parses-th">Avg %</th>
        <th class="mcn-parses-th">Best DPS</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  }

  // ── By Difficulty summary ──────────────────────────────────────────────
  function buildDiffSummary() {
    const diffMap = {};
    for (const r of rows) {
      const key = r.difficulty_label;
      if (!diffMap[key]) diffMap[key] = { difficulty: r.difficulty, bestPcts: [], avgPcts: [], kills: 0 };
      if (r.best_pct != null) diffMap[key].bestPcts.push(r.best_pct);
      if (r.avg_pct != null)  diffMap[key].avgPcts.push(r.avg_pct);
      diffMap[key].kills += r.total_kills;
    }
    // Sort highest difficulty first (Mythic > Heroic > Normal)
    const labels = Object.keys(diffMap).sort((a, b) => diffMap[b].difficulty - diffMap[a].difficulty);
    const rowsHtml = labels.map(label => {
      const d = diffMap[label];
      const avg = arr => arr.length ? arr.reduce((s, v) => s + v, 0) / arr.length : null;
      return `<tr>
        <td class="mcn-parses-td mcn-parses-boss">${label}</td>
        <td class="mcn-parses-td mcn-parses-pct">${pctCell(avg(d.bestPcts), true)}</td>
        <td class="mcn-parses-td mcn-parses-kills">${d.kills}</td>
        <td class="mcn-parses-td mcn-parses-avg">${pctCell(avg(d.avgPcts), false)}</td>
      </tr>`;
    }).join('');
    return `<div class="mcn-parses-section-label">By Difficulty</div>
    <table class="mcn-parses-table">
      <thead><tr>
        <th class="mcn-parses-th mcn-parses-th--boss">Difficulty</th>
        <th class="mcn-parses-th">Avg Best %</th>
        <th class="mcn-parses-th">Total Kills</th>
        <th class="mcn-parses-th">Avg %</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  }

  // ── By Boss summary ────────────────────────────────────────────────────
  function buildBossSummary() {
    const bossMap = {};
    for (const r of rows) {
      const name = r.encounter_name;
      if (!bossMap[name]) bossMap[name] = { bestPct: null, avgPcts: [], kills: 0 };
      if (r.best_pct != null && (bossMap[name].bestPct == null || r.best_pct > bossMap[name].bestPct)) {
        bossMap[name].bestPct = r.best_pct;
      }
      if (r.avg_pct != null) bossMap[name].avgPcts.push(r.avg_pct);
      bossMap[name].kills += r.total_kills;
    }
    const bossNames = Object.keys(bossMap).sort();
    const rowsHtml = bossNames.map(name => {
      const b = bossMap[name];
      const avgAvg = b.avgPcts.length ? b.avgPcts.reduce((s, v) => s + v, 0) / b.avgPcts.length : null;
      return `<tr>
        <td class="mcn-parses-td mcn-parses-boss">${name}</td>
        <td class="mcn-parses-td mcn-parses-pct">${pctCell(b.bestPct, true)}</td>
        <td class="mcn-parses-td mcn-parses-kills">${b.kills}</td>
        <td class="mcn-parses-td mcn-parses-avg">${pctCell(avgAvg, false)}</td>
      </tr>`;
    }).join('');
    return `<div class="mcn-parses-section-label">By Boss</div>
    <table class="mcn-parses-table">
      <thead><tr>
        <th class="mcn-parses-th mcn-parses-th--boss">Boss</th>
        <th class="mcn-parses-th">Best %</th>
        <th class="mcn-parses-th">Total Kills</th>
        <th class="mcn-parses-th">Avg %</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  }

  area.innerHTML = `
    <div class="mcn-detail-area__heading">Parses</div>
    <div class="mcn-prog-panel">
      ${buildDetailTable()}
      ${buildDiffSummary()}
      ${buildBossSummary()}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Professions detail panel
// ---------------------------------------------------------------------------

async function _fetchCrafting(charId) {
  if (_craftingCache[charId]) return _craftingCache[charId];
  try {
    const resp = await fetch(`/api/v1/me/character/${charId}/crafting`);
    const body = await resp.json().catch(() => ({}));
    if (body.ok) {
      _craftingCache[charId] = body.data;
      return body.data;
    }
  } catch {}
  return null;
}

function _renderProfessionsDetail(area, data) {
  const craftable = data.craftable || [];

  if (craftable.length === 0) {
    area.innerHTML = `
      <div class="mcn-detail-area__heading">Professions</div>
      <div class="mcn-prog-panel">
        <div class="mcn-detail-placeholder">No profession data for this character.</div>
      </div>
    `;
    return;
  }

  // Build profession → recipe count map
  const profMap = {};
  for (const r of craftable) {
    profMap[r.profession] = (profMap[r.profession] || 0) + 1;
  }
  const profNames = Object.keys(profMap).sort();

  const profCards = profNames.map(name => {
    const slug    = PROFESSION_ICONS[name] || "trade_engineering";
    const count   = profMap[name];
    const iconUrl = `https://wow.zamimg.com/images/wow/icons/medium/${slug}.jpg`;
    return `<div class="mcn-prof-item">
      <img class="mcn-prof-icon" src="${iconUrl}" alt="${name}" loading="lazy">
      <div class="mcn-prof-name">${name}</div>
      <div class="mcn-prof-count">${count} recipe${count !== 1 ? "s" : ""}</div>
    </div>`;
  }).join("");

  // Build unique sorted expansion list for the filter dropdown
  const expansions = [...new Set(
    craftable.map(r => r.expansion_name).filter(Boolean)
  )].sort();

  const profOptions = profNames.map(p =>
    `<option value="${escHtml(p)}">${escHtml(p)}</option>`
  ).join("");
  const expOptions = expansions.map(e =>
    `<option value="${escHtml(e)}">${escHtml(e)}</option>`
  ).join("");

  const PAGE_SIZE = 15;
  let _page = 0;

  area.innerHTML = `
    <div class="mcn-detail-area__heading">Professions</div>
    <div class="mcn-prog-panel">
      <div class="mcn-prof-grid">${profCards}</div>
      <div class="mcn-parses-section-label">Recipes</div>
      <div class="mcn-prof-filters">
        <select id="mcn-prof-filter" class="mcn-filter-sel">
          <option value="">All Professions</option>${profOptions}
        </select>
        <select id="mcn-exp-filter" class="mcn-filter-sel">
          <option value="">All Expansions</option>${expOptions}
        </select>
        <input id="mcn-recipe-search" class="mcn-filter-input" type="text" placeholder="Search recipes…">
      </div>
      <table class="mcn-prof-table">
        <thead><tr><th>Profession</th><th>Expansion</th><th>Recipe</th><th></th></tr></thead>
        <tbody id="mcn-prof-tbody"></tbody>
      </table>
      <div class="mcn-prof-pagination" id="mcn-prof-pagination"></div>
    </div>
  `;

  function getFiltered() {
    const pf  = document.getElementById("mcn-prof-filter")?.value  || "";
    const ef  = document.getElementById("mcn-exp-filter")?.value   || "";
    const sf  = (document.getElementById("mcn-recipe-search")?.value || "").toLowerCase().trim();
    return craftable.filter(r => {
      if (pf && r.profession !== pf) return false;
      if (ef && r.expansion_name !== ef) return false;
      if (sf && !r.recipe_name.toLowerCase().includes(sf)) return false;
      return true;
    });
  }

  function renderTable() {
    const filtered = getFiltered();
    const total    = filtered.length;
    const maxPage  = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
    if (_page > maxPage) _page = maxPage;
    const slice    = filtered.slice(_page * PAGE_SIZE, _page * PAGE_SIZE + PAGE_SIZE);

    const tbody = document.getElementById("mcn-prof-tbody");
    if (!tbody) return;
    tbody.innerHTML = slice.map(r => {
      const exp  = escHtml(r.expansion_name || "—");
      const name = escHtml(r.recipe_name);
      const url  = r.wowhead_url || "#";
      return `<tr>
        <td class="mcn-prof-td-prof">${escHtml(r.profession)}</td>
        <td class="mcn-prof-td-exp">${exp}</td>
        <td class="mcn-prof-td-recipe">${name}</td>
        <td class="mcn-prof-td-link"><a href="${url}" target="_blank" rel="noopener noreferrer" class="mcn-prof-wh-link" title="View on Wowhead">WH</a></td>
      </tr>`;
    }).join("");

    // Pagination controls
    const pg = document.getElementById("mcn-prof-pagination");
    if (!pg) return;
    if (total <= PAGE_SIZE) {
      pg.innerHTML = `<span class="mcn-prof-pg-info">${total} recipe${total !== 1 ? "s" : ""}</span>`;
    } else {
      const start = _page * PAGE_SIZE + 1;
      const end   = Math.min((_page + 1) * PAGE_SIZE, total);
      pg.innerHTML = `
        <button class="mcn-prof-pg-btn" id="mcn-pg-prev" ${_page === 0 ? "disabled" : ""}>&#8592; Prev</button>
        <span class="mcn-prof-pg-info">${start}–${end} of ${total}</span>
        <button class="mcn-prof-pg-btn" id="mcn-pg-next" ${_page >= maxPage ? "disabled" : ""}>Next &#8594;</button>
      `;
      document.getElementById("mcn-pg-prev")?.addEventListener("click", () => { _page--; renderTable(); });
      document.getElementById("mcn-pg-next")?.addEventListener("click", () => { _page++; renderTable(); });
    }
  }

  function resetAndRender() { _page = 0; renderTable(); }

  renderTable();
  document.getElementById("mcn-prof-filter")?.addEventListener("change", resetAndRender);
  document.getElementById("mcn-exp-filter")?.addEventListener("change", resetAndRender);
  document.getElementById("mcn-recipe-search")?.addEventListener("input", resetAndRender);
}

// ---------------------------------------------------------------------------
// Market detail panel
// ---------------------------------------------------------------------------

async function _fetchMarket(charId) {
  if (_marketCache[charId]) return _marketCache[charId];
  try {
    const resp = await fetch(`/api/v1/me/character/${charId}/market`);
    const body = await resp.json().catch(() => ({}));
    if (body.ok) {
      _marketCache[charId] = body.data;
      return body.data;
    }
  } catch {}
  return null;
}

function _updateMarketTabCount(count) {
  const btn = document.querySelector('.mcn-stat-tab[data-tab-key="market"]');
  if (!btn) return;
  const valEl = btn.querySelector(".mcn-stat-tab__value");
  if (valEl) {
    valEl.textContent = String(count);
    valEl.classList.remove("mcn-stat-tab__value--muted");
  }
}

function _renderMarketDetail(area, data) {
  const { prices, available, last_updated } = data;

  if (!available || !prices || prices.length === 0) {
    area.innerHTML = `
      <div class="mcn-detail-area__heading">Market</div>
      <div class="mcn-prog-panel">
        <div class="mcn-detail-placeholder">No market data available for your realm yet.</div>
      </div>
    `;
    return;
  }

  _updateMarketTabCount(prices.length);

  const rows = prices.map(item => {
    const realmCls  = item.is_realm_specific ? " mcn-market-row--realm" : "";
    const realmFlag = item.is_realm_specific ? '<span class="mcn-market-realm-flag">*</span>' : "";
    const wowheadName = item.item_name.replace(/ /g, "+").replace(/'/g, "%27");
    const qty = item.quantity_available ? item.quantity_available.toLocaleString() : "\u2014";
    return `<tr class="${realmCls}">
      <td class="mcn-market-name">
        <span class="mcn-market-cat mcn-market-cat--${item.category}">${item.category}</span>
        <a href="https://www.wowhead.com/search?q=${wowheadName}" target="_blank" rel="noopener noreferrer" class="mcn-market-item-link">${item.item_name}</a>${realmFlag}
      </td>
      <td class="mcn-market-price">${goldStr(item.min_buyout)}</td>
      <td class="mcn-market-qty">${qty}</td>
    </tr>`;
  }).join("");

  const hasRealmSpecific = prices.some(p => p.is_realm_specific);
  const footnote = hasRealmSpecific
    ? '<p class="mcn-market-footnote">* Realm-specific auction price.</p>'
    : "";

  let updatedStr = "";
  if (last_updated) {
    const d = new Date(last_updated);
    updatedStr = `<span class="mcn-market-updated">Updated ${d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</span>`;
  }

  area.innerHTML = `
    <div class="mcn-market-header-row">
      <div class="mcn-detail-area__heading">Market</div>
      ${updatedStr}
    </div>
    <div class="mcn-prog-panel">
      <table class="mcn-market-table">
        <thead><tr><th>Item</th><th>Min Price</th><th>Available</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${footnote}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Detail area router
// ---------------------------------------------------------------------------

function _renderDetailArea(key) {
  const area = document.getElementById("mcn-detail-area");
  if (!area) return;

  if (key === "gear") {
    const charId = _selectedChar?.id;
    if (charId) {
      _gpActivateTab(charId);
    } else {
      area.innerHTML = '<div class="mcn-detail-placeholder">Select a character to view gear plan</div>';
    }
    return;
  }

  if (key === "raid" || key === "mplus") {
    const charId = _selectedChar?.id;
    if (!charId) {
      area.innerHTML = '<div class="mcn-detail-placeholder">Select a character</div>';
      return;
    }
    area.innerHTML = '<div class="mcn-detail-placeholder">Loading&hellip;</div>';
    _fetchProgression(charId).then(data => {
      if (!data) {
        area.innerHTML = '<div class="mcn-detail-placeholder">Could not load progression data.</div>';
        return;
      }
      if (key === "raid")  _renderRaidDetail(area, data);
      if (key === "mplus") _renderMplusDetail(area, data);
    });
    return;
  }

  if (key === "parse") {
    const charId = _selectedChar?.id;
    if (!charId) {
      area.innerHTML = '<div class="mcn-detail-placeholder">Select a character</div>';
      return;
    }
    area.innerHTML = '<div class="mcn-detail-placeholder">Loading&hellip;</div>';
    _fetchParsesDetail(charId).then(data => {
      if (!data) {
        area.innerHTML = '<div class="mcn-detail-placeholder">Could not load parse data.</div>';
        return;
      }
      _renderParsesDetail(area, data);
    });
    return;
  }

  if (key === "prof") {
    const charId = _selectedChar?.id;
    if (!charId) {
      area.innerHTML = '<div class="mcn-detail-placeholder">Select a character</div>';
      return;
    }
    area.innerHTML = '<div class="mcn-detail-placeholder">Loading&hellip;</div>';
    _fetchCrafting(charId).then(data => {
      if (!data) {
        area.innerHTML = '<div class="mcn-detail-placeholder">Could not load profession data.</div>';
        return;
      }
      _renderProfessionsDetail(area, data);
    });
    return;
  }

  if (key === "market") {
    const charId = _selectedChar?.id;
    if (!charId) {
      area.innerHTML = '<div class="mcn-detail-placeholder">Select a character</div>';
      return;
    }
    area.innerHTML = '<div class="mcn-detail-placeholder">Loading&hellip;</div>';
    _fetchMarket(charId).then(data => {
      if (!data) {
        area.innerHTML = '<div class="mcn-detail-placeholder">Could not load market data.</div>';
        return;
      }
      _renderMarketDetail(area, data);
    });
    return;
  }
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
  _gpResetPaperdolls();   // reset to placeholder; gear loads on gear-tab activation
  _gpCloseDrawer();
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

// =============================================================================
// GEAR PLAN — Phase UI-1C
// Two-box paperdoll slot cards, gear controls, drawer, SimC modal
// =============================================================================

// ── Constants ─────────────────────────────────────────────────────────────────

const GP_LEFT_BODY_SLOTS   = ['head','neck','shoulder','back','chest','wrist'];
const GP_LEFT_WEAPON_SLOTS = ['main_hand','off_hand'];
const GP_LEFT_SLOTS        = [...GP_LEFT_BODY_SLOTS, ...GP_LEFT_WEAPON_SLOTS];
const GP_RIGHT_SLOTS       = ['hands','waist','legs','feet','ring_1','ring_2','trinket_1','trinket_2'];
const GP_INACTIVE_SLOTS    = new Set();

// Ordered list of all slots for the gear table (WoW equipment order)
const GP_ALL_SLOTS = [
  'head','neck','shoulder','back','chest','wrist',
  'hands','waist','legs','feet',
  'ring_1','ring_2','trinket_1','trinket_2',
  'main_hand','off_hand',
];

const GP_SLOT_LABELS = {
  head:'Head', neck:'Neck', shoulder:'Shoulder', back:'Back',
  chest:'Chest', shirt:'Shirt', tabard:'Tabard', wrist:'Wrist',
  hands:'Hands', waist:'Waist', legs:'Legs', feet:'Feet',
  ring_1:'Ring 1', ring_2:'Ring 2',
  trinket_1:'Trinket 1', trinket_2:'Trinket 2',
  main_hand:'Main Hand', off_hand:'Off Hand',
};

// Fallback track colors before API data loads
const GP_TRACK_FALLBACK = { V: '#22c55e', C: '#3b82f6', H: '#a855f7', M: '#f97316' };

// ── Per-character cache ────────────────────────────────────────────────────────

const _gpCache = {};   // charId → API data (plan, slots, bisSources, heroTalents, trackColors)
let _gpOpenSlot = null;

// ── Helpers ───────────────────────────────────────────────────────────────────

function _gpEsc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function _gpFetch(url, opts) {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    ...(opts || {}),
  });
  try { return await r.json(); }
  catch { return { ok: false, error: `HTTP ${r.status}` }; }
}

function _gpColor(t, tc) {
  return (tc && tc[t]) || GP_TRACK_FALLBACK[t] || '#888';
}

function _gpPill(t, tc) {
  const c = _gpColor(t, tc);
  return `<span class="mcn-track-pill" style="background:${_gpEsc(c)}">${_gpEsc(t)}</span>`;
}

// Build grouped source HTML: one block per instance, bosses indented below.
// Groups by display_name (server-computed from source_config).
// track_label and display_name are both computed server-side.
function _gpSourceHtml(sources, groupCls, instCls, bossCls) {
  if (!sources.length) return '';
  const instMap = new Map();
  for (const src of sources) {
    const key = src.display_name || src.instance_name || '';
    if (!instMap.has(key)) {
      instMap.set(key, { trackLabel: src.track_label || '', bosses: [] });
    }
    instMap.get(key).bosses.push(src.encounter_name);
  }
  return [...instMap.entries()].map(([inst, { trackLabel, bosses }]) => {
    const header = inst
      ? `${_gpEsc(inst)}${trackLabel ? ` (${trackLabel})` : ''}`
      : (trackLabel || '');
    const instLine = `<div class="${instCls}">${header}</div>`;
    const bossLines = bosses.map(b => `<div class="${bossCls}">${_gpEsc(b)}</div>`).join('');
    return `<div class="${groupCls}">${instLine}${bossLines}</div>`;
  }).join('');
}

// ── Status ─────────────────────────────────────────────────────────────────────

function _gpShowStatus(msg, type) {
  const el = document.getElementById('mcn-gp-status');
  if (!el) return;
  el.textContent = msg;
  el.className   = `mcn-gp-status mcn-gp-status--${type}`;
  el.hidden      = false;
}

function _gpClearStatus() {
  const el = document.getElementById('mcn-gp-status');
  if (el) el.hidden = true;
}

// ── Paperdoll placeholder reset ───────────────────────────────────────────────

function _gpResetPaperdolls() {
  const leftEl  = document.getElementById('mcn-left-paperdoll');
  const rightEl = document.getElementById('mcn-right-paperdoll');
  if (leftEl) {
    leftEl.innerHTML = '<div class="mcn-paperdoll__placeholder">'
      + GP_LEFT_BODY_SLOTS.map(s => `<span class="mcn-paperdoll__slot-ph" title="${GP_SLOT_LABELS[s]}"></span>`).join('')
      + '<div class="mcn-paperdoll__weapon-sep"></div>'
      + GP_LEFT_WEAPON_SLOTS.map(s => `<span class="mcn-paperdoll__slot-ph" title="${GP_SLOT_LABELS[s]}"></span>`).join('')
      + '</div>';
  }
  if (rightEl) {
    rightEl.innerHTML = '<div class="mcn-paperdoll__placeholder">'
      + GP_RIGHT_SLOTS.map(s => `<span class="mcn-paperdoll__slot-ph" title="${GP_SLOT_LABELS[s]}"></span>`).join('')
      + '</div>';
  }
}


// ── Slot card builder — two-box design ────────────────────────────────────────

function _gpBuildSlotCard(slotKey, sd, tc) {
  const isInactive = GP_INACTIVE_SLOTS.has(slotKey);
  sd = sd || {};
  const eq       = sd.equipped;
  const desired  = sd.desired;
  const upgrades = isInactive ? [] : (sd.upgrade_tracks || []);
  const bisRecs  = isInactive ? [] : (sd.bis_recommendations || []);

  const isBis       = !isInactive && !!sd.is_bis;
  const isBisMythic = isBis && eq?.quality_track === 'M';

  // Goal: explicit desired first, then first BIS rec
  const primaryBis = bisRecs[0] || null;
  const goalItem   = !isInactive ? (desired || primaryBis) : null;
  const showGoal   = goalItem && (!eq || goalItem.blizzard_item_id !== eq?.blizzard_item_id);

  const card = document.createElement('div');
  card.className  = 'mcn-slot-card' + (isInactive ? ' is-inactive' : '');
  card.dataset.slot = slotKey;
  if (!isInactive) {
    if (isBis)            card.classList.add('is-bis');
    else if (sd.needs_upgrade) card.classList.add('needs-upgrade');
    if (_gpOpenSlot === slotKey) card.classList.add('is-open');
    card.addEventListener('click', () => _gpSelectSlotInCenter(slotKey));
  }

  // Slot label row
  const label = document.createElement('div');
  label.className = 'mcn-slot-card__label';
  label.textContent = GP_SLOT_LABELS[slotKey] || slotKey;
  card.appendChild(label);

  // Two-box row
  const boxes = document.createElement('div');
  boxes.className = 'mcn-slot-card__boxes';

  // — Upgrade box —
  const uBox = document.createElement('div');
  uBox.className = 'mcn-slot-card__upgrade';

  if (isInactive) {
    uBox.appendChild(Object.assign(document.createElement('div'), { className: 'mcn-slot-icon--no-goal' }));
  } else if (isBisMythic) {
    uBox.innerHTML = `<div class="mcn-slot-icon mcn-slot-icon--bis-mythic" title="BIS at Mythic track">
      <svg viewBox="0 0 24 24" fill="none" stroke="#4ade80" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="20 6 9 17 4 12"/>
      </svg></div>`;
  } else if (isBis) {
    uBox.innerHTML = `<div class="mcn-slot-icon mcn-slot-icon--bis" title="BIS">
      <svg viewBox="0 0 24 24" fill="#d4a84b" stroke="#b8922e" stroke-width="0.5" stroke-linejoin="round">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
      </svg></div>`;
    if (upgrades.length) {
      const pills = document.createElement('div');
      pills.className = 'mcn-track-pills';
      pills.innerHTML = upgrades.map(t => _gpPill(t, tc)).join('');
      uBox.appendChild(pills);
    }
  } else if (showGoal && goalItem.icon_url) {
    const qc = goalItem.quality_track ? _gpColor(goalItem.quality_track, tc)
      : (upgrades[0] ? _gpColor(upgrades[0], tc) : null);
    const bs = qc && qc !== '#888' ? ` style="border-color:${qc};box-shadow:0 0 4px ${qc}55"` : '';
    uBox.innerHTML = `<a href="https://www.wowhead.com/item=${goalItem.blizzard_item_id}" target="_blank" rel="noopener noreferrer" class="mcn-slot-icon-link">
      <img class="mcn-slot-icon" src="${_gpEsc(goalItem.icon_url)}" alt="" title="${_gpEsc(goalItem.item_name || goalItem.name || '')}"${bs} loading="lazy">
    </a>`;
    if (upgrades.length) {
      const pills = document.createElement('div');
      pills.className = 'mcn-track-pills';
      pills.innerHTML = upgrades.map(t => _gpPill(t, tc)).join('');
      uBox.appendChild(pills);
    }
  } else if (showGoal) {
    const el = document.createElement('div');
    el.className   = 'mcn-slot-icon mcn-slot-icon--empty';
    el.title       = goalItem.item_name || goalItem.name || 'Goal';
    el.textContent = '\u2192';
    uBox.appendChild(el);
    if (upgrades.length) {
      const pills = document.createElement('div');
      pills.className = 'mcn-track-pills';
      pills.innerHTML = upgrades.map(t => _gpPill(t, tc)).join('');
      uBox.appendChild(pills);
    }
  } else {
    uBox.appendChild(Object.assign(document.createElement('div'), {
      className: 'mcn-slot-icon mcn-slot-icon--no-goal',
      title: 'No goal set',
    }));
    if (upgrades.length) {
      const pills = document.createElement('div');
      pills.className = 'mcn-track-pills';
      pills.innerHTML = upgrades.map(t => _gpPill(t, tc)).join('');
      uBox.appendChild(pills);
    }
  }

  // — Equipped box —
  const eBox = document.createElement('div');
  eBox.className = 'mcn-slot-card__equipped';

  if (eq && eq.blizzard_item_id) {
    const qc = eq.quality_track ? _gpColor(eq.quality_track, tc) : (eq.is_crafted ? '#c0a060' : null);
    const bs = qc && qc !== '#888' ? ` style="border-color:${qc};box-shadow:0 0 4px ${qc}55"` : '';
    if (eq.icon_url) {
      eBox.innerHTML = `<a href="https://www.wowhead.com/item=${eq.blizzard_item_id}" target="_blank" rel="noopener noreferrer" class="mcn-slot-icon-link">
        <img class="mcn-slot-icon" src="${_gpEsc(eq.icon_url)}" alt="" title="${_gpEsc(eq.item_name || '')}"${bs} loading="lazy">
      </a>
      <div class="mcn-slot-card__ilvl">${eq.item_level || ''}</div>`;
    } else {
      eBox.innerHTML = `<div class="mcn-slot-icon mcn-slot-icon--empty" title="${_gpEsc(eq.item_name || '')}">${_gpEsc((GP_SLOT_LABELS[slotKey] || slotKey)[0])}</div>
      <div class="mcn-slot-card__ilvl">${eq.item_level || ''}</div>`;
    }
  } else {
    eBox.appendChild(Object.assign(document.createElement('div'), { className: 'mcn-slot-icon mcn-slot-icon--no-goal' }));
  }

  boxes.appendChild(uBox);
  boxes.appendChild(eBox);
  card.appendChild(boxes);
  return card;
}

// ── Render paperdoll columns ───────────────────────────────────────────────────

function _gpRenderPaperdolls(slots, tc) {
  const leftEl  = document.getElementById('mcn-left-paperdoll');
  const rightEl = document.getElementById('mcn-right-paperdoll');
  if (leftEl) {
    leftEl.innerHTML = '';
    GP_LEFT_BODY_SLOTS.forEach(k => leftEl.appendChild(_gpBuildSlotCard(k, slots[k], tc)));
    // Weapon separator — visual break between body and weapon slots
    const sep = document.createElement('div');
    sep.className = 'mcn-paperdoll__weapon-sep';
    leftEl.appendChild(sep);
    GP_LEFT_WEAPON_SLOTS.forEach(k => leftEl.appendChild(_gpBuildSlotCard(k, slots[k], tc)));
  }
  if (rightEl) {
    rightEl.innerHTML = '';
    GP_RIGHT_SLOTS.forEach(k => rightEl.appendChild(_gpBuildSlotCard(k, slots[k], tc)));
  }
  if (window.$WowheadPower) window.$WowheadPower.refreshLinks();
}

// ── Render weapons in center panel ────────────────────────────────────────────

function _gpRenderWeapons(slots, tc) {
  const el = document.getElementById('mcn-gp-weapons');
  if (!el) return;
  el.innerHTML = '';
  GP_WEAPON_SLOTS.forEach(k => el.appendChild(_gpBuildSlotCard(k, slots[k], tc)));
  if (window.$WowheadPower) window.$WowheadPower.refreshLinks();
}

// ── Gear table (Option C) — full-width slot table ─────────────────────────────

function _gpRenderGearTable(slots, tc) {
  const allSlots = slots || {};

  const hasAnyData = GP_ALL_SLOTS.some(k => {
    const sd = allSlots[k];
    return sd && (sd.equipped?.blizzard_item_id || sd.desired?.blizzard_item_id || (sd.bis_recommendations || []).length > 0);
  });

  if (!hasAnyData) {
    return `<div class="mcn-gear-table-empty">
      No gear data yet. Use <strong>Sync Gear</strong> to load your equipped items,
      then <strong>Fill BIS</strong> to set goal items.
    </div>`;
  }

  const rows = GP_ALL_SLOTS.map(slotKey => {
    const sd      = allSlots[slotKey] || {};
    const eq      = sd.equipped;
    const desired = sd.desired;
    const bisRecs = sd.bis_recommendations || [];
    const sources = sd.item_sources        || [];
    const upgrades = sd.upgrade_tracks     || [];
    const isBis       = !!sd.is_bis;
    const isBisMythic = isBis && eq?.quality_track === 'M';

    // Row class
    let rowClass = 'mcn-gt__row';
    if (isBis)           rowClass += ' mcn-gt__row--bis';
    else if (sd.needs_upgrade) rowClass += ' mcn-gt__row--upgrade';

    // ── Equipped cell ──────────────────────────────────────────────────────
    let equippedHtml;
    if (eq && eq.blizzard_item_id) {
      const qc = eq.quality_track ? _gpColor(eq.quality_track, tc) : null;
      const iconBs = qc ? ` style="border-color:${_gpEsc(qc)}"` : '';
      const badge  = eq.quality_track
        ? `<span class="mcn-track-pill" style="background:${_gpEsc(qc)}">${_gpEsc(eq.quality_track)}</span>`
        : '';
      const icon = eq.icon_url
        ? `<a href="https://www.wowhead.com/item=${eq.blizzard_item_id}" target="_blank" rel="noopener noreferrer">
             <img class="mcn-gt__icon" src="${_gpEsc(eq.icon_url)}" alt="" loading="lazy"${iconBs}>
           </a>`
        : '';
      const nameColor = qc ? ` style="color:${_gpEsc(qc)}"` : '';
      equippedHtml = `<div class="mcn-gt__item">
        ${icon}
        <div class="mcn-gt__item-info">
          <a href="https://www.wowhead.com/item=${eq.blizzard_item_id}" target="_blank" rel="noopener noreferrer"
             class="mcn-gt__name"${nameColor}>${_gpEsc(eq.item_name || 'Unknown')}</a>
          <div class="mcn-gt__meta">
            ${eq.item_level ? `<span class="mcn-gt__ilvl">${eq.item_level}</span>` : ''}
            ${badge}
          </div>
        </div>
      </div>`;
    } else {
      equippedHtml = '<span class="mcn-gt__empty">&mdash;</span>';
    }

    // ── Goal cell ──────────────────────────────────────────────────────────
    let goalHtml;
    if (isBisMythic) {
      goalHtml = `<span class="mcn-gt__bis-check" title="BIS at Mythic track">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#4ade80" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        BIS
      </span>`;
    } else if (isBis) {
      goalHtml = `<span class="mcn-gt__bis-star" title="BIS">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="#d4a84b" stroke="#b8922e" stroke-width="0.5" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
        BIS
      </span>`;
    } else {
      const goalItem = desired || (bisRecs.length ? bisRecs[0] : null);
      if (goalItem && goalItem.blizzard_item_id) {
        const icon = goalItem.icon_url
          ? `<a href="https://www.wowhead.com/item=${goalItem.blizzard_item_id}" target="_blank" rel="noopener noreferrer">
               <img class="mcn-gt__icon" src="${_gpEsc(goalItem.icon_url)}" alt="" loading="lazy">
             </a>`
          : '';
        goalHtml = `<div class="mcn-gt__item">
          ${icon}
          <a href="https://www.wowhead.com/item=${goalItem.blizzard_item_id}" target="_blank" rel="noopener noreferrer"
             class="mcn-gt__name">${_gpEsc(goalItem.item_name || goalItem.name || 'Unknown')}</a>
        </div>`;
      } else {
        goalHtml = '<span class="mcn-gt__empty">&mdash;</span>';
      }
    }

    // ── Source cell ────────────────────────────────────────────────────────
    const craftedSrc = sd.crafted_source || null;
    let sourceHtml;
    if (craftedSrc) {
      const ct = craftedSrc.track || 'H';
      const cc = _gpColor(ct, tc);
      sourceHtml = `<div class="mcn-gt__crafted-source">
        <span class="mcn-gt__crafted-label">Crafted Item</span>
        <span class="mcn-track-pill" style="background:${_gpEsc(cc)}">${_gpEsc(ct)}</span>
      </div>`;
    } else if (sources.length) {
      sourceHtml = _gpSourceHtml(sources, 'mcn-gt__source-group', 'mcn-gt__source-inst', 'mcn-gt__source-boss');
    } else {
      sourceHtml = '<span class="mcn-gt__empty">&mdash;</span>';
    }

    // ── Upgrades cell ──────────────────────────────────────────────────────
    let upgradesHtml;
    if (isBisMythic) {
      upgradesHtml = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#4ade80" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" title="Mythic BIS"><polyline points="20 6 9 17 4 12"/></svg>`;
    } else if (upgrades.length) {
      upgradesHtml = `<div class="mcn-track-pills">${upgrades.map(t => _gpPill(t, tc)).join('')}</div>`;
    } else {
      upgradesHtml = '<span class="mcn-gt__empty">&mdash;</span>';
    }

    return `<tr class="${rowClass}" onclick="_gpSelectSlotInCenter('${slotKey}')" style="cursor:pointer">
      <td class="mcn-gt__slot-cell">${_gpEsc(GP_SLOT_LABELS[slotKey] || slotKey)}</td>
      <td class="mcn-gt__equipped-cell">${equippedHtml}</td>
      <td class="mcn-gt__goal-cell">${goalHtml}</td>
      <td class="mcn-gt__source-cell">${sourceHtml}</td>
      <td class="mcn-gt__upgrades-cell">${upgradesHtml}</td>
    </tr>`;
  }).join('');

  return `<div class="mcn-gear-table-wrap">
    <table class="mcn-gear-table">
      <thead>
        <tr>
          <th class="mcn-gt__slot-cell">Slot</th>
          <th class="mcn-gt__equipped-cell">Equipped</th>
          <th class="mcn-gt__goal-cell">Goal</th>
          <th class="mcn-gt__source-cell">Source</th>
          <th class="mcn-gt__upgrades-cell">Upgrades</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

// ── Render gear controls in center detail area ────────────────────────────────

function _gpRenderCenterPanel(data) {
  const area = document.getElementById('mcn-detail-area');
  if (!area) return;

  const plan        = data.plan;
  const bisSources  = data.bis_sources  || [];
  const heroTalents = data.hero_talents || [];

  const htOpts = ['<option value="">\u2014 Any \u2014</option>']
    .concat((heroTalents || []).map(ht =>
      `<option value="${ht.id}"${plan?.hero_talent_id === ht.id ? ' selected' : ''}>${_gpEsc(ht.name)}</option>`
    )).join('');

  const srcOpts = (bisSources || []).map(s =>
    `<option value="${s.id}"${plan?.bis_source_id === s.id ? ' selected' : ''}>${_gpEsc(s.name)}</option>`
  ).join('');

  area.innerHTML = `
    <div id="mcn-gp-slot-detail" hidden></div>
    <div class="mcn-detail-area__heading">Gear Plan</div>
    <div class="mcn-gear-controls">
      <div class="mcn-gear-ctrl-row">
        <label class="mcn-gear-label">Hero Talent</label>
        <select id="mcn-gp-ht-sel" class="mcn-gear-select">${htOpts}</select>
        <label class="mcn-gear-label">Source</label>
        <select id="mcn-gp-src-sel" class="mcn-gear-select">${srcOpts}</select>
      </div>
      <div class="mcn-gear-actions">
        <button id="mcn-gp-btn-sync"   class="btn btn-secondary btn-sm" type="button">Sync Gear</button>
        <button id="mcn-gp-btn-fill"   class="btn btn-primary btn-sm"   type="button">Fill BIS</button>
        <button id="mcn-gp-btn-import" class="btn btn-secondary btn-sm" type="button">Import SimC</button>
        <button id="mcn-gp-btn-export" class="btn btn-secondary btn-sm" type="button">Export SimC</button>
        <button id="mcn-gp-btn-reset"  class="btn btn-danger btn-sm"    type="button">Reset Plan</button>
      </div>
    </div>
    <div id="mcn-gp-status" class="mcn-gp-status" hidden></div>
    ${_gpRenderGearTable(data.slots, data.track_colors)}
  `;

  // If a slot is currently selected, re-populate its detail panel
  if (_gpOpenSlot) {
    const sd = data.slots?.[_gpOpenSlot] || {};
    _gpPopulateSlotDetail(_gpOpenSlot, sd, data.track_colors || {});
    document.querySelectorAll('.mcn-slot-card').forEach(c => {
      c.classList.toggle('is-open', c.dataset.slot === _gpOpenSlot);
    });
  }

  // Wire selects + buttons
  document.getElementById('mcn-gp-ht-sel')  ?.addEventListener('change', _gpOnConfigChange);
  document.getElementById('mcn-gp-src-sel') ?.addEventListener('change', _gpOnConfigChange);
  document.getElementById('mcn-gp-btn-sync')  ?.addEventListener('click', _gpOnSyncGear);
  document.getElementById('mcn-gp-btn-fill')  ?.addEventListener('click', _gpOnPopulate);
  document.getElementById('mcn-gp-btn-import')?.addEventListener('click', _gpShowSimcModal);
  document.getElementById('mcn-gp-btn-export')?.addEventListener('click', _gpOnExportSimc);
  document.getElementById('mcn-gp-btn-reset') ?.addEventListener('click', _gpOnDeletePlan);

  // Wire SimC modal once
  const modal = document.getElementById('mcn-simc-modal');
  if (modal && !modal._gpWired) {
    modal._gpWired = true;
    document.getElementById('mcn-simc-close') ?.addEventListener('click', _gpHideSimcModal);
    document.getElementById('mcn-simc-cancel')?.addEventListener('click', _gpHideSimcModal);
    document.getElementById('mcn-simc-submit')?.addEventListener('click', _gpOnSimcImport);
    modal.querySelector('.mcn-modal__backdrop')?.addEventListener('click', _gpHideSimcModal);
  }

  // Wire drawer close
  const drawerClose = document.getElementById('mcn-gp-drawer-close');
  if (drawerClose && !drawerClose._gpWired) {
    drawerClose._gpWired = true;
    drawerClose.addEventListener('click', _gpCloseDrawer);
  }
}

// ── Slot selection — routes paperdoll clicks into center panel ────────────────

function _gpSelectSlotInCenter(slotKey) {
  // Toggle: clicking the open slot closes it
  if (_gpOpenSlot === slotKey) {
    window.mcnGpCloseSlotDetail();
    return;
  }

  _gpOpenSlot = slotKey;

  // If gear tab isn't active, activate it.
  // _gpRenderCenterPanel (called async inside) will see _gpOpenSlot and populate the panel.
  if (_activeTab !== 'gear') {
    _activateTab('gear');
    return;
  }

  // Gear tab already showing — update slot detail in-place
  _gpUpdateSlotDetail(slotKey);
}

function _gpUpdateSlotDetail(slotKey) {
  const charId = _selectedChar?.id;
  const data   = charId ? _gpCache[charId] : null;
  if (!data) return;
  const sd = data.slots?.[slotKey] || {};
  _gpPopulateSlotDetail(slotKey, sd, data.track_colors || {});
  document.querySelectorAll('.mcn-slot-card').forEach(c => {
    c.classList.toggle('is-open', c.dataset.slot === slotKey);
  });
}

function _gpPopulateSlotDetail(slotKey, sd, tc) {
  const el = document.getElementById('mcn-gp-slot-detail');
  if (!el) return;
  el.hidden = false;
  el.innerHTML = `
    <div class="mcn-slot-detail__header">
      <span class="mcn-slot-detail__title">${_gpEsc(GP_SLOT_LABELS[slotKey] || slotKey)}</span>
      <button class="mcn-slot-detail__close" type="button" onclick="mcnGpCloseSlotDetail()">&times;</button>
    </div>
    <div class="mcn-slot-detail__body mcn-drawer__body">${_gpRenderDrawerBody(slotKey, sd, tc)}</div>
  `;
}

window.mcnGpCloseSlotDetail = function() {
  _gpOpenSlot = null;
  const el = document.getElementById('mcn-gp-slot-detail');
  if (el) el.hidden = true;
  document.querySelectorAll('.mcn-slot-card').forEach(c => c.classList.remove('is-open'));
};

// ── Load gear plan ─────────────────────────────────────────────────────────────

async function _gpLoadPlan(charId, forceReload) {
  if (!forceReload && _gpCache[charId]) {
    const d = _gpCache[charId];
    _gpRenderPaperdolls(d.slots, d.track_colors);
    _gpRenderCenterPanel(d);
    return;
  }

  const area = document.getElementById('mcn-detail-area');
  if (area) area.innerHTML = '<div class="mcn-detail-placeholder">Loading gear plan\u2026</div>';

  try {
    const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}`);
    if (!resp.ok) throw new Error(resp.error || 'Failed to load gear plan');

    _gpCache[charId] = resp.data;

    _gpRenderPaperdolls(resp.data.slots, resp.data.track_colors);
    _gpRenderCenterPanel(resp.data);

  } catch (err) {
    const area2 = document.getElementById('mcn-detail-area');
    if (area2) area2.innerHTML = `<div class="mcn-detail-placeholder" style="color:#f87171">Could not load gear plan: ${_gpEsc(err.message)}</div>`;
  }
}

async function _gpActivateTab(charId) {
  await _gpLoadPlan(charId);
}

async function _gpReload() {
  const charId = _selectedChar?.id;
  if (!charId) return;
  delete _gpCache[charId];
  await _gpLoadPlan(charId, true);
}

// ── Action handlers ────────────────────────────────────────────────────────────

async function _gpOnConfigChange() {
  const charId = _selectedChar?.id;
  if (!charId) return;
  const htSel  = document.getElementById('mcn-gp-ht-sel');
  const srcSel = document.getElementById('mcn-gp-src-sel');
  const htId   = htSel?.value  ? parseInt(htSel.value,  10) : null;
  const srcId  = srcSel?.value ? parseInt(srcSel.value, 10) : null;
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/config`, {
    method: 'PATCH',
    body: JSON.stringify({ hero_talent_id: htId, bis_source_id: srcId }),
  });
  if (resp.ok) { await _gpReload(); }
  else _gpShowStatus(resp.error || 'Config update failed', 'err');
}

async function _gpOnSyncGear() {
  const charId = _selectedChar?.id;
  if (!charId) return;
  _gpShowStatus('Syncing equipped gear\u2026', 'info');
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/sync-equipment`, { method: 'POST' });
  if (resp.ok) {
    _gpShowStatus('Gear synced \u2014 reloading\u2026', 'ok');
    setTimeout(() => _gpReload(), 800);
  } else {
    _gpShowStatus(resp.error || 'Sync failed', 'err');
  }
}

async function _gpOnPopulate() {
  const charId = _selectedChar?.id;
  if (!charId) return;
  const htSel  = document.getElementById('mcn-gp-ht-sel');
  const srcSel = document.getElementById('mcn-gp-src-sel');
  const htId   = htSel?.value  ? parseInt(htSel.value,  10) : null;
  const srcId  = srcSel?.value ? parseInt(srcSel.value, 10) : null;
  _gpShowStatus('Filling unlocked slots from BIS\u2026', 'info');
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/populate`, {
    method: 'POST',
    body: JSON.stringify({ source_id: srcId, hero_talent_id: htId }),
  });
  if (resp.ok) {
    _gpShowStatus(`${resp.data?.populated || 0} slots filled`, 'ok');
    await _gpReload();
  } else {
    _gpShowStatus(resp.error || 'Populate failed', 'err');
  }
}

async function _gpOnDeletePlan() {
  if (!confirm('Reset this gear plan? All goal items will be cleared.')) return;
  const charId = _selectedChar?.id;
  if (!charId) return;
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}`, { method: 'DELETE' });
  if (resp.ok) {
    _gpShowStatus('Plan reset', 'ok');
    _gpCloseDrawer();
    await _gpReload();
  } else {
    _gpShowStatus(resp.error || 'Failed', 'err');
  }
}

async function _gpOnExportSimc() {
  const charId = _selectedChar?.id;
  if (!charId) return;
  _gpShowStatus('Generating SimC\u2026', 'info');
  try {
    const resp = await fetch(`/api/v1/me/gear-plan/${charId}/export-simc`, { credentials: 'include' });
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      _gpShowStatus(d.error || 'Export failed', 'err');
      return;
    }
    const text = await resp.text();
    const a = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(new Blob([text], { type: 'text/plain' })),
      download: 'gear_plan.simc',
    });
    a.click();
    URL.revokeObjectURL(a.href);
    _gpClearStatus();
  } catch (err) {
    _gpShowStatus(err.message, 'err');
  }
}

// ── SimC modal ─────────────────────────────────────────────────────────────────

function _gpShowSimcModal() {
  const modal    = document.getElementById('mcn-simc-modal');
  const textarea = document.getElementById('mcn-simc-text');
  if (modal)    modal.hidden = false;
  if (textarea) { textarea.value = ''; textarea.focus(); }
}

function _gpHideSimcModal() {
  const modal = document.getElementById('mcn-simc-modal');
  if (modal) modal.hidden = true;
}

async function _gpOnSimcImport() {
  const textarea = document.getElementById('mcn-simc-text');
  const text = textarea?.value?.trim();
  if (!text) return;
  _gpHideSimcModal();
  const charId = _selectedChar?.id;
  if (!charId) return;
  _gpShowStatus('Importing\u2026', 'info');
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/import-simc`, {
    method: 'POST',
    body: JSON.stringify({ simc_text: text }),
  });
  if (resp.ok) {
    const d = resp.data || {};
    _gpShowStatus(`Imported: ${d.populated || 0} slots set${d.skipped_locked ? `, ${d.skipped_locked} locked skipped` : ''}`, 'ok');
    await _gpReload();
  } else {
    _gpShowStatus(resp.error || 'Import failed', 'err');
  }
}

// ── Slot drawer ────────────────────────────────────────────────────────────────

function _gpToggleDrawer(slotKey) {
  if (_gpOpenSlot === slotKey) _gpCloseDrawer();
  else _gpOpenDrawer(slotKey);
}

function _gpOpenDrawer(slotKey) {
  _gpOpenSlot = slotKey;

  document.querySelectorAll('.mcn-slot-card').forEach(c => {
    c.classList.toggle('is-open', c.dataset.slot === slotKey);
  });

  const charId = _selectedChar?.id;
  const data   = charId ? _gpCache[charId] : null;
  const sd     = data?.slots?.[slotKey] || {};
  const tc     = data?.trackColors || {};

  const titleEl = document.getElementById('mcn-gp-drawer-title');
  const bodyEl  = document.getElementById('mcn-gp-drawer-body');
  const drawer  = document.getElementById('mcn-gp-drawer');

  if (titleEl) titleEl.textContent = GP_SLOT_LABELS[slotKey] || slotKey;
  if (bodyEl)  bodyEl.innerHTML    = _gpRenderDrawerBody(slotKey, sd, tc);
  if (drawer)  {
    drawer.hidden = false;
    drawer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  if (window.$WowheadPower) window.$WowheadPower.refreshLinks();
}

function _gpCloseDrawer() {
  _gpOpenSlot = null;
  document.querySelectorAll('.mcn-slot-card').forEach(c => c.classList.remove('is-open'));
  const drawer = document.getElementById('mcn-gp-drawer');
  if (drawer) drawer.hidden = true;
}

function _gpRenderDrawerBody(slotKey, sd, tc) {
  // dbSlot is the actual DB slot key for write operations. After paired-slot
  // normalization the visual position may differ from the DB slot (e.g. visual
  // ring_1 might correspond to DB ring_2 after an alphabetical swap).
  const dbSlot  = sd.canonical_slot || slotKey;
  const eq      = sd.equipped;
  const desired = sd.desired;
  const bis     = sd.bis_recommendations || [];
  const sources = sd.item_sources        || [];
  const tracks  = sd.available_tracks    || [];
  const upgrades = sd.upgrade_tracks     || [];

  // 1 — Equipped
  let equippedHtml;
  if (eq && eq.blizzard_item_id) {
    const qc = eq.quality_track ? _gpColor(eq.quality_track, tc) : null;
    const ns = qc && qc !== '#888' ? ` style="color:${qc}"` : '';
    const bs = qc && qc !== '#888' ? ` style="border-color:${qc};box-shadow:0 0 6px ${qc}80"` : '';
    const badge = eq.quality_track ? `<span class="mcn-track-pill" style="background:${_gpEsc(qc)}">${_gpEsc(eq.quality_track)}</span>` : '';
    equippedHtml = `<div class="mcn-drawer-item">
      ${eq.icon_url ? `<img class="mcn-drawer-item__icon" src="${_gpEsc(eq.icon_url)}" alt="" loading="lazy"${bs}>` : ''}
      <div class="mcn-drawer-item__info">
        <div class="mcn-drawer-item__name"${ns}>
          <a href="https://www.wowhead.com/item=${eq.blizzard_item_id}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none">${_gpEsc(eq.item_name || 'Unknown')}</a>
        </div>
        <div class="mcn-drawer-item__meta">${eq.item_level ? eq.item_level + '\u00a0' : ''}${badge}</div>
      </div>
    </div>`;
  } else {
    equippedHtml = '<div class="mcn-drawer-empty">Nothing equipped</div>';
  }

  // 2 — BIS grid
  const PAIRED = new Set(['ring_1','ring_2','trinket_1','trinket_2']);
  const bisHtml = _gpRenderBisGrid(slotKey, bis, tc, PAIRED.has(slotKey) ? null : (sd.desired_blizzard_item_id || null), dbSlot);

  // 3 — Your goal
  let goalHtml;
  if (desired && desired.blizzard_item_id) {
    const locked = desired.is_locked;
    goalHtml = `<div class="mcn-drawer-item" style="margin-bottom:0.5rem">
      ${desired.icon_url ? `<img class="mcn-drawer-item__icon" src="${_gpEsc(desired.icon_url)}" alt="" loading="lazy">` : ''}
      <div class="mcn-drawer-item__info">
        <div class="mcn-drawer-item__name">
          <a href="https://www.wowhead.com/item=${desired.blizzard_item_id}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none">${_gpEsc(desired.item_name || 'Unknown')}</a>
        </div>
      </div>
    </div>
    <div style="display:flex;gap:0.4rem;flex-wrap:wrap;margin-bottom:0.4rem">
      <button class="mcn-lock-btn${locked ? ' locked' : ''}" type="button"
              onclick="mcnGpToggleLock('${_gpEsc(dbSlot)}',${locked})">
        ${locked ? '&#x1F512; Locked' : '&#x1F513; Lock'}
      </button>
      <button class="btn btn-sm btn-secondary" type="button"
              onclick="mcnGpClearSlot('${_gpEsc(dbSlot)}')">Clear</button>
    </div>`;
  } else {
    goalHtml = '<div class="mcn-drawer-empty">No goal item set</div>';
  }

  const manualHtml = `<div class="mcn-manual-row">
    <input type="number" class="mcn-manual-input" id="mcn-mid-${_gpEsc(dbSlot)}" placeholder="Item ID" min="1">
    <button class="btn btn-sm btn-secondary" type="button" onclick="mcnGpFetchAndSet('${_gpEsc(dbSlot)}')">Fetch</button>
  </div>`;

  // 4 — Drop source
  const craftedSource = sd.crafted_source || null;
  let dropHtml;
  if (craftedSource) {
    const ct = craftedSource.track || 'H';
    const cc = _gpColor(ct, tc);
    const pill = `<span class="mcn-track-pill" style="background:${_gpEsc(cc)}">${_gpEsc(ct)}-Crest</span>`;
    const ccUrl = _gpEsc(craftedSource.crafting_corner_url || '/crafting-corner');
    dropHtml = `<div class="mcn-crafted-section">
      <div class="mcn-crafted-section__header">
        <span class="mcn-crafted-section__label">Crafted Item</span>
        ${pill}
      </div>
      <a href="${ccUrl}" class="mcn-crafted-section__link" target="_self">
        Order in Crafting Corner &rarr;
      </a>
    </div>`;
  } else if (sources.length) {
    const tPills = tracks.map(t => _gpPill(t, tc)).join(' ');
    const uPills = upgrades.map(t => _gpPill(t, tc)).join(' ');
    const srcBlock = _gpSourceHtml(
      sources,
      'mcn-drawer-source__group',
      'mcn-drawer-source__inst',
      'mcn-drawer-source__boss',
    );
    dropHtml = srcBlock +
      (tPills ? `<div class="mcn-drawer-item__meta" style="margin-top:4px"><span style="font-size:0.68rem;color:var(--color-text-muted)">Available:</span> ${tPills}</div>` : '') +
      (uPills ? `<div class="mcn-drawer-item__meta" style="margin-top:4px"><span style="font-size:0.68rem;color:var(--color-text-muted)">Upgrade to:</span> ${uPills}</div>` : '');
  } else {
    dropHtml = '<div class="mcn-drawer-empty">No drop source data</div>';
  }

  return `
    <div><div class="mcn-drawer-section__title">Equipped</div>${equippedHtml}</div>
    <div><div class="mcn-drawer-section__title">Your Goal</div>${goalHtml}${manualHtml}</div>
    <div><div class="mcn-drawer-section__title">Drop Location</div>${dropHtml}</div>
    <div class="mcn-drawer__bis-section"><div class="mcn-drawer-section__title">BIS Recommendations</div>${bisHtml}</div>`;
}

function _gpRenderBisGrid(slotKey, bis, tc, primaryBid, dbSlot) {
  dbSlot = dbSlot || slotKey;
  if (!bis.length) return '<div class="mcn-drawer-empty">No BIS data for this slot</div>';

  const srcMap = new Map();
  for (const r of bis) {
    if (!srcMap.has(r.source_id)) srcMap.set(r.source_id, r.short_label || r.source_name || `Source ${r.source_id}`);
  }
  const sources = [...srcMap.entries()].map(([id, label]) => ({ id, label }));

  const itemMap = new Map();
  for (const r of bis) {
    if (!itemMap.has(r.blizzard_item_id)) itemMap.set(r.blizzard_item_id, { bid: r.blizzard_item_id, name: r.item_name, icon: r.icon_url, srcIds: new Set() });
    itemMap.get(r.blizzard_item_id).srcIds.add(r.source_id);
  }

  const items = [...itemMap.values()].sort((a, b) => {
    if (primaryBid) {
      const d = (b.bid === primaryBid ? 1 : 0) - (a.bid === primaryBid ? 1 : 0);
      if (d !== 0) return d;
    }
    const d2 = b.srcIds.size - a.srcIds.size;
    return d2 !== 0 ? d2 : a.name.localeCompare(b.name);
  });

  const hdrCells = sources.map(s => `<th class="mcn-bis-grid__src" title="${_gpEsc(s.label)}">${_gpEsc(s.label)}</th>`).join('');

  const rows = items.map(item => {
    const cells = sources.map(s =>
      item.srcIds.has(s.id)
        ? `<td class="mcn-bis-grid__check mcn-bis-grid__check--yes">&#10003;</td>`
        : `<td class="mcn-bis-grid__check mcn-bis-grid__check--no">&mdash;</td>`
    ).join('');
    const icon = item.icon
      ? `<img class="mcn-bis-grid__icon" src="${_gpEsc(item.icon)}" alt="" loading="lazy">`
      : `<span class="mcn-bis-grid__icon-ph"></span>`;
    return `<tr>
      <td class="mcn-bis-grid__name"><div class="mcn-bis-grid__name-inner">${icon}<a href="https://www.wowhead.com/item=${item.bid}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none">${_gpEsc(item.name)}</a></div></td>
      ${cells}
      <td class="mcn-bis-grid__action"><button class="btn btn-sm btn-secondary" type="button" style="padding:0.1rem 0.4rem;font-size:0.7rem" onclick="mcnGpSetDesiredItem('${_gpEsc(dbSlot)}',${item.bid})">Use</button></td>
    </tr>`;
  }).join('');

  return `<table class="mcn-bis-grid">
    <thead><tr><th class="mcn-bis-grid__name-col">Item</th>${hdrCells}<th></th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// ── Slot action globals (called from onclick attrs in drawer) ──────────────────

window.mcnGpSetDesiredItem = async function(slot, blizzardItemId) {
  const charId = _selectedChar?.id;
  if (!charId) return;
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/slot/${slot}`, {
    method: 'PUT',
    body: JSON.stringify({ blizzard_item_id: blizzardItemId }),
  });
  if (resp.ok) { _gpShowStatus('Goal updated', 'ok'); await _gpReload(); }
  else _gpShowStatus(resp.error || 'Failed', 'err');
};

window.mcnGpClearSlot = async function(slot) {
  const charId = _selectedChar?.id;
  if (!charId) return;
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/slot/${slot}`, {
    method: 'PUT',
    body: JSON.stringify({ blizzard_item_id: null }),
  });
  if (resp.ok) { _gpShowStatus('Slot cleared', 'ok'); await _gpReload(); }
  else _gpShowStatus(resp.error || 'Failed', 'err');
};

window.mcnGpToggleLock = async function(slot, currentlyLocked) {
  const charId = _selectedChar?.id;
  if (!charId) return;
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/slot/${slot}`, {
    method: 'PUT',
    body: JSON.stringify({ is_locked: !currentlyLocked }),
  });
  if (resp.ok) {
    _gpShowStatus(!currentlyLocked ? 'Slot locked' : 'Slot unlocked', 'ok');
    await _gpReload();
  } else {
    _gpShowStatus(resp.error || 'Failed', 'err');
  }
};

window.mcnGpFetchAndSet = async function(slot) {
  const input  = document.getElementById(`mcn-mid-${slot}`);
  const itemId = parseInt(input?.value, 10);
  if (!itemId) return;
  _gpShowStatus('Fetching item\u2026', 'info');
  const itemResp = await _gpFetch(`/api/v1/items/${itemId}`);
  if (!itemResp.ok) { _gpShowStatus(itemResp.error || 'Item not found', 'err'); return; }
  await window.mcnGpSetDesiredItem(slot, itemResp.data.blizzard_item_id);
};
