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
  html += '<div class="mc-prog-card">';
  html += '<div class="mc-prog-card__title">Raid Progression</div>';
  html += '<div class="mc-prog-card__body">';
  if (raid_progress && raid_progress.length > 0) {
    for (const tier of raid_progress) {
      html += `<div class="mc-raid-name">${tier.raid_name}</div>`;
      for (const diff of DIFF_ORDER) {
        html += renderRaidDiffRow(diff, tier.difficulties[diff] || null);
      }
    }
  } else {
    html += '<span class="mc-mplus-empty">No raid data synced yet</span>';
  }
  html += '</div></div>';

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
  makeCardsCollapsible(panel, 'mc-prog');
  panel.hidden = false;
}

// ---------------------------------------------------------------------------
// Parses panel helpers
// ---------------------------------------------------------------------------

const WCL_TIER_ORDER = ["lfr", "normal", "heroic", "mythic"];
const WCL_DIFF_LABELS = { lfr: "LFR", normal: "Normal", heroic: "Heroic", mythic: "Mythic" };

function parsePercentileTier(pct) {
  if (pct >= 100) return "pink";
  if (pct >= 99)  return "gold";
  if (pct >= 95)  return "orange";
  if (pct >= 75)  return "purple";
  if (pct >= 50)  return "blue";
  if (pct >= 25)  return "green";
  return "gray";
}

function ordinalSuffix(n) {
  const s = ["th","st","nd","rd"];
  const v = n % 100;
  return n + (s[(v-20)%10] || s[v] || s[0]);
}

