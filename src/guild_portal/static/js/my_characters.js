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
  tank:   "ability_defend",
  healer: "spell_holy_flashheal",
  dps:    "ability_meleedamage",
  ranged: "ability_meleedamage",
  melee:  "ability_meleedamage",
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
  // Reset gear plan local state for new character
  _gpEquippedTab = null;
  _gpEquippedShowInput = false;
  _gpBisTab = 'guide';
  _renderHeader(char);
  _renderGuides(char);
  _gpResetPaperdolls();   // reset to placeholder; gear loads on gear-tab activation
  _gpCloseDrawer();
  _loadSummary(charId);
}

// ---------------------------------------------------------------------------
// Refresh button (delegates to existing /api/v1/me/bnet-sync)
// ---------------------------------------------------------------------------

// _initSimcModal removed — SimC import is now inline in the gear plan sections

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

const _gpCache = {};       // charId → API data (plan, slots, bisSources, heroTalents, trackColors)
const _gpAvailCache = {};  // "charId:dbSlot" → { status: 'loading'|'done'|'error', items: [] }
let _gpOpenSlot = null;
let _gpEquippedTab = null;        // null = use plan.equipped_source; 'blizzard'|'simc' for local override
let _gpEquippedShowInput = false; // show the SimC paste area in the equipped section
let _gpBisTab = 'guide';          // 'current'|'guide'|'simc_bis'

// ── Tour (Phase 1E.7) ─────────────────────────────────────────────────────────
const GP_TOUR_KEY = 'patt_gear_tour_v1';
let _gpTour          = null;
let _gpTourScheduled = false;

// ── Helpers ───────────────────────────────────────────────────────────────────