function renderParsesPanel(data, charRealm, charName) {
  const panel = document.getElementById("mc-parses");
  const { tier_name, wcl_configured, parses, summary } = data;

  const wclCharUrl = `https://www.warcraftlogs.com/character/us/${charRealm}/${charName.toLowerCase()}`;

  // Handle WCL not configured
  if (!wcl_configured) {
    panel.innerHTML = `
      <div class="mc-prog-card">
        <div class="mc-prog-card__title">Warcraft Logs \u2014 Recent Parses</div>
        <div class="mc-prog-card__body">
          <div class="mc-parse-not-configured">WCL not configured \u2014 contact an officer to set up Warcraft Logs integration.</div>
        </div>
      </div>`;
    panel.hidden = false;
    return;
  }

  // Group by difficulty
  const byDiff = {};
  for (const p of (parses || [])) {
    if (!byDiff[p.difficulty]) byDiff[p.difficulty] = [];
    byDiff[p.difficulty].push(p);
  }

  const availDiffs = WCL_TIER_ORDER.filter(d => byDiff[d] && byDiff[d].length > 0);

  // No data state
  if (!parses || parses.length === 0) {
    panel.innerHTML = `
      <div class="mc-prog-card">
        <div class="mc-prog-card__title">Warcraft Logs \u2014 Recent Parses</div>
        <div class="mc-prog-card__body">
          <span class="mc-parse-empty">No Warcraft Logs data found for this character.</span>
          <a href="${wclCharUrl}" target="_blank" rel="noopener noreferrer" class="mc-parse-wcl-link">View on Warcraft Logs \u2197</a>
        </div>
      </div>`;
    panel.hidden = false;
    return;
  }

  // Default tab: heroic if available, else first with data
  let activeTab = availDiffs.includes("heroic") ? "heroic" : availDiffs[0];

  function buildTableHtml(diff) {
    const rows = (byDiff[diff] || []).slice().sort((a, b) => b.percentile - a.percentile);
    if (!rows.length) return '<span class="mc-parse-empty">No data for this difficulty.</span>';
    let html = `
      <table class="mc-parse-table">
        <thead><tr>
          <th>Boss</th>
          <th>Parse %</th>
          <th>Best Parse</th>
        </tr></thead>
        <tbody>`;
    for (const row of rows) {
      const tier = parsePercentileTier(row.percentile);
      const pct = Math.round(row.percentile);
      const rowClass = tier === "gray" ? "mc-parse-row--gray" : (tier === "gold" ? "mc-parse-row--legendary" : "");
      const bossLink = row.report_code
        ? `<a href="https://www.warcraftlogs.com/reports/${row.report_code}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none;">${row.boss_name}</a>`
        : row.boss_name;
      html += `
        <tr class="${rowClass}">
          <td>${bossLink}</td>
          <td><span class="mc-parse-pct mc-parse--${tier}">${ordinalSuffix(pct)}</span></td>
          <td>
            <div class="mc-parse-bar-wrap">
              <div class="mc-parse-bar-fill mc-parse-bar--${tier}" style="width:${pct}%"></div>
            </div>
          </td>
        </tr>`;
    }
    html += '</tbody></table>';
    return html;
  }

  function buildSummaryHtml() {
    if (!summary) return '';
    const parts = [];
    if (summary.best_percentile != null) {
      parts.push(`Best this tier: <strong>${ordinalSuffix(Math.round(summary.best_percentile))} percentile</strong> (${WCL_DIFF_LABELS[summary.best_difficulty] || summary.best_difficulty}, ${summary.best_boss})`);
    }
    if (summary.heroic_average != null) {
      parts.push(`Heroic avg: <strong>${ordinalSuffix(Math.round(summary.heroic_average))}</strong>`);
    }
    return parts.length ? `<div class="mc-parse-summary">${parts.map(p => `<span>${p}</span>`).join('')}</div>` : '';
  }

  function buildTabsHtml(activeDiff) {
    return `<div class="mc-parse-tabs" id="mc-parse-tabs">
      ${availDiffs.map(d => `<button class="mc-parse-tab${d === activeDiff ? ' mc-parse-tab--active' : ''}" data-diff="${d}">${WCL_DIFF_LABELS[d] || d}</button>`).join('')}
    </div>`;
  }

  const tierLabel = tier_name ? ` \u2014 ${tier_name}` : '';

  function renderFull(activeDiff) {
    panel.innerHTML = `
      <div class="mc-prog-card">
        <div class="mc-prog-card__title">Warcraft Logs${tierLabel}</div>
        <div class="mc-prog-card__body">
          ${buildSummaryHtml()}
          ${availDiffs.length > 1 ? buildTabsHtml(activeDiff) : ''}
          <div id="mc-parse-table-body">${buildTableHtml(activeDiff)}</div>
          <a href="${wclCharUrl}" target="_blank" rel="noopener noreferrer" class="mc-parse-wcl-link" style="margin-top:0.75rem;">View full profile on Warcraft Logs \u2197</a>
        </div>
      </div>`;

    // Attach tab click handlers
    const tabsEl = panel.querySelector("#mc-parse-tabs");
    if (tabsEl) {
      tabsEl.querySelectorAll(".mc-parse-tab").forEach(btn => {
        btn.addEventListener("click", () => {
          activeTab = btn.dataset.diff;
          renderFull(activeTab);
        });
      });
    }
    makeCardsCollapsible(panel, 'mc-parses');
    panel.hidden = false;
  }

  renderFull(activeTab);
}

// ---------------------------------------------------------------------------
// Market panel helpers
// ---------------------------------------------------------------------------

function goldStr(copper) {
  if (!copper || copper <= 0) return '\u2014';
  const gold = Math.floor(copper / 10000);
  const silver = Math.floor((copper % 10000) / 100);
  if (gold >= 1000) return gold.toLocaleString() + 'g';
  if (gold > 0) return `${gold}g ${silver}s`;
  return `${silver}s`;
}