function _gpEsc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _gpTimeAgo(dateVal) {
  if (!dateVal) return null;
  const diff = Date.now() - new Date(dateVal).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min${mins !== 1 ? 's' : ''} ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs !== 1 ? 's' : ''} ago`;
  const days = Math.floor(hrs / 24);
  return `${days} day${days !== 1 ? 's' : ''} ago`;
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
        ? `<a href="https://www.wowhead.com/item=${eq.blizzard_item_id}" class="mcn-wh-link" target="_blank" rel="noopener noreferrer">
             <img class="mcn-gt__icon" src="${_gpEsc(eq.icon_url)}" alt="" loading="lazy"${iconBs}>
           </a>`
        : '';
      const nameColor = qc ? ` style="color:${_gpEsc(qc)}"` : '';
      equippedHtml = `<div class="mcn-gt__item">
        ${icon}
        <div class="mcn-gt__item-info">
          <span class="mcn-gt__name"${nameColor}>${_gpEsc(eq.item_name || 'Unknown')}</span>
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
          ? `<a href="https://www.wowhead.com/item=${goalItem.blizzard_item_id}" class="mcn-wh-link" target="_blank" rel="noopener noreferrer">
               <img class="mcn-gt__icon" src="${_gpEsc(goalItem.icon_url)}" alt="" loading="lazy">
             </a>`
          : '';
        goalHtml = `<div class="mcn-gt__item">
          ${icon}
          <span class="mcn-gt__name">${_gpEsc(goalItem.item_name || goalItem.name || 'Unknown')}</span>
        </div>`;
      } else {
        goalHtml = '<span class="mcn-gt__empty">&mdash;</span>';
      }
    }

    // ── Source cell ────────────────────────────────────────────────────────
    const craftedSrc = sd.crafted_source || null;
    let sourceHtml;
    if (craftedSrc) {
      const profLabel = craftedSrc.profession ? _gpEsc(craftedSrc.profession) : 'Crafted Item';
      sourceHtml = `<div class="mcn-gt__crafted-source">
        <span class="mcn-gt__crafted-label">${profLabel}</span>
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

  // ── Equipped Gear Source section ────────────────────────────────────────
  const serverSrc      = plan?.equipped_source || 'blizzard';
  const effectiveEqTab = _gpEquippedTab ?? serverSrc;
  const isEqBlizzard   = effectiveEqTab === 'blizzard';
  const isEqSimC       = effectiveEqTab === 'simc';

  const simcAt      = plan?.simc_imported_at   ? new Date(plan.simc_imported_at)   : null;
  const blizzardAt  = plan?.blizzard_synced_at ? new Date(plan.blizzard_synced_at) : null;
  const simcAgeDays = simcAt ? (Date.now() - simcAt.getTime()) / 86400000 : null;
  const simcStale   = simcAgeDays !== null && simcAgeDays > 7;

  // ── Status dots for equipped tabs ──────────────────────────────────────
  // Most recently updated tab = green; within 7d of the other = amber;
  // >7d older than the other = red; no data = off/blank.
  function _eqDot(thisAt, otherAt) {
    if (!thisAt) return '<span class="mcn-gp-dot mcn-gp-dot--off"></span>';
    if (!otherAt) return '<span class="mcn-gp-dot mcn-gp-dot--green"></span>';
    const diffDays = (thisAt.getTime() - otherAt.getTime()) / 86400000;
    if (diffDays >= 0) return '<span class="mcn-gp-dot mcn-gp-dot--green"></span>';
    if (Math.abs(diffDays) <= 7) return '<span class="mcn-gp-dot mcn-gp-dot--amber"></span>';
    return '<span class="mcn-gp-dot mcn-gp-dot--red"></span>';
  }
  const blizzDot = _eqDot(blizzardAt, simcAt);
  const simcDot  = _eqDot(simcAt, blizzardAt);

  // Blizzard panel — sync timestamp + Sync Now button
  const blizzardPanel = `
    <div class="mcn-gp-panel" id="mcn-gp-panel-blizzard"${isEqBlizzard ? '' : ' hidden'}>
      <div class="mcn-gp-sync-row">
        ${blizzardAt
          ? `<span class="mcn-gp-src-ts">Last synced ${_gpTimeAgo(blizzardAt)}</span>`
          : `<span class="mcn-gp-src-ts mcn-gp-src-ts--none">Not yet synced</span>`}
        <button id="mcn-gp-btn-sync" class="btn btn-secondary btn-sm" type="button">Sync Now</button>
      </div>
    </div>`;

  // SimC equipped panel — shows textarea if no import yet OR user clicked Re-import
  // Profile is persisted in DB; switching tabs does not lose it.
  const showSimcInput = isEqSimC && (_gpEquippedShowInput || !simcAt);
  const simcPanel = `
    <div class="mcn-gp-panel mcn-gp-panel--simc" id="mcn-gp-panel-simc"${isEqSimC ? '' : ' hidden'}>
      ${showSimcInput ? `
        <textarea id="mcn-gp-eq-simc-text" class="mcn-gp-textarea"
                  placeholder="Paste SimC profile here\u2026" rows="6"></textarea>
        <div class="mcn-gp-panel-actions">
          <button id="mcn-gp-btn-import-eq" class="btn btn-primary btn-sm" type="button">Set as Equipped</button>
          ${simcAt ? `<button id="mcn-gp-btn-cancel-eq" class="btn btn-secondary btn-sm" type="button">Cancel</button>` : ''}
        </div>` : `
        <div class="mcn-gp-sync-row">
          <span class="mcn-gp-src-ts">Snapshot from ${_gpTimeAgo(simcAt)}</span>
          <button id="mcn-gp-btn-reimport-eq" class="btn btn-secondary btn-sm" type="button">Re-import</button>
        </div>
        ${simcStale ? `<div class="mcn-gp-src-stale">&#9888; ${Math.floor(simcAgeDays)} days old \u2014 consider re-importing</div>` : ''}`}
    </div>`;

  const equippedSection = `
    <div class="mcn-gp-section">
      <div class="mcn-gp-section__hdr">
        <span class="mcn-gp-section__title">Equipped Gear Source</span>
        <button id="mcn-gp-btn-export-eq" class="mcn-gp-section__export" title="Export equipped gear as SimC" type="button">&#11015;</button>
      </div>
      <div class="mcn-gp-section__tabs">
        <button class="mcn-gp-stab${isEqBlizzard ? ' is-active' : ''}"
                onclick="_gpOnEquippedTab('blizzard')" type="button">${blizzDot} Blizzard API</button>
        <button class="mcn-gp-stab${isEqSimC ? ' is-active' : ''}"
                onclick="_gpOnEquippedTab('simc')" type="button">${simcDot} Import SimC</button>
      </div>
      ${blizzardPanel}
      ${simcPanel}
    </div>`;

  // ── BIS Sourcing section ────────────────────────────────────────────────
  const bisTab = _gpBisTab;

  const selectedSource = bisSources.find(s => s.id === plan?.bis_source_id) || bisSources[0];
  const showHtDropdown = !!(selectedSource?.has_hero_talent_variants && heroTalents.length > 0);

  const htOpts = ['<option value="">\u2014 Any \u2014</option>']
    .concat((heroTalents || []).map(ht =>
      `<option value="${ht.id}"${plan?.hero_talent_id === ht.id ? ' selected' : ''}>${_gpEsc(ht.name)}</option>`
    )).join('');

  const ORIGIN_LABEL       = { archon: 'u.gg', wowhead: 'Wowhead', icy_veins: 'Icy Veins' };
  const CONTENT_TYPE_LABEL = { raid: 'Raid', mythic_plus: 'M+', overall: 'All' };
  const CONTENT_TYPE_ORDER = { overall: 0, raid: 1, mythic_plus: 2 };
  const srcByOrigin = [];
  const seenOrigins = [];
  for (const s of (bisSources || [])) {
    if (!seenOrigins.includes(s.origin)) { seenOrigins.push(s.origin); srcByOrigin.push({ origin: s.origin, sources: [] }); }
    srcByOrigin.find(g => g.origin === s.origin).sources.push(s);
  }
  srcByOrigin.forEach(g => g.sources.sort((a, b) =>
    (CONTENT_TYPE_ORDER[a.content_type] ?? 9) - (CONTENT_TYPE_ORDER[b.content_type] ?? 9)));
  const srcOpts = srcByOrigin.map(({ origin, sources }) => {
    const groupLabel = ORIGIN_LABEL[origin] || origin;
    const options = sources.map(s => {
      const label = (CONTENT_TYPE_LABEL[s.content_type] || s.short_label) + (s.is_default ? ' \u2605' : '');
      return `<option value="${s.id}"${plan?.bis_source_id === s.id ? ' selected' : ''}>${_gpEsc(label)}</option>`;
    }).join('');
    return `<optgroup label="${_gpEsc(groupLabel)}">${options}</optgroup>`;
  }).join('');

  const bisCurrentPanel = `
    <div class="mcn-gp-panel" id="mcn-gp-panel-bis-current"${bisTab === 'current' ? '' : ' hidden'}>
      <p class="mcn-gp-blurb">Set your BIS goals to match your currently equipped gear. Unlocked slots will be updated to what you have on right now.</p>
      <div class="mcn-gp-panel-actions">
        <button id="mcn-gp-btn-set-from-eq" class="btn btn-primary btn-sm" type="button">Set Goals to Current Gear</button>
      </div>
    </div>`;

  const bisGuidePanel = `
    <div class="mcn-gp-panel" id="mcn-gp-panel-bis-guide"${bisTab === 'guide' ? '' : ' hidden'}>
      <div class="mcn-gear-ctrl-row">
        <label class="mcn-gear-label">BIS List</label>
        <select id="mcn-gp-src-sel" class="mcn-gear-select">${srcOpts}</select>
        ${showHtDropdown ? `
        <label class="mcn-gear-label">Hero Talent</label>
        <select id="mcn-gp-ht-sel" class="mcn-gear-select">${htOpts}</select>` : ''}
        <button id="mcn-gp-btn-fill" class="btn btn-primary btn-sm" type="button">Fill BIS</button>
      </div>
    </div>`;

  const bisSimcPanel = `
    <div class="mcn-gp-panel mcn-gp-panel--simc" id="mcn-gp-panel-bis-simc"${bisTab === 'simc_bis' ? '' : ' hidden'}>
      <textarea id="mcn-gp-bis-simc-text" class="mcn-gp-textarea"
                placeholder="Paste SimC profile here\u2026 Your BIS goals will be set to the items in the profile." rows="6"></textarea>
      <div class="mcn-gp-panel-actions">
        <button id="mcn-gp-btn-import-bis" class="btn btn-primary btn-sm" type="button">Set as BIS Goals</button>
      </div>
    </div>`;

  const bisSection = `
    <div class="mcn-gp-section">
      <div class="mcn-gp-section__hdr">
        <span class="mcn-gp-section__title">BIS Sourcing</span>
        <button id="mcn-gp-btn-export-bis" class="mcn-gp-section__export" title="Export BIS goals as SimC" type="button">&#11015;</button>
      </div>
      <div class="mcn-gp-section__tabs">
        <button class="mcn-gp-stab${bisTab === 'current'  ? ' is-active' : ''}"
                onclick="_gpOnBisTab('current')"  type="button">My Current Gear</button>
        <button class="mcn-gp-stab${bisTab === 'guide'    ? ' is-active' : ''}"
                onclick="_gpOnBisTab('guide')"    type="button">Use a Guide</button>
        <button class="mcn-gp-stab${bisTab === 'simc_bis' ? ' is-active' : ''}"
                onclick="_gpOnBisTab('simc_bis')" type="button">Import SimC</button>
      </div>
      ${bisCurrentPanel}
      ${bisGuidePanel}
      ${bisSimcPanel}
    </div>`;

  area.innerHTML = `
    <div id="mcn-gp-slot-detail" hidden></div>
    <div class="mcn-detail-area__heading">
      Gear Plan
      <button id="mcn-gp-tour-btn" class="mcn-gp-tour-btn" type="button" title="Take a guided tour of the gear plan">?</button>
      <a href="#mcn-gp-faq" class="mcn-gp-faq-link">FAQ &#x2193;</a>
    </div>
    <div class="mcn-gp-sections">
      ${equippedSection}
      ${bisSection}
    </div>
    <div id="mcn-gp-status" class="mcn-gp-status" hidden></div>
    ${_gpRenderGearTable(data.slots, data.track_colors)}
    ${_gpRenderFaq()}
  `;

  // If a slot is currently selected, re-populate its detail panel
  if (_gpOpenSlot) {
    const sd = data.slots?.[_gpOpenSlot] || {};
    _gpPopulateSlotDetail(_gpOpenSlot, sd, data.track_colors || {});
    document.querySelectorAll('.mcn-slot-card').forEach(c => {
      c.classList.toggle('is-open', c.dataset.slot === _gpOpenSlot);
    });
  }

  // Wire Equipped section
  document.getElementById('mcn-gp-btn-sync')       ?.addEventListener('click', _gpOnSyncGear);
  document.getElementById('mcn-gp-btn-import-eq')  ?.addEventListener('click', _gpOnImportEquipped);
  document.getElementById('mcn-gp-btn-cancel-eq')  ?.addEventListener('click', _gpCancelSimcInput);
  document.getElementById('mcn-gp-btn-reimport-eq')?.addEventListener('click', _gpStartSimcReimport);
  document.getElementById('mcn-gp-btn-export-eq')  ?.addEventListener('click', _gpOnExportEquipped);

  // Wire BIS section
  document.getElementById('mcn-gp-btn-set-from-eq') ?.addEventListener('click',  _gpOnSetGoalsFromEquipped);
  document.getElementById('mcn-gp-ht-sel')          ?.addEventListener('change', _gpOnConfigChange);
  document.getElementById('mcn-gp-src-sel')          ?.addEventListener('change', _gpOnConfigChange);
  document.getElementById('mcn-gp-btn-fill')         ?.addEventListener('click',  _gpOnPopulate);
  document.getElementById('mcn-gp-btn-import-bis')   ?.addEventListener('click',  _gpOnImportBisSimc);
  document.getElementById('mcn-gp-btn-export-bis')   ?.addEventListener('click',  _gpOnExportSimc);

  // Wire drawer close
  const drawerClose = document.getElementById('mcn-gp-drawer-close');
  if (drawerClose && !drawerClose._gpWired) {
    drawerClose._gpWired = true;
    drawerClose.addEventListener('click', _gpCloseDrawer);
  }

  // Wire tour button (Phase 1E.7)
  document.getElementById('mcn-gp-tour-btn')?.addEventListener('click', _gpLaunchTour);

  // Auto-launch tour on first visit
  _gpMaybeLaunchTour();
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
  // Trigger available items fetch in background (fire-and-forget)
  const charId = _selectedChar?.id;
  if (charId) {
    const dbSlot = sd.canonical_slot || slotKey;
    _gpLoadAvailableItems(charId, dbSlot);
  }
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
  // Invalidate available-items cache for this character so re-fetches happen after
  // gear sync or plan changes that could affect item availability.
  const prefix = `${charId}:`;
  for (const key of Object.keys(_gpAvailCache)) {
    if (key.startsWith(prefix)) delete _gpAvailCache[key];
  }
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
  if (_gpTour) { _gpShowStatus('Dismiss the tour first (\u2715 button), then sync.', 'info'); return; }
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
  if (_gpTour) { _gpShowStatus('Dismiss the tour first (\u2715 button), then fill.', 'info'); return; }
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

function _gpDownloadText(text, filename) {
  const a = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(new Blob([text], { type: 'text/plain' })),
    download: filename,
  });
  a.click();
  URL.revokeObjectURL(a.href);
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
    _gpDownloadText(await resp.text(), 'gear_plan.simc');
    _gpClearStatus();
  } catch (err) {
    _gpShowStatus(err.message, 'err');
  }
}

// ── Equipped section handlers (Phase 1E.6 redesign) ───────────────────────────

function _gpOnEquippedTab(tab) {
  const charId = _selectedChar?.id;
  if (!charId) return;
  const cached = _gpCache[charId];
  const serverSrc = cached?.plan?.equipped_source || 'blizzard';

  if (tab === 'simc') {
    // Show SimC panel locally; no server call until user submits
    _gpEquippedTab = 'simc';
    const hasImport = !!(cached?.plan?.simc_imported_at);
    _gpEquippedShowInput = !hasImport;
    if (cached) _gpRenderCenterPanel(cached);
  } else {
    // Switching to Blizzard: if server is already on blizzard, just local redraw
    _gpEquippedTab = 'blizzard';
    _gpEquippedShowInput = false;
    if (serverSrc === 'simc') {
      // Need to commit the switch to server
      _gpFetch(`/api/v1/me/gear-plan/${charId}/source`, {
        method: 'PATCH',
        body: JSON.stringify({ source: 'blizzard' }),
      }).then(resp => {
        if (resp.ok) { _gpReload(); }
        else _gpShowStatus(resp.error || 'Source switch failed', 'err');
      });
    } else if (cached) {
      _gpRenderCenterPanel(cached);
    }
  }
}

function _gpOnBisTab(tab) {
  _gpBisTab = tab;
  const charId = _selectedChar?.id;
  const cached = charId ? _gpCache[charId] : null;
  if (cached) _gpRenderCenterPanel(cached);
}

function _gpStartSimcReimport() {
  _gpEquippedShowInput = true;
  const charId = _selectedChar?.id;
  const cached = charId ? _gpCache[charId] : null;
  if (cached) _gpRenderCenterPanel(cached);
}

function _gpCancelSimcInput() {
  _gpEquippedShowInput = false;
  const charId = _selectedChar?.id;
  const cached = charId ? _gpCache[charId] : null;
  if (cached) _gpRenderCenterPanel(cached);
}

async function _gpOnImportEquipped() {
  const textarea = document.getElementById('mcn-gp-eq-simc-text');
  const text = textarea?.value?.trim();
  if (!text) { _gpShowStatus('Paste a SimC profile first', 'err'); return; }
  const charId = _selectedChar?.id;
  if (!charId) return;
  _gpShowStatus('Importing\u2026', 'info');
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/import-equipped-simc`, {
    method: 'POST',
    body: JSON.stringify({ simc_text: text }),
  });
  if (resp.ok) {
    _gpEquippedTab = 'simc';
    _gpEquippedShowInput = false;
    _gpShowStatus('Equipped gear updated from SimC', 'ok');
    await _gpReload();
  } else {
    _gpShowStatus(resp.error || 'Import failed', 'err');
  }
}

async function _gpOnSetGoalsFromEquipped() {
  const charId = _selectedChar?.id;
  if (!charId) return;
  _gpShowStatus('Copying equipped gear to goals\u2026', 'info');
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/set-goals-from-equipped`, { method: 'POST' });
  if (resp.ok) {
    const d = resp.data || {};
    const msg = `Goals set: ${d.populated || 0} slot${d.populated !== 1 ? 's' : ''}${d.skipped_locked ? `, ${d.skipped_locked} locked skipped` : ''}`;
    _gpBisTab = 'guide';
    _gpShowStatus(msg, 'ok');
    await _gpReload();
  } else {
    _gpShowStatus(resp.error || 'Failed to set goals', 'err');
  }
}

async function _gpOnImportBisSimc() {
  const textarea = document.getElementById('mcn-gp-bis-simc-text');
  const text = textarea?.value?.trim();
  if (!text) { _gpShowStatus('Paste a SimC profile first', 'err'); return; }
  const charId = _selectedChar?.id;
  if (!charId) return;
  _gpShowStatus('Importing\u2026', 'info');
  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/import-simc`, {
    method: 'POST',
    body: JSON.stringify({ simc_text: text }),
  });
  if (resp.ok) {
    const d = resp.data || {};
    _gpShowStatus(`BIS goals set: ${d.populated || 0} slots${d.skipped_locked ? `, ${d.skipped_locked} locked skipped` : ''}`, 'ok');
    _gpBisTab = 'guide';  // Switch to guide view to see the results
    await _gpReload();
  } else {
    _gpShowStatus(resp.error || 'Import failed', 'err');
  }
}