function renderMarketPanel(data) {
  const panel = document.getElementById('mc-market');
  const { prices, available } = data;

  if (!available || !prices || prices.length === 0) {
    panel.innerHTML = `
      <div class="mc-prog-card">
        <div class="mc-prog-card__title">Market Watch</div>
        <div class="mc-prog-card__body">
          <span class="mc-mplus-empty">No market data available for your realm yet.</span>
        </div>
      </div>`;
    panel.hidden = false;
    return;
  }

  const rows = prices.map(item => {
    const realmCls = item.is_realm_specific ? ' mc-market-row--realm' : '';
    const realmFlag = item.is_realm_specific ? '<span class="mc-market-realm-flag">*</span>' : '';
    const wowheadName = item.item_name.replace(/ /g, '+').replace(/'/g, '%27');
    const qty = item.quantity_available ? item.quantity_available.toLocaleString() : '\u2014';
    return `<tr class="${realmCls}">
      <td class="mc-market-name">
        <span class="mc-market-cat mc-market-cat--${item.category}">${item.category}</span>
        <a href="https://www.wowhead.com/search?q=${wowheadName}" target="_blank" rel="noopener noreferrer" class="mc-market-item-link">${item.item_name}</a>${realmFlag}
      </td>
      <td class="mc-market-price">${goldStr(item.min_buyout)}</td>
      <td class="mc-market-qty">${qty}</td>
    </tr>`;
  }).join('');

  const hasRealmSpecific = prices.some(p => p.is_realm_specific);
  const footnote = hasRealmSpecific
    ? '<p class="mc-market-footnote">* Realm-specific auction price.</p>'
    : '';

  panel.innerHTML = `
    <div class="mc-prog-card">
      <div class="mc-prog-card__title">Market Watch</div>
      <div class="mc-prog-card__body">
        <table class="mc-market-table">
          <thead><tr><th>Item</th><th>Min Price</th><th>Available</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        ${footnote}
      </div>
    </div>`;
  makeCardsCollapsible(panel, 'mc-market');
  panel.hidden = false;
}

// ---------------------------------------------------------------------------
// Collapsible card helper (shared by all panels)
// ---------------------------------------------------------------------------

function makeCardsCollapsible(containerEl, keyPrefix) {
  containerEl.querySelectorAll('.mc-prog-card').forEach((card, i) => {
    const title = card.querySelector('div.mc-prog-card__title');
    if (!title) return;
    const key = `${keyPrefix}-${i}`;
    if (localStorage.getItem(key) === '1') {
      card.classList.add('mc-prog-card--collapsed');
    }
    title.addEventListener('click', () => {
      const collapsed = card.classList.toggle('mc-prog-card--collapsed');
      localStorage.setItem(key, collapsed ? '1' : '0');
    });
  });
}

// ---------------------------------------------------------------------------
// Crafting & Raid Prep panel helpers
// ---------------------------------------------------------------------------

function craftingCollapseState(key) {
  const val = localStorage.getItem(key);
  return val === null ? true : val === '1'; // default open
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _updateCraftingTable() {
  const prof = document.getElementById('mc-craft-prof')?.value || '';
  const tier = document.getElementById('mc-craft-tier')?.value || '';
  const search = (document.getElementById('mc-craft-search')?.value || '').trim();

  let filtered = _craftableAll;

  if (search.length >= 2) {
    const q = search.toLowerCase();
    filtered = _craftableAll.filter(r => r.recipe_name.toLowerCase().includes(q));
  } else {
    if (prof) filtered = filtered.filter(r => r.profession === prof);
    if (tier) filtered = filtered.filter(r => r.tier_name === tier);
  }

  const tbody = document.getElementById('mc-craft-tbody');
  if (!tbody) return;

  tbody.innerHTML = filtered.map(r => {
    const expansion = r.expansion_name
      ? `<span class="mc-craft-expansion">${escHtml(r.expansion_name)}</span>`
      : '';
    return `<tr>
      <td class="mc-craft-prof-cell">${escHtml(r.profession)}${expansion}</td>
      <td><a href="${r.wowhead_url}" target="_blank" rel="noopener noreferrer" class="mc-craft-link">${escHtml(r.recipe_name)}</a></td>
    </tr>`;
  }).join('');

  const count = document.getElementById('mc-craft-count');
  if (count) {
    count.textContent = filtered.length !== _craftableAll.length
      ? `Showing ${filtered.length} of ${_craftableAll.length} recipes`
      : '';
  }
}

function _onCraftProfChange() {
  const prof = document.getElementById('mc-craft-prof')?.value || '';
  const tierSelect = document.getElementById('mc-craft-tier');
  if (!tierSelect) return;

  tierSelect.innerHTML = '<option value="">All Expansions</option>';
  tierSelect.disabled = true;

  if (prof) {
    const seen = new Set();
    const tiers = [];
    for (const r of _craftableAll) {
      if (r.profession === prof && r.tier_name && !seen.has(r.tier_name)) {
        seen.add(r.tier_name);
        tiers.push({ value: r.tier_name, label: r.expansion_name || r.tier_name });
      }
    }
    tiers.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.value;
      opt.textContent = t.label;
      tierSelect.appendChild(opt);
    });
    if (tiers.length > 1) tierSelect.disabled = false;
  }

  _updateCraftingTable();
}