async function _gpOnExportEquipped() {
  const charId = _selectedChar?.id;
  if (!charId) return;
  const resp = await fetch(`/api/v1/me/gear-plan/${charId}/export-equipped-simc`);
  if (!resp.ok) { _gpShowStatus('No equipped gear data to export', 'err'); return; }
  const text = await resp.text();
  _gpDownloadText(text, 'equipped_gear.simc');
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
    const equippedIsGoal = eq.blizzard_item_id === sd.desired_blizzard_item_id;
    const useBtn = !equippedIsGoal
      ? `<button class="btn btn-sm btn-secondary" type="button" style="padding:0.1rem 0.4rem;font-size:0.7rem;flex-shrink:0;align-self:center" onclick="mcnGpSetDesiredItem('${_gpEsc(dbSlot)}',${eq.blizzard_item_id})">Use</button>`
      : '';
    equippedHtml = `<div class="mcn-drawer-item" style="align-items:center">
      ${eq.icon_url ? `<a href="https://www.wowhead.com/item=${eq.blizzard_item_id}" class="mcn-wh-link" target="_blank" rel="noopener noreferrer"><img class="mcn-drawer-item__icon" src="${_gpEsc(eq.icon_url)}" alt="" loading="lazy"${bs}></a>` : ''}
      <div class="mcn-drawer-item__info" style="flex:1">
        <div class="mcn-drawer-item__name"${ns}>
          ${_gpEsc(eq.item_name || 'Unknown')}
        </div>
        <div class="mcn-drawer-item__meta">${eq.item_level ? eq.item_level + '\u00a0' : ''}${badge}</div>
      </div>
      ${useBtn}
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
      ${desired.icon_url ? `<a href="https://www.wowhead.com/item=${desired.blizzard_item_id}" class="mcn-wh-link" target="_blank" rel="noopener noreferrer"><img class="mcn-drawer-item__icon" src="${_gpEsc(desired.icon_url)}" alt="" loading="lazy"></a>` : ''}
      <div class="mcn-drawer-item__info">
        <div class="mcn-drawer-item__name">
          ${_gpEsc(desired.item_name || 'Unknown')}
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
    <div class="mcn-manual-search-wrap">
      <input type="text" class="mcn-manual-input" id="mcn-mid-${_gpEsc(dbSlot)}"
             placeholder="Name, ID, or Wowhead link"
             oninput="mcnGpSearchItems('${_gpEsc(dbSlot)}', this.value)"
             autocomplete="off">
      <div class="mcn-item-results" id="mcn-mir-${_gpEsc(dbSlot)}" hidden></div>
    </div>
    <button class="btn btn-sm btn-secondary" type="button" onclick="mcnGpFetchAndSet('${_gpEsc(dbSlot)}')">Fetch</button>
  </div>`;

  // 4 — Drop source
  const craftedSource = sd.crafted_source || null;
  let dropHtml;
  if (craftedSource) {
    const ccUrl = _gpEsc(craftedSource.crafting_corner_url || '/crafting-corner');

    let craftersHtml;
    if (craftedSource.no_recipe_found || craftedSource.total_crafters === 0) {
      craftersHtml = `<div class="mcn-crafted-section__crafter mcn-crafted-section__crafter--none">No guild crafter has this pattern</div>`;
    } else {
      craftersHtml = craftedSource.crafters.map(c =>
        `<div class="mcn-crafted-section__crafter">${_gpEsc(c)}</div>`
      ).join('');
      const remaining = craftedSource.total_crafters - craftedSource.crafters.length;
      if (remaining > 0) {
        craftersHtml += `<div class="mcn-crafted-section__crafter mcn-crafted-section__crafter--more">+${remaining} others</div>`;
      }
    }

    const profBlock = craftedSource.profession
      ? `<div class="mcn-crafted-section__prof">${_gpEsc(craftedSource.profession)}</div>`
      : `<div class="mcn-crafted-section__prof">Crafted Item</div>`;

    dropHtml = `<div class="mcn-crafted-section">
      <div class="mcn-crafted-section__crafters">
        ${profBlock}
        ${craftersHtml}
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

  // 5 — Available from Content — raid, M+, crafted (lazy-loaded; uses cache if already fetched)
  const charId5  = _selectedChar?.id;
  const cacheKey = charId5 ? `${charId5}:${dbSlot}` : null;
  const avCached = cacheKey ? _gpAvailCache[cacheKey] : null;
  const availBodyHtml = avCached
    ? _gpRenderAvailSections(dbSlot, avCached.groups, tc, avCached.status)
    : '<div class="mcn-drawer-empty">Loading\u2026</div>';
  const availHtml = `<div id="mcn-avail-body-${_gpEsc(dbSlot)}">${availBodyHtml}</div>`;

  // Phase 1E.5: excluded items section
  const excludedItems = sd.excluded_items || [];
  const excludedHtml = excludedItems.length > 0
    ? `<details class="mcn-avail-section">
        <summary class="mcn-avail-section__toggle">Excluded items (${excludedItems.length})</summary>
        <div>${_gpRenderExcludedItems(dbSlot, excludedItems)}</div>
       </details>`
    : '';

  return `
    <div><div class="mcn-drawer-section__title">Equipped</div>${equippedHtml}</div>
    <div><div class="mcn-drawer-section__title">Your Goal</div>${goalHtml}${manualHtml}</div>
    <div><div class="mcn-drawer-section__title">Drop Location</div>${dropHtml}</div>
    <div class="mcn-drawer__bis-section"><div class="mcn-drawer-section__title">BIS Recommendations</div>${bisHtml}</div>
    <div class="mcn-drawer__bis-section">${availHtml}</div>
    ${excludedItems.length > 0 ? `<div class="mcn-drawer__bis-section">${excludedHtml}</div>` : ''}`;
}

function _gpRenderBisGrid(slotKey, bis, tc, primaryBid, dbSlot) {
  dbSlot = dbSlot || slotKey;
  if (!bis.length) return '<div class="mcn-drawer-empty">No BIS data for this slot</div>';

  const ORIGIN_LABEL_G        = { archon: 'u.gg', wowhead: 'Wowhead', icy_veins: 'Icy Veins' };
  const CONTENT_TYPE_LABEL_G  = { raid: 'Raid', mythic_plus: 'M+', overall: 'All' };
  const CONTENT_TYPE_ORDER_G  = { overall: 0, raid: 1, mythic_plus: 2 };

  const srcMap = new Map();
  for (const r of bis) {
    if (!srcMap.has(r.source_id)) srcMap.set(r.source_id, {
      id: r.source_id,
      label: r.short_label || r.source_name || `Source ${r.source_id}`,
      origin: r.origin || '',
      content_type: r.content_type || '',
    });
  }
  const sources = [...srcMap.values()];

  // Group sources by origin for two-row header
  const originGroups = [];
  const seenOrigins  = [];
  for (const s of sources) {
    if (!seenOrigins.includes(s.origin)) { seenOrigins.push(s.origin); originGroups.push({ origin: s.origin, cols: [] }); }
    originGroups.find(g => g.origin === s.origin).cols.push(s);
  }
  originGroups.forEach(g => g.cols.sort((a, b) =>
    (CONTENT_TYPE_ORDER_G[a.content_type] ?? 9) - (CONTENT_TYPE_ORDER_G[b.content_type] ?? 9)));
  const hasMultiColGroup = originGroups.some(g => g.cols.length > 1);

  // Row 1: "Item" + provider cells + action
  const providerCells = originGroups.map(g => {
    const label   = _gpEsc(ORIGIN_LABEL_G[g.origin] || g.origin);
    const colspan = g.cols.length;
    // Single-column group: span both rows so row 2 stays clean
    return colspan === 1
      ? `<th class="mcn-bis-grid__provider mcn-bis-grid__provider--solo"${hasMultiColGroup ? ' rowspan="2"' : ''}>${label}</th>`
      : `<th class="mcn-bis-grid__provider" colspan="${colspan}">${label}</th>`;
  }).join('');

  // Row 2: content-type label for each column in multi-col groups only
  const contentCells = hasMultiColGroup
    ? originGroups.flatMap(g => g.cols.length === 1 ? [] : g.cols.map(s =>
        `<th class="mcn-bis-grid__src">${_gpEsc(CONTENT_TYPE_LABEL_G[s.content_type] || s.label)}</th>`
      )).join('')
    : '';

  const thead = hasMultiColGroup
    ? `<thead>
        <tr><th class="mcn-bis-grid__name-col" rowspan="2">Item</th>${providerCells}<th rowspan="2"></th></tr>
        <tr>${contentCells}</tr>
       </thead>`
    : `<thead><tr><th class="mcn-bis-grid__name-col">Item</th>${providerCells}<th></th></tr></thead>`;

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

  const rows = items.map(item => {
    const cells = sources.map(s =>
      item.srcIds.has(s.id)
        ? `<td class="mcn-bis-grid__check mcn-bis-grid__check--yes">&#10003;</td>`
        : `<td class="mcn-bis-grid__check mcn-bis-grid__check--no">&mdash;</td>`
    ).join('');
    const icon = item.icon
      ? `<a href="https://www.wowhead.com/item=${item.bid}" class="mcn-wh-link" target="_blank" rel="noopener noreferrer"><img class="mcn-bis-grid__icon" src="${_gpEsc(item.icon)}" alt="" loading="lazy"></a>`
      : `<span class="mcn-bis-grid__icon-ph"></span>`;
    const nameEsc = _gpEsc(item.name).replace(/'/g, "&#39;");
    return `<tr>
      <td class="mcn-bis-grid__name"><div class="mcn-bis-grid__name-inner">${icon}${_gpEsc(item.name)}</div></td>
      ${cells}
      <td class="mcn-bis-grid__action">
        <button class="btn btn-sm btn-secondary" type="button" style="padding:0.1rem 0.4rem;font-size:0.7rem" onclick="mcnGpSetDesiredItem('${_gpEsc(dbSlot)}',${item.bid})">Use</button>
        <button class="mcn-exclude-btn" type="button" title="Exclude this item" onclick="mcnGpExcludeItem('${_gpEsc(dbSlot)}',${item.bid},'${nameEsc}')">&times;</button>
      </td>
    </tr>`;
  }).join('');

  return `<table class="mcn-bis-grid">${thead}<tbody>${rows}</tbody></table>`;
}

// ── Available items (Phase 1E.4 / 1F) ─────────────────────────────────────────

// Renders up to four collapsible sections: Tier/Catalyst, Raid Loot, Mythic+ Loot, Crafted.
// `groups` is { tier: [...] | null, raid: [...], dungeon: [...], crafted: [...] } from the API.
// Tier section is omitted entirely when groups.tier is null (non-tier slot).
function _gpRenderAvailSections(dbSlot, groups, tc, status) {
  if (status === 'loading') return '<div class="mcn-drawer-empty">Loading\u2026</div>';
  if (status === 'error')   return '<div class="mcn-drawer-empty">Could not load items</div>';

  const sections = [
    // Tier section only rendered when backend signals this is a tier/catalyst slot
    ...(groups?.tier != null
      ? [{ key: 'tier', label: 'Tier / Catalyst', showTracks: false, subField: null }]
      : []),
    { key: 'raid',    label: 'Raid Loot',    showTracks: true,  subField: 'source_name'     },
    { key: 'dungeon', label: 'Mythic+ Loot', showTracks: true,  subField: 'source_instance' },
    { key: 'crafted', label: 'Crafted',      showTracks: false, subField: null              },
  ];

  return sections.map(({ key, label, showTracks, subField }) => {
    const items = groups?.[key] || [];
    const bodyHtml = items.length
      ? _gpRenderAvailTable(dbSlot, items, tc, showTracks, subField)
      : `<div class="mcn-drawer-empty">No eligible ${label.toLowerCase()} items found</div>`;
    return `<details class="mcn-avail-section">
      <summary class="mcn-avail-section__toggle">${label}</summary>
      ${bodyHtml}
    </details>`;
  }).join('');
}