function renderCraftingPanel(data, charName) {
  const panel = document.getElementById('mc-crafting');
  const { craftable, consumables } = data;
  _craftableAll = craftable || [];

  const parts = [];

  // Section A: What I Can Craft
  if (craftable && craftable.length > 0) {
    const openAttr = craftingCollapseState('mc-crafting-recipes') ? ' open' : '';
    const seenProfs = new Set();
    const professions = [];
    for (const r of craftable) {
      if (!seenProfs.has(r.profession)) { seenProfs.add(r.profession); professions.push(r.profession); }
    }
    professions.sort();
    const profOptions = professions.map(p =>
      `<option value="${escHtml(p)}">${escHtml(p)}</option>`
    ).join('');
    parts.push(`
      <details class="mc-crafting-section" data-collapse-key="mc-crafting-recipes"${openAttr}>
        <summary class="mc-prog-card__title">What ${escHtml(charName)} Can Craft (${craftable.length})</summary>
        <div class="mc-prog-card__body" style="padding:0">
          <div class="mc-craft-filters">
            <select id="mc-craft-prof" class="mc-craft-select">
              <option value="">All Professions</option>${profOptions}
            </select>
            <select id="mc-craft-tier" class="mc-craft-select" disabled>
              <option value="">All Expansions</option>
            </select>
            <input type="text" id="mc-craft-search" class="mc-craft-search" placeholder="Search recipes\u2026" autocomplete="off">
          </div>
          <table class="mc-craft-table">
            <thead><tr><th>Profession</th><th>Recipe</th></tr></thead>
            <tbody id="mc-craft-tbody"></tbody>
          </table>
          <div id="mc-craft-count" class="mc-craft-count"></div>
        </div>
      </details>`);
  }

  // Section B: Raid Consumables
  if (consumables && consumables.length > 0) {
    const openAttr = craftingCollapseState('mc-crafting-consumables') ? ' open' : '';
    const rows = consumables.map(item => {
      const price = item.min_buyout ? item.min_buyout_display : '\u2014';
      let status;
      if (!item.min_buyout) {
        status = '<span class="mc-cons-status--na">\u2014</span>';
      } else if (item.quantity_available != null && item.quantity_available < 50) {
        status = `<span class="mc-cons-status--low">\u26A0 Low stock (${item.quantity_available})</span>`;
      } else if (item.change_pct != null && item.change_pct > 5) {
        status = `<span class="mc-cons-status--up">\uD83D\uDCC8 +${item.change_pct.toFixed(1)}%</span>`;
      } else if (item.change_pct != null && item.change_pct < -5) {
        status = `<span class="mc-cons-status--down">\uD83D\uDCC9 ${item.change_pct.toFixed(1)}%</span>`;
      } else {
        status = '<span class="mc-cons-status--stable">\u2705 stable</span>';
      }
      const catHtml = `<span class="mc-market-cat mc-market-cat--${item.category}">${item.category}</span>`;
      return `<tr>
        <td>${catHtml}<a href="${item.wowhead_url}" target="_blank" rel="noopener noreferrer" class="mc-craft-link">${item.item_name}</a></td>
        <td class="mc-cons-price">${price}</td>
        <td class="mc-cons-status-cell">${status}</td>
      </tr>`;
    }).join('');
    parts.push(`
      <details class="mc-crafting-section" data-collapse-key="mc-crafting-consumables"${openAttr}>
        <summary class="mc-prog-card__title">Raid Consumables \u2014 Current Prices</summary>
        <div class="mc-prog-card__body" style="padding:0">
          <table class="mc-craft-table">
            <thead><tr><th>Item</th><th>Price</th><th>Status</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </details>`);
  }

  if (parts.length === 0) {
    panel.innerHTML = `
      <div class="mc-prog-card">
        <div class="mc-prog-card__title">Crafting &amp; Raid Prep</div>
        <div class="mc-prog-card__body">
          <span class="mc-mplus-empty">No crafting data available for this character.</span>
        </div>
      </div>`;
    panel.hidden = false;
    return;
  }

  panel.innerHTML = parts.join('');
  panel.hidden = false;

  // Initialize crafting recipe filters
  if (_craftableAll.length > 0) {
    _updateCraftingTable();
    document.getElementById('mc-craft-prof')?.addEventListener('change', _onCraftProfChange);
    document.getElementById('mc-craft-tier')?.addEventListener('change', _updateCraftingTable);
    document.getElementById('mc-craft-search')?.addEventListener('input', _updateCraftingTable);
  }

  // Persist collapse state on toggle
  panel.querySelectorAll('details.mc-crafting-section').forEach(det => {
    det.addEventListener('toggle', () => {
      const key = det.dataset.collapseKey;
      if (key) localStorage.setItem(key, det.open ? '1' : '0');
    });
  });
}