// Renders one item table for a single source section.
// subField: which source property to show as item subtitle ('source_name' for
// raid boss, 'source_instance' for M+ dungeon name, null for crafted).
function _gpRenderAvailTable(dbSlot, items, tc, showTracks, subField) {
  const rows = items.map(item => {
    const icon = item.icon_url
      ? `<a href="https://www.wowhead.com/item=${item.blizzard_item_id}" class="mcn-wh-link" target="_blank" rel="noopener noreferrer"><img class="mcn-bis-grid__icon" src="${_gpEsc(item.icon_url)}" alt="" loading="lazy"></a>`
      : `<span class="mcn-bis-grid__icon-ph"></span>`;

    let trackCell = '';
    if (showTracks) {
      const allTracks = new Set(item.sources?.flatMap(s => s.quality_tracks || []) || []);
      const trackPills = ['V','C','H','M'].filter(t => allTracks.has(t)).map(t => _gpPill(t, tc)).join(' ');
      trackCell = `<td class="mcn-avail-item__tracks">${trackPills}</td>`;
    }

    const subTexts = subField
      ? [...new Set((item.sources || []).map(s => s[subField]).filter(Boolean))]
      : [];
    const subHtml = subTexts.length
      ? `<div class="mcn-avail-item__inst">${_gpEsc(subTexts.join(', '))}</div>`
      : '';

    const nameEsc = _gpEsc(item.name).replace(/'/g, "&#39;");
    return `<tr>
      <td class="mcn-bis-grid__name">
        <div class="mcn-bis-grid__name-inner">
          ${icon}
          ${_gpEsc(item.name)}
        </div>
        ${subHtml}
      </td>
      ${trackCell}
      <td class="mcn-bis-grid__action">
        <button class="btn btn-sm btn-secondary" type="button" style="padding:0.1rem 0.4rem;font-size:0.7rem" onclick="mcnGpSetDesiredItem('${_gpEsc(dbSlot)}',${item.blizzard_item_id})">Use</button>
        <button class="mcn-exclude-btn" type="button" title="Exclude this item" onclick="mcnGpExcludeItem('${_gpEsc(dbSlot)}',${item.blizzard_item_id},'${nameEsc}')">&times;</button>
      </td>
    </tr>`;
  }).join('');

  const trackTh = showTracks ? `<th style="font-size:0.63rem;text-align:center">Tracks</th>` : '';
  return `<table class="mcn-bis-grid">
    <thead><tr>
      <th class="mcn-bis-grid__name-col">Item</th>
      ${trackTh}
      <th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function _gpRenderExcludedItems(dbSlot, items) {
  if (!items || !items.length) return '';
  const rows = items.map(item => {
    const icon = item.icon_url
      ? `<a href="https://www.wowhead.com/item=${item.blizzard_item_id}" class="mcn-wh-link" target="_blank" rel="noopener noreferrer" style="opacity:0.5"><img class="mcn-bis-grid__icon" src="${_gpEsc(item.icon_url)}" alt="" loading="lazy"></a>`
      : `<span class="mcn-bis-grid__icon-ph"></span>`;
    return `<tr>
      <td class="mcn-bis-grid__name">
        <div class="mcn-bis-grid__name-inner">
          ${icon}
          <span style="opacity:0.5">${_gpEsc(item.name)}</span>
        </div>
      </td>
      <td class="mcn-bis-grid__action">
        <button class="btn btn-sm btn-secondary" type="button" title="Un-exclude" style="padding:0.1rem 0.4rem;font-size:0.7rem" onclick="mcnGpUnexcludeItem('${_gpEsc(dbSlot)}',${item.blizzard_item_id})">&#8617;</button>
      </td>
    </tr>`;
  }).join('');
  return `<table class="mcn-bis-grid" style="opacity:0.8">
    <thead><tr><th class="mcn-bis-grid__name-col">Item</th><th></th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function _gpLoadAvailableItems(charId, dbSlot) {
  const key = `${charId}:${dbSlot}`;
  if (_gpAvailCache[key]) {
    // Already loaded — update DOM in case it rendered before the fetch completed
    if (_gpAvailCache[key].status === 'done') {
      const bodyEl = document.getElementById(`mcn-avail-body-${dbSlot}`);
      if (bodyEl) {
        const tc = _gpCache[charId]?.track_colors || {};
        bodyEl.innerHTML = _gpRenderAvailSections(dbSlot, _gpAvailCache[key].groups, tc, 'done');
      }
    }
    return;
  }

  _gpAvailCache[key] = { status: 'loading', groups: {} };

  try {
    const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/available-items?slot=${encodeURIComponent(dbSlot)}`);
    if (resp.ok) {
      _gpAvailCache[key] = { status: 'done', groups: resp.data || {} };
    } else {
      _gpAvailCache[key] = { status: 'error', groups: {} };
    }
  } catch {
    _gpAvailCache[key] = { status: 'error', groups: {} };
  }

  // Update the section in-place if still visible
  const bodyEl = document.getElementById(`mcn-avail-body-${dbSlot}`);
  if (bodyEl) {
    const tc    = _gpCache[charId]?.track_colors || {};
    const state = _gpAvailCache[key];
    bodyEl.innerHTML = _gpRenderAvailSections(dbSlot, state.groups, tc, state.status);
  }
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
  const input = document.getElementById(`mcn-mid-${slot}`);
  const raw   = input?.value?.trim() || '';
  if (!raw) return;

  // Format 2 — Wowhead URL: extract item ID from /item=NNNNN or /item/NNNNN
  const urlMatch = raw.match(/[?&/]item[=/](\d+)/i);
  if (urlMatch) {
    const itemId = parseInt(urlMatch[1], 10);
    _gpShowStatus('Fetching item\u2026', 'info');
    const itemResp = await _gpFetch(`/api/v1/items/${itemId}`);
    if (!itemResp.ok) { _gpShowStatus(itemResp.error || 'Item not found', 'err'); return; }
    await window.mcnGpSetDesiredItem(slot, itemResp.data.blizzard_item_id);
    return;
  }

  // Format 1 — Plain integer
  const itemId = parseInt(raw, 10);
  if (!isNaN(itemId) && itemId > 0 && String(itemId) === raw) {
    _gpShowStatus('Fetching item\u2026', 'info');
    const itemResp = await _gpFetch(`/api/v1/items/${itemId}`);
    if (!itemResp.ok) { _gpShowStatus(itemResp.error || 'Item not found', 'err'); return; }
    await window.mcnGpSetDesiredItem(slot, itemResp.data.blizzard_item_id);
    return;
  }

  // Format 3 — Name: trigger inline search
  await window.mcnGpSearchItems(slot, raw);
};

window.mcnGpSearchItems = async function(slot, value) {
  const val       = (value || '').trim();
  const resultsEl = document.getElementById(`mcn-mir-${slot}`);
  if (!resultsEl) return;

  // Don't search for plain numbers or URLs — those go through Fetch
  if (val.length < 2 || /^\d+$/.test(val) || /[?&/]item[=/]/i.test(val)) {
    resultsEl.hidden = true;
    resultsEl.innerHTML = '';
    return;
  }

  const resp = await _gpFetch(`/api/v1/items/search?q=${encodeURIComponent(val)}`);
  if (!resp.ok || !resp.data?.length) {
    resultsEl.hidden = true;
    resultsEl.innerHTML = '';
    return;
  }

  resultsEl.innerHTML = resp.data.map(item => {
    const icon = item.icon_url
      ? `<img src="${_gpEsc(item.icon_url)}" alt="" class="mcn-item-result__icon">`
      : `<span class="mcn-item-result__icon-ph"></span>`;
    return `<div class="mcn-item-result" onclick="mcnGpPickSearchResult('${_gpEsc(slot)}',${item.blizzard_item_id},'${_gpEsc(item.name)}')">${icon}<span>${_gpEsc(item.name)}</span></div>`;
  }).join('');
  resultsEl.hidden = false;
};

window.mcnGpPickSearchResult = async function(slot, blizzardItemId, name) {
  const resultsEl = document.getElementById(`mcn-mir-${slot}`);
  if (resultsEl) { resultsEl.hidden = true; resultsEl.innerHTML = ''; }
  const input = document.getElementById(`mcn-mid-${slot}`);
  if (input) input.value = '';
  await window.mcnGpSetDesiredItem(slot, blizzardItemId);
};

// ── Item exclusion (Phase 1E.5) ───────────────────────────────────────────────

window.mcnGpExcludeItem = async function(slot, blizzardItemId, itemName) {
  const charId = _selectedChar?.id;
  if (!charId) return;

  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/slots/${slot}/exclude`, {
    method: 'PATCH',
    body: JSON.stringify({ blizzard_item_id: blizzardItemId }),
  });

  if (!resp.ok) {
    _gpShowStatus(resp.error || 'Failed to exclude item', 'err');
    return;
  }

  // Invalidate available-items cache so the excluded item disappears
  const cacheKey = `${charId}:${slot}`;
  delete _gpAvailCache[cacheKey];

  // Reload plan (gets updated excluded_items list), then re-open drawer
  await _gpReload();

  // Show undo toast
  _gpShowExcludeToast(slot, blizzardItemId, itemName);
};

window.mcnGpUnexcludeItem = async function(slot, blizzardItemId) {
  const charId = _selectedChar?.id;
  if (!charId) return;

  // Dismiss toast if still showing
  const toast = document.getElementById('mcn-exclude-toast');
  if (toast) { clearTimeout(toast._gpTimer); toast.remove(); }

  const resp = await _gpFetch(`/api/v1/me/gear-plan/${charId}/slots/${slot}/exclude`, {
    method: 'DELETE',
    body: JSON.stringify({ blizzard_item_id: blizzardItemId }),
  });

  if (resp.ok) {
    // Invalidate available-items cache for this slot
    delete _gpAvailCache[`${charId}:${slot}`];
    await _gpReload();
  } else {
    _gpShowStatus(resp.error || 'Failed to un-exclude item', 'err');
  }
};

function _gpShowExcludeToast(slot, blizzardItemId, itemName) {
  // Remove any existing toast
  const existing = document.getElementById('mcn-exclude-toast');
  if (existing) { clearTimeout(existing._gpTimer); existing.remove(); }

  const toast = document.createElement('div');
  toast.id = 'mcn-exclude-toast';
  toast.className = 'mcn-exclude-toast';
  toast.innerHTML = `<span>Excluded <strong>${_gpEsc(itemName)}</strong></span>
    <button type="button" onclick="mcnGpUnexcludeItem('${_gpEsc(slot)}',${blizzardItemId})">Undo</button>`;
  document.body.appendChild(toast);

  toast._gpTimer = setTimeout(() => toast.remove(), 3000);
}

// ── Gear plan help tour (Phase 1E.7) ──────────────────────────────────────────

function _gpRenderFaq() {
  const entries = [
    {
      q: 'Where did this gear plan come from?',
      a: 'Automatically pre-filled with the Wowhead Overall BIS list for your spec when your character was first synced. You can change any slot by clicking a row, or swap to a different guide source using the BIS Sourcing selector above.',
    },
    {
      q: 'My equipped gear looks wrong or outdated',
      a: `Blizzard's API can lag 24–72 hours after you log out. For an instant update, use <strong>SimC</strong>:<br>
          <ol class="mcn-faq-ol">
            <li>Install the <strong>Simulationcraft</strong> addon from <a href="https://www.curseforge.com/wow/addons/simulationcraft" target="_blank" rel="noopener noreferrer">CurseForge</a> or <a href="https://addons.wago.io/addons/simulationcraft" target="_blank" rel="noopener noreferrer">Wago</a>.</li>
            <li>Log in to your character in WoW and type <code>/simc</code> in chat.</li>
            <li>Copy the entire output from the popup window.</li>
            <li>On this page, go to <strong>Equipped Gear Source → Import SimC</strong>, paste it in, and click <strong>Set as Equipped</strong>.</li>
          </ol>`,
    },
    {
      q: 'Why does this plan differ from what I\'d sim on RaidBots?',
      a: 'BIS lists are generalized for your spec. A sim runs against your specific stats and current gear combination. Use this plan to track upgrade targets; once you\'re mostly geared, RaidBots can fine-tune the last few choices.',
    },
    {
      q: 'How do I lock or exclude an item?',
      a: '<strong>Lock</strong> (padlock icon in the slot drawer) protects a slot from Fill BIS — useful when you\'ve chosen something intentionally different from the guide. <strong>Exclude</strong> (✕ button on any BIS or available-item row) hides that specific item from all recommendations for this slot permanently. Both can be undone.',
    },
    {
      q: 'What are the quality tracks — Veteran, Champion, Hero, Mythic?',
      a: '<strong>V</strong>eteran / <strong>C</strong>hampion / <strong>H</strong>ero / <strong>M</strong>ythic — the upgrade track system. Your plan shows which tracks you still need for each slot. Vault drops are always at the highest track you\'ve cleared that boss/key level on.',
    },
  ];

  const items = entries.map(({ q, a }) => `
    <details class="mcn-faq-item">
      <summary class="mcn-faq-q">${_gpEsc(q)}</summary>
      <div class="mcn-faq-a">${a}</div>
    </details>`).join('');

  return `<div class="mcn-gp-faq" id="mcn-gp-faq">
    <div class="mcn-gp-faq__hdr">Frequently Asked Questions</div>
    ${items}
  </div>`;
}

function _gpLaunchTour() {
  if (typeof Shepherd === 'undefined') {
    console.warn('Shepherd.js not loaded — tour unavailable');
    return;
  }
  // Cancel any existing tour
  if (_gpTour) { try { _gpTour.cancel(); } catch (_) {} }

  // Ensure we're on the Guide BIS tab so dropdowns are visible
  if (_gpBisTab !== 'guide') {
    _gpBisTab = 'guide';
    const charId = _selectedChar?.id;
    if (charId && _gpCache[charId]) _gpRenderCenterPanel(_gpCache[charId]);
  }

  const btnNext = (t) => ({ text: 'Next →',     action: () => t.next(),     classes: 'mcn-shepherd-btn mcn-shepherd-btn--primary' });
  const btnBack = (t) => ({ text: '← Back',     action: () => t.back(),     classes: 'mcn-shepherd-btn mcn-shepherd-btn--secondary' });
  const btnDone = (t) => ({ text: 'Done ✓',     action: () => t.complete(), classes: 'mcn-shepherd-btn mcn-shepherd-btn--primary' });

  _gpTour = new Shepherd.Tour({
    useModalOverlay: false,
    exitOnEsc: true,
    defaultStepOptions: {
      cancelIcon: { enabled: true },
      classes: 'mcn-shepherd-step',
      scrollTo: { behavior: 'smooth', block: 'nearest' },
    },
  });

  // Helper: return attachTo options if the element exists, else undefined (centered popup)
  function at(selector, on) {
    return document.querySelector(selector)
      ? { element: selector, on: on || 'bottom' }
      : undefined;
  }

  // Helper: highlight a whole section panel while popup points at a sub-element
  function hl(sectionSel) {
    const HL = 'mcn-shepherd-highlight';
    return {
      show() { document.querySelector(sectionSel)?.classList.add(HL); },
      hide() { document.querySelector(sectionSel)?.classList.remove(HL); },
    };
  }

  const EQUIPPED_SEL = '.mcn-gp-section:first-of-type';
  const BIS_SEL      = '.mcn-gp-sections > .mcn-gp-section:nth-child(2)';

  const t = _gpTour;

  // ── Stop 1: Equipped Gear Source section ─────────────────────────────────
  t.addStep({
    id: 'equipped-source',
    title: 'Your Equipped Gear',
    text: 'This section reflects what you\'re currently wearing in-game. Blizzard\'s API can lag 24–72 hours after you log out, so the snapshot here may be a day or two behind.',
    attachTo: at(EQUIPPED_SEL, 'right'),
    highlightClass: 'mcn-shepherd-highlight',
    buttons: [btnNext(t)],
  });

  // ── Stop 2: Sync Now ──────────────────────────────────────────────────────
  t.addStep({
    id: 'sync-now',
    title: 'Sync with Blizzard',
    text: 'Hit <strong>Sync Now</strong> to pull the latest data from Blizzard. Great to do before you check your vault!',
    attachTo: at('#mcn-gp-btn-sync', 'bottom'),
    when: hl(EQUIPPED_SEL),
    buttons: [btnBack(t), btnNext(t)],
  });

  // ── Stop 3: Import SimC tab ───────────────────────────────────────────────
  t.addStep({
    id: 'simc-tab',
    title: 'Import SimC — Instant Update',
    text: 'If the Blizzard data is still stale, switch to <strong>Import SimC</strong>. Install the Simulationcraft addon, type <code>/simc</code> in WoW, paste the output here, and your gear updates immediately — no waiting. The FAQ at the bottom of this page has step-by-step instructions.',
    attachTo: at(`${EQUIPPED_SEL} .mcn-gp-section__tabs`, 'bottom'),
    when: hl(EQUIPPED_SEL),
    buttons: [btnBack(t), btnNext(t)],
  });

  // ── Stop 4: BIS Sourcing section ──────────────────────────────────────────
  t.addStep({
    id: 'bis-sourcing',
    title: 'BIS Goals',
    text: 'Now the right section: your Best-in-Slot goals. This is what you\'re working toward for each slot. There are a few ways to set this up — pick a guide, use your current gear as a baseline, or import from SimC.',
    attachTo: at(BIS_SEL, 'left'),
    highlightClass: 'mcn-shepherd-highlight',
    buttons: [btnBack(t), btnNext(t)],
  });

  // ── Stop 5: BIS List dropdown ─────────────────────────────────────────────
  // Ensure guide tab is visible
  if (document.querySelector('#mcn-gp-panel-bis-guide')?.hidden) _gpOnBisTab('guide');

  t.addStep({
    id: 'bis-source-sel',
    title: 'Pick Your Guide',
    text: 'Select your BIS source here. <strong>Wowhead Overall</strong> is a great starting point — it loads the full ranked gear list from Wowhead\'s spec guide for your spec, covering both raid drops and M+ in one list.',
    attachTo: at('#mcn-gp-src-sel', 'bottom'),
    when: hl(BIS_SEL),
    buttons: [btnBack(t), btnNext(t)],
  });

  // ── Stop 6: Hero Talent (conditional) ────────────────────────────────────
  if (document.getElementById('mcn-gp-ht-sel')) {
    t.addStep({
      id: 'ht-sel',
      title: 'Hero Talent',
      text: 'Your spec has hero talent variants with different BIS lists. Pick the tree you\'re playing so the recommendations match your build.',
      attachTo: at('#mcn-gp-ht-sel', 'bottom'),
      when: hl(BIS_SEL),
      buttons: [btnBack(t), btnNext(t)],
    });
  }

  // ── Stop 7: Fill BIS ──────────────────────────────────────────────────────
  t.addStep({
    id: 'fill-bis',
    title: 'Fill BIS',
    text: 'Click <strong>Fill BIS</strong> to populate every unlocked slot with the top-ranked recommendation from your chosen guide. You can run this anytime — locked slots are always preserved.',
    attachTo: at('#mcn-gp-btn-fill', 'bottom'),
    when: hl(BIS_SEL),
    buttons: [btnBack(t), btnNext(t)],
  });

  // ── Stop 8: Paperdoll layout — highlight both columns ─────────────────────
  t.addStep({
    id: 'paperdoll',
    title: 'Gear Slots',
    text: 'The columns on either side show all your gear slots. Your <strong>equipped item</strong> is on the inner side (closer to center); your <strong>BIS goal</strong> is on the outer edge. A <span style="color:#4ade80;font-weight:600;">green</span> left border means you already have that BIS item — a <span style="color:#f87171;font-weight:600;">red</span> border means there\'s still an upgrade to go after. The colored border on each icon shows its quality track: <strong>V</strong>eteran / <strong>C</strong>hampion / <strong>H</strong>ero / <strong>M</strong>ythic.',
    attachTo: at('#mcn-left-paperdoll', 'right'),
    when: {
      show() {
        document.getElementById('mcn-left-paperdoll')?.classList.add('mcn-shepherd-highlight');
        document.getElementById('mcn-right-paperdoll')?.classList.add('mcn-shepherd-highlight');
      },
      hide() {
        document.getElementById('mcn-left-paperdoll')?.classList.remove('mcn-shepherd-highlight');
        document.getElementById('mcn-right-paperdoll')?.classList.remove('mcn-shepherd-highlight');
      },
    },
    buttons: [btnBack(t), btnNext(t)],
  });

  // ── Stop 9: Slot detail — open head slot so the panel is visible ──────────
  t.addStep({
    id: 'slot-detail',
    title: 'Slot Detail Panel',
    text: 'Click any slot card or table row to open this detail view. Here you can see exactly why an item is recommended, ranked alternatives, where it drops, and controls to <strong>lock</strong> a slot or <strong>exclude</strong> items you don\'t want showing up in recommendations.',
    attachTo: at('#mcn-gp-slot-detail', 'bottom'),
    highlightClass: 'mcn-shepherd-highlight',
    beforeShowPromise() {
      return new Promise(resolve => {
        _gpSelectSlotInCenter(GP_LEFT_BODY_SLOTS[0] || 'head');
        setTimeout(resolve, 150);
      });
    },
    buttons: [btnBack(t), btnNext(t)],
  });

  // ── Stop 10: Gear table ───────────────────────────────────────────────────
  t.addStep({
    id: 'gear-table',
    title: 'Gear Summary Table',
    text: 'Scroll down for the full table — every slot at a glance. You\'ll see your equipped item, your goal, the drop source, and which quality tracks (<strong>V</strong>eteran / <strong>C</strong>hampion / <strong>H</strong>ero / <strong>M</strong>ythic) you still have available. Click any row to open the detail panel.',
    attachTo: at('.mcn-gear-table-wrap', 'top'),
    highlightClass: 'mcn-shepherd-highlight',
    buttons: [btnBack(t), btnDone(t)],
  });

  t.on('complete', () => { localStorage.setItem(GP_TOUR_KEY, '1'); _gpTour = null; });
  t.on('cancel',   () => { localStorage.setItem(GP_TOUR_KEY, '1'); _gpTour = null; });

  t.start();
}

// Auto-launch tour on first visit to the gear tab (idempotent — only fires once per page load)
function _gpMaybeLaunchTour() {
  if (localStorage.getItem(GP_TOUR_KEY)) return;
  if (_gpTourScheduled) return;
  _gpTourScheduled = true;
  setTimeout(() => {
    _gpTourScheduled = false;
    if (!localStorage.getItem(GP_TOUR_KEY)) _gpLaunchTour();
  }, 800);
}