// ---------------------------------------------------------------------------
// Out-of-guild characters
// ---------------------------------------------------------------------------

function renderOutOfGuild(chars) {
  const section = document.getElementById("oog-section");
  const grid = document.getElementById("oog-grid");
  if (!section || !grid) return;
  if (!chars || chars.length === 0) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  grid.innerHTML = chars.map(c => `
    <div class="oog-card">
      <span class="oog-card__name">${c.name}</span>
      <span class="oog-card__realm">${c.realm}</span>
      ${c.class ? `<span class="oog-card__class">${c.class}</span>` : ""}
      <span class="oog-card__level">Level ${c.level}</span>
    </div>
  `).join("");
}

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

let _allChars = [];
let _craftableAll = [];

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

  // Load parses panel
  const parsesPanel = document.getElementById("mc-parses");
  parsesPanel.hidden = true;
  parsesPanel.innerHTML = "";

  try {
    const parsesResp = await fetch(`/api/v1/me/character/${charId}/parses`, { credentials: "include" });
    if (parsesResp.ok) {
      const parsesJson = await parsesResp.json();
      if (parsesJson.ok) {
        renderParsesPanel(parsesJson.data, char.realm_slug, char.character_name);
      }
    }
  } catch (err) {
    // Parses are non-critical — fail silently
    console.warn("Parses load failed:", err);
  }

  // Load market panel
  const marketPanel = document.getElementById('mc-market');
  marketPanel.hidden = true;
  marketPanel.innerHTML = '';

  try {
    const marketResp = await fetch(`/api/v1/me/character/${charId}/market`, { credentials: 'include' });
    if (marketResp.ok) {
      const marketJson = await marketResp.json();
      if (marketJson.ok) {
        renderMarketPanel(marketJson.data);
      }
    }
  } catch (err) {
    // Market is non-critical — fail silently
    console.warn('Market load failed:', err);
  }

  // Load crafting panel
  const craftingPanel = document.getElementById('mc-crafting');
  craftingPanel.hidden = true;
  craftingPanel.innerHTML = '';

  try {
    const craftingResp = await fetch(`/api/v1/me/character/${charId}/crafting`, { credentials: 'include' });
    if (craftingResp.ok) {
      const craftingJson = await craftingResp.json();
      if (craftingJson.ok) {
        renderCraftingPanel(craftingJson.data, char.character_name);
      }
    }
  } catch (err) {
    // Crafting is non-critical — fail silently
    console.warn('Crafting load failed:', err);
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

    const { characters, default_character_id, out_of_guild_characters } = json.data;
    loading.hidden = true;

    // Render out-of-guild section (always, even if no in-guild chars)
    renderOutOfGuild(out_of_guild_characters || []);

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

// ---------------------------------------------------------------------------
// Refresh Characters button
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("btn-refresh-chars");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Refreshing\u2026";

    try {
      const resp = await fetch(
        `/api/v1/me/bnet-sync?next=${encodeURIComponent(window.location.pathname)}`,
        { method: "POST", credentials: "include" }
      );
      const data = await resp.json();

      if (data.redirect) {
        // Not linked or token expired — go through OAuth
        window.location.href = data.redirect;
        return;
      }

      if (data.ok) {
        // Sync happened — reload to show updated character list
        window.location.reload();
      }
    } catch (err) {
      console.error("Character refresh failed:", err);
      btn.disabled = false;
      btn.textContent = "Refresh Characters";
    }
  });
});

document.addEventListener("DOMContentLoaded", init);
