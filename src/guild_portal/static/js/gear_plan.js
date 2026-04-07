/**
 * gear_plan.js — Personal Gear Plan paperdoll page
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────

let _state = {
  characters: [],
  activeCharId: null,
  plan: null,
  slots: {},
  bisSources: [],
  heroTalents: [],
  trackColors: {},
  openSlot: null,
};

// WoW paperdoll layout
// Left:    Head → Wrist  (shirt + tabard are cosmetic/inactive)
// Right:   Hands → Trinket 2
// Centre:  Main Hand + Off Hand  (at grid bottom)
const LEFT_SLOTS    = ['head','neck','shoulder','back','chest','shirt','tabard','wrist'];
const RIGHT_SLOTS   = ['hands','waist','legs','feet','ring_1','ring_2','trinket_1','trinket_2'];
const WEAPON_SLOTS  = ['main_hand','off_hand'];

// Cosmetic slots: synced from Blizzard for display but no BIS / upgrade logic.
const INACTIVE_SLOTS = new Set(['shirt','tabard']);

const SLOT_LABELS = {
  head:'Head', neck:'Neck', shoulder:'Shoulder', back:'Back',
  chest:'Chest', shirt:'Shirt', tabard:'Tabard', wrist:'Wrist',
  hands:'Hands', waist:'Waist', legs:'Legs', feet:'Feet',
  ring_1:'Ring 1', ring_2:'Ring 2',
  trinket_1:'Trinket 1', trinket_2:'Trinket 2',
  main_hand:'Main Hand', off_hand:'Off Hand',
};

// ── Helpers ───────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function trackColor(t) { return _state.trackColors[t] || '#888'; }

function trackBadge(t) {
  return `<span class="gp-track" style="background:${esc(trackColor(t))}" title="${esc(t)} track">${esc(t)}</span>`;
}

function craftedBadge() {
  return `<span class="gp-track gp-track--crafted" title="Crafted item">Crafted</span>`;
}

/**
 * Build a provider × item BIS recommendation grid for a drawer section.
 * Rows = unique items, sorted by how many sources recommend them (desc), then alpha.
 * Columns = one per active source. Cells = ✓ or greyed dash.
 */
/**
 * @param {string}      slotKey
 * @param {Array}       bis        bis_recommendations array from slot data
 * @param {number|null} primaryBid blizzard_item_id to pin to top (slot's effective desired item)
 */
function renderBisGrid(slotKey, bis, primaryBid = null) {
  if (!bis.length) {
    return '<div class="gp-drawer-empty">No BIS data for this slot</div>';
  }

  // Unique sources in appearance order
  const srcMap = new Map(); // source_id → short_label
  for (const r of bis) {
    if (!srcMap.has(r.source_id)) {
      srcMap.set(r.source_id, r.short_label || r.source_name || `Source ${r.source_id}`);
    }
  }
  const sources = [...srcMap.entries()].map(([id, label]) => ({ id, label }));

  // Unique items + which sources include them
  const itemMap = new Map(); // blizzard_item_id → {bid, name, icon, srcIds}
  for (const r of bis) {
    const bid = r.blizzard_item_id;
    if (!itemMap.has(bid)) {
      itemMap.set(bid, { bid, name: r.item_name, icon: r.icon_url, srcIds: new Set() });
    }
    itemMap.get(bid).srcIds.add(r.source_id);
  }

  // Sort: pin the slot's effective desired item first (so ring_1 and ring_2 each
  // show their own matched BIS item at the top), then most-recommended, then alpha.
  const items = [...itemMap.values()].sort((a, b) => {
    if (primaryBid) {
      const aPin = a.bid === primaryBid ? 1 : 0;
      const bPin = b.bid === primaryBid ? 1 : 0;
      if (aPin !== bPin) return bPin - aPin;
    }
    const d = b.srcIds.size - a.srcIds.size;
    return d !== 0 ? d : a.name.localeCompare(b.name);
  });

  const headerCells = sources.map(s =>
    `<th class="gp-bis-grid__src" title="${esc(s.label)}">${esc(s.label)}</th>`
  ).join('');

  const bodyRows = items.map(item => {
    const cells = sources.map(s =>
      item.srcIds.has(s.id)
        ? `<td class="gp-bis-grid__check gp-bis-grid__check--yes">✓</td>`
        : `<td class="gp-bis-grid__check gp-bis-grid__check--no">—</td>`
    ).join('');
    const iconHtml = item.icon
      ? `<img class="gp-bis-grid__icon" src="${esc(item.icon)}" alt="" loading="lazy">`
      : `<span class="gp-bis-grid__icon-ph"></span>`;
    return `
      <tr>
        <td class="gp-bis-grid__name" title="${esc(item.name)}">
          <div class="gp-bis-grid__name-inner">${iconHtml}<a href="https://www.wowhead.com/item=${item.bid}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none">${esc(item.name)}</a></div>
        </td>
        ${cells}
        <td class="gp-bis-grid__action">
          <button class="btn btn-sm btn-secondary"
                  style="padding:0.1rem 0.4rem;font-size:0.72rem"
                  onclick="setDesiredItem('${esc(slotKey)}',${item.bid})">Use</button>
        </td>
      </tr>`;
  }).join('');

  return `
    <table class="gp-bis-grid">
      <thead>
        <tr>
          <th class="gp-bis-grid__name-col">Item</th>
          ${headerCells}
          <th></th>
        </tr>
      </thead>
      <tbody>${bodyRows}</tbody>
    </table>`;
}

function buildIcon(src, qColor) {
  const img = document.createElement('img');
  img.className = 'gp-slot-card__icon';
  img.src = src;
  img.alt = '';
  img.loading = 'lazy';
  // Use border-color + outer box-shadow: inset shadows are drawn behind the
  // image's own pixels and would be invisible. Outer glow works fine as long
  // as the parent has no overflow:hidden.
  if (qColor) {
    img.style.borderColor = qColor;
    img.style.boxShadow   = `0 0 6px ${qColor}80`;
  } else {
    img.style.borderColor = 'rgba(255,255,255,0.15)';
    img.style.boxShadow   = '';
  }
  return img;
}

// Fetch icon from Wowhead cache and swap it in once loaded.
// Called when the equipped item exists but has no cached icon_url yet.
async function fetchSlotIcon(blizzardItemId, iconEl, qColor) {
  try {
    const resp = await apiFetch(`/api/v1/items/${blizzardItemId}`);
    if (resp.ok && resp.data?.icon_url) {
      // Only update if the element is still in the DOM (user may have changed char)
      if (iconEl.isConnected) {
        iconEl.innerHTML = '';
        iconEl.appendChild(buildIcon(resp.data.icon_url, qColor));
      }
    }
  } catch { /* non-critical — placeholder stays */ }
}

async function apiFetch(url, opts = {}) {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    ...opts,
  });
  try { return await r.json(); } catch { return { ok: false, error: `HTTP ${r.status}` }; }
}

function showStatus(msg, type) {
  const el = $('gp-status');
  el.textContent = msg;
  el.className = `gp-status gp-status--${type}`;
  el.hidden = false;
}

function clearStatus() {
  $('gp-status').hidden = true;
}

function setLoading(on) {
  $('gp-loading').hidden = !on;
}

function showError(msg) {
  const el = $('gp-error');
  el.textContent = msg;
  el.hidden = false;
}

// ── Init ──────────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', init);

async function init() {
  // Validate all critical DOM elements are present before doing anything.
  // If any are missing it usually means a stale cached JS is running against
  // a newer (or older) template — hard-refresh (Ctrl+Shift+R) fixes it.
  const REQUIRED_IDS = [
    'gp-loading','gp-error','gp-no-chars','gp-main',
    'gp-col-left','gp-col-right','gp-col-weapons','gp-center','gp-status',
    'gp-char-select','gp-ht-select','gp-source-select',
    'gp-drawer','gp-simc-modal',
  ];
  const missing = REQUIRED_IDS.filter(id => !document.getElementById(id));
  if (missing.length) {
    console.error('[GearPlan] Missing DOM elements (stale cache?):', missing);
    document.body.insertAdjacentHTML('afterbegin',
      `<div style="background:#f87171;color:#000;padding:1rem;font-weight:bold;text-align:center">
        Gear Plan failed to initialise — missing elements: ${missing.join(', ')}.<br>
        Please hard-refresh the page (Ctrl+Shift+R / Cmd+Shift+R).
      </div>`);
    return;
  }

  await loadCharacters();

  $('gp-char-select')   .addEventListener('change', onCharChange);
  $('gp-ht-select')     .addEventListener('change', onConfigChange);
  $('gp-source-select') .addEventListener('change', onConfigChange);
  $('gp-btn-sync')      .addEventListener('click',  onSyncGear);
  $('gp-btn-populate')  .addEventListener('click',  onPopulate);
  $('gp-btn-import-simc').addEventListener('click', () => showSimcModal());
  $('gp-btn-export-simc').addEventListener('click', onExportSimc);
  $('gp-btn-delete-plan').addEventListener('click', onDeletePlan);
  $('gp-drawer-close')  .addEventListener('click',  closeDrawer);
  $('gp-simc-submit')   .addEventListener('click',  onSimcImport);
  $('gp-simc-cancel')   .addEventListener('click',  hideSimcModal);
  $('gp-simc-close')    .addEventListener('click',  hideSimcModal);
  $('gp-simc-modal').querySelector('.gp-modal__backdrop').addEventListener('click', hideSimcModal);
}

// ── Load characters ───────────────────────────────────────────────────────

async function loadCharacters() {
  setLoading(true);
  try {
    const resp = await apiFetch('/api/v1/me/characters');
    if (!resp.ok) throw new Error(resp.error || 'Failed to load characters');

    const chars = (resp.data && resp.data.characters) || [];
    const inGuild = chars.filter(c => c.in_guild !== false);

    if (!inGuild.length) {
      setLoading(false);
      $('gp-no-chars').hidden = false;
      return;
    }

    _state.characters = inGuild;
    populateCharSelector(inGuild);
    setLoading(false);
    $('gp-char-select').hidden = false;

    const defaultId = resp.data.default_character_id;
    const startId = (defaultId && inGuild.find(c => c.id === defaultId))
      ? defaultId
      : inGuild[0].id;

    $('gp-char-select').value = startId;
    await loadPlan(parseInt(startId, 10));
  } catch (err) {
    setLoading(false);
    showError(err.message);
  }
}

function populateCharSelector(chars) {
  const sel = $('gp-char-select');
  sel.innerHTML = '';
  for (const c of chars) {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = `${c.character_name} (${c.realm_slug})`;
    sel.appendChild(opt);
  }
}

async function onCharChange() {
  const charId = parseInt($('gp-char-select').value, 10);
  closeDrawer();
  await loadPlan(charId);
}

// ── Load plan ─────────────────────────────────────────────────────────────

async function loadPlan(charId) {
  _state.activeCharId = charId;
  $('gp-main').hidden = true;
  showStatus('Loading…', 'info');

  try {
    const resp = await apiFetch(`/api/v1/me/gear-plan/${charId}`);
    if (!resp.ok) throw new Error(resp.error || 'Failed to load plan');

    const data = resp.data;
    _state.plan       = data.plan;
    _state.slots      = data.slots;
    _state.bisSources = data.bis_sources || [];
    _state.heroTalents = data.hero_talents || [];
    _state.trackColors = data.track_colors || {};

    updateCharBadge(charId);
    updateHtSelect();
    updateSourceSelect();
    renderPaperdoll();

    $('gp-main').hidden = false;
    clearStatus();
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

// ── Config controls ───────────────────────────────────────────────────────

function updateCharBadge(charId) {
  const char = _state.characters.find(c => c.id === charId);
  if (!char) return;
  const nameEl = $('gp-char-name');
  const metaEl = $('gp-char-meta');
  if (nameEl) nameEl.textContent = char.character_name;
  if (metaEl) {
    const spec = _state.plan?.spec_name || char.spec_name || '';
    const cls  = char.class_name || '';
    metaEl.textContent = [spec, cls, char.realm_slug].filter(Boolean).join(' · ');
  }
}

function updateHtSelect() {
  const sel = $('gp-ht-select');
  sel.innerHTML = '<option value="">— Any —</option>';
  for (const ht of _state.heroTalents) {
    const opt = document.createElement('option');
    opt.value = ht.id;
    opt.textContent = ht.name;
    if (_state.plan?.hero_talent_id === ht.id) opt.selected = true;
    sel.appendChild(opt);
  }
}

function updateSourceSelect() {
  const sel = $('gp-source-select');
  sel.innerHTML = '';
  for (const src of _state.bisSources) {
    const opt = document.createElement('option');
    opt.value = src.id;
    opt.textContent = src.name;
    if (_state.plan?.bis_source_id === src.id) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function onConfigChange() {
  const htId  = $('gp-ht-select').value  ? parseInt($('gp-ht-select').value, 10)  : null;
  const srcId = $('gp-source-select').value ? parseInt($('gp-source-select').value, 10) : null;
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/config`, {
    method: 'PATCH',
    body: JSON.stringify({ hero_talent_id: htId, bis_source_id: srcId }),
  });
  if (resp.ok) await loadPlan(_state.activeCharId);
  else showStatus(resp.error || 'Config update failed', 'err');
}

// ── Paperdoll rendering ───────────────────────────────────────────────────

function renderPaperdoll() {
  const leftEl    = $('gp-col-left');
  const rightEl   = $('gp-col-right');
  const weaponsEl = $('gp-col-weapons');
  leftEl.innerHTML    = '';
  rightEl.innerHTML   = '';
  weaponsEl.innerHTML = '';

  for (const slot of LEFT_SLOTS)   leftEl.appendChild(buildSlotCard(slot));
  for (const slot of RIGHT_SLOTS)  rightEl.appendChild(buildSlotCard(slot));
  for (const slot of WEAPON_SLOTS) weaponsEl.appendChild(buildSlotCard(slot));
  if (window.$WowheadPower) window.$WowheadPower.refreshLinks();
}

function buildSlotCard(slotKey) {
  const isInactive = INACTIVE_SLOTS.has(slotKey);
  const sd = _state.slots[slotKey] || {};
  const eq = sd.equipped;
  const desired = sd.desired;
  const upgrades = isInactive ? [] : (sd.upgrade_tracks || []);
  const bisRecs  = isInactive ? [] : (sd.bis_recommendations || []);

  // Equipped item display values
  let iconSrc = null, dispName = null, dispIlvl = null, dispTrack = null;
  if (eq && eq.blizzard_item_id) {
    iconSrc   = eq.icon_url;
    dispName  = eq.item_name;
    dispIlvl  = eq.item_level;
    dispTrack = eq.quality_track;
  }

  // Crafted items use a neutral gold; otherwise use quality track colour
  const isCrafted = eq?.is_crafted || false;
  const qColor = dispTrack ? trackColor(dispTrack) : (isCrafted ? '#c0a060' : null);

  // Goal item (only for active slots)
  const primaryBis = !isInactive
    ? (bisRecs.find(r => r.source_id === _state.plan?.bis_source_id) || bisRecs[0])
    : null;
  const goalItem = !isInactive ? (desired || primaryBis) : null;
  const showGoal = goalItem && (!eq || goalItem.blizzard_item_id !== eq?.blizzard_item_id);

  const card = document.createElement('div');
  card.className = 'gp-slot-card';
  card.dataset.slot = slotKey;
  if (isInactive) {
    card.classList.add('is-inactive');
  } else {
    if (_state.openSlot === slotKey) card.classList.add('is-open');
    if (sd.is_bis) card.classList.add('is-bis');
    else if (sd.needs_upgrade) card.classList.add('needs-upgrade');
    card.addEventListener('click', () => toggleDrawer(slotKey));
  }

  // ── Icon ────────────────────────────────────────────────────────
  const iconEl = document.createElement('div');
  if (dispName) iconEl.title = dispName;
  if (iconSrc) {
    iconEl.appendChild(buildIcon(iconSrc, qColor));
  } else if (eq && eq.blizzard_item_id) {
    // Item exists but icon not yet cached — show placeholder and lazy-fetch
    const empty = document.createElement('div');
    empty.className = 'gp-slot-card__icon--empty';
    empty.textContent = (SLOT_LABELS[slotKey] || slotKey)[0];
    iconEl.appendChild(empty);
    fetchSlotIcon(eq.blizzard_item_id, iconEl, qColor);
  } else {
    const empty = document.createElement('div');
    empty.className = 'gp-slot-card__icon--empty';
    empty.textContent = SLOT_LABELS[slotKey] || slotKey;
    iconEl.appendChild(empty);
  }

  // ── Body ─────────────────────────────────────────────────────────
  const body = document.createElement('div');
  body.className = 'gp-slot-card__body';

  const label = document.createElement('div');
  label.className = 'gp-slot-card__label';
  label.textContent = SLOT_LABELS[slotKey] || slotKey;

  const name = document.createElement('div');
  name.className = 'gp-slot-card__name';
  name.title = dispName || '—';
  if (eq && eq.blizzard_item_id && dispName) {
    const link = document.createElement('a');
    link.href = `https://www.wowhead.com/item=${eq.blizzard_item_id}`;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = dispName;
    link.style.color = qColor || 'inherit';
    link.style.textDecoration = 'none';
    link.addEventListener('click', e => e.stopPropagation());
    name.appendChild(link);
  } else {
    name.textContent = dispName || '—';
    if (qColor) name.style.color = qColor;
  }

  const meta = document.createElement('div');
  meta.className = 'gp-slot-card__meta';
  if (dispIlvl) {
    const ilvl = document.createElement('span');
    ilvl.className = 'gp-slot-card__ilvl';
    ilvl.textContent = dispIlvl;
    meta.appendChild(ilvl);
  }
  if (isCrafted) {
    meta.innerHTML += craftedBadge();
  } else if (dispTrack) {
    meta.innerHTML += trackBadge(dispTrack);
  }

  body.appendChild(label);
  body.appendChild(name);
  body.appendChild(meta);

  // Goal row (active slots only)
  if (showGoal) {
    const goal = document.createElement('div');
    goal.className = 'gp-slot-card__goal';
    const gName = goalItem.item_name || goalItem.name || '?';
    if (goalItem.icon_url) {
      goal.innerHTML = `<img class="gp-slot-card__goal-icon" src="${esc(goalItem.icon_url)}" alt="" loading="lazy">`;
    } else {
      goal.innerHTML = `<span style="color:var(--color-accent)">→</span>`;
    }
    const gText = document.createElement('span');
    gText.textContent = gName;
    gText.title = gName;
    goal.appendChild(gText);
    body.appendChild(goal);
  }

  // Upgrade track row (active slots only)
  if (upgrades.length) {
    const upgradeRow = document.createElement('div');
    upgradeRow.className = 'gp-upgrade-row';
    upgradeRow.innerHTML = upgrades.map(t => trackBadge(t)).join('');
    body.appendChild(upgradeRow);
  }

  card.appendChild(iconEl);
  card.appendChild(body);
  return card;
}

// ── Drawer ────────────────────────────────────────────────────────────────

function toggleDrawer(slotKey) {
  if (_state.openSlot === slotKey) closeDrawer();
  else openDrawer(slotKey);
}

function openDrawer(slotKey) {
  _state.openSlot = slotKey;

  document.querySelectorAll('.gp-slot-card').forEach(c => {
    c.classList.toggle('is-open', c.dataset.slot === slotKey);
  });

  const sd = _state.slots[slotKey] || {};
  $('gp-drawer-title').textContent = `${SLOT_LABELS[slotKey] || slotKey}`;
  $('gp-drawer-body').innerHTML = renderDrawerBody(slotKey, sd);
  $('gp-drawer').hidden = false;
  $('gp-drawer').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  if (window.$WowheadPower) window.$WowheadPower.refreshLinks();
}

function closeDrawer() {
  _state.openSlot = null;
  document.querySelectorAll('.gp-slot-card').forEach(c => c.classList.remove('is-open'));
  $('gp-drawer').hidden = true;
}

function renderDrawerBody(slotKey, sd) {
  const eq      = sd.equipped;
  const desired = sd.desired;
  const bis     = sd.bis_recommendations || [];
  const sources = sd.item_sources || [];
  const tracks  = sd.available_tracks || [];
  const upgrades = sd.upgrade_tracks || [];

  // Section 1: Equipped
  let equippedHtml;
  if (eq && eq.blizzard_item_id) {
    const track = eq.quality_track ? trackBadge(eq.quality_track) : '';
    const qColor = eq.quality_track ? trackColor(eq.quality_track) : null;
    const nameStyle = qColor ? ` style="color:${qColor}"` : '';
    const borderStyle = qColor ? ` style="border-color:${qColor}"` : '';
    equippedHtml = `
      <div class="gp-drawer-item">
        ${eq.icon_url ? `<img class="gp-drawer-item__icon" src="${esc(eq.icon_url)}" alt="" loading="lazy"${borderStyle}>` : ''}
        <div class="gp-drawer-item__info">
          <div class="gp-drawer-item__name"${nameStyle}>
            <a href="https://www.wowhead.com/item=${eq.blizzard_item_id}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none">${esc(eq.item_name || 'Unknown')}</a>
          </div>
          <div class="gp-drawer-item__meta">${eq.item_level || ''}&nbsp;${track}</div>
          ${eq.enchant_id ? `<div class="gp-drawer-item__meta">Enchant: ${eq.enchant_id}</div>` : ''}
        </div>
      </div>`;
  } else {
    equippedHtml = '<div class="gp-drawer-empty">Nothing equipped</div>';
  }

  // Section 2: BIS recommendation grid
  // Paired slots (ring/trinket) share a combined pool — no pin, let source-count sort handle it.
  // Other slots pin their effective desired item to the top row.
  const _pairedSlots = new Set(['ring_1','ring_2','trinket_1','trinket_2']);
  const bisGridHtml = renderBisGrid(slotKey, bis, _pairedSlots.has(slotKey) ? null : (sd.desired_blizzard_item_id || null));

  // Section 3: Your selection
  let selectionHtml;
  if (desired && desired.blizzard_item_id) {
    const locked = desired.is_locked;
    selectionHtml = `
      <div class="gp-drawer-item" style="margin-bottom:0.5rem">
        ${desired.icon_url ? `<img class="gp-drawer-item__icon" src="${esc(desired.icon_url)}" alt="" loading="lazy">` : ''}
        <div class="gp-drawer-item__info">
          <div class="gp-drawer-item__name">
            <a href="https://www.wowhead.com/item=${desired.blizzard_item_id}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none">${esc(desired.item_name || 'Unknown')}</a>
          </div>
        </div>
      </div>
      <div style="display:flex;gap:0.4rem;flex-wrap:wrap">
        <button class="gp-lock-btn ${locked ? 'locked' : ''}"
                onclick="toggleLock('${esc(slotKey)}',${locked})">
          ${locked ? '🔒 Locked' : '🔓 Lock'}
        </button>
        <button class="btn btn-sm btn-secondary"
                onclick="clearSlot('${esc(slotKey)}')">Clear</button>
      </div>`;
  } else {
    selectionHtml = '<div class="gp-drawer-empty">No goal item set</div>';
  }

  // Manual lookup
  const manualHtml = `
    <div class="gp-manual-row">
      <input type="number" class="gp-manual-input" id="gp-mid-${esc(slotKey)}" placeholder="Item ID" min="1">
      <button class="btn btn-sm btn-secondary" onclick="fetchAndSetItem('${esc(slotKey)}')">Fetch</button>
    </div>`;

  // Section 4: Drop location + tracks
  let dropHtml;
  if (sources.length) {
    const loc = sources[0];
    const trackPills = tracks.map(t => trackBadge(t)).join(' ');
    const upgPills   = upgrades.map(t => trackBadge(t)).join(' ');
    dropHtml = `
      <div class="gp-drawer-item__meta" style="flex-wrap:wrap;gap:4px">
        <span>${esc(loc.source_name)}${loc.source_instance ? ` — ${esc(loc.source_instance)}` : ''}</span>
      </div>
      <div class="gp-drawer-item__meta" style="margin-top:4px">
        <span style="color:var(--color-text-muted);font-size:0.7rem">Available:</span>
        ${trackPills}
      </div>
      ${upgrades.length ? `
      <div class="gp-drawer-item__meta" style="margin-top:4px">
        <span style="color:var(--color-text-muted);font-size:0.7rem">Upgrade:</span>
        ${upgPills}
      </div>` : ''}`;
  } else {
    dropHtml = '<div class="gp-drawer-empty">No drop source data</div>';
  }

  return `
    <div>
      <div class="gp-drawer-section__title">Equipped</div>
      ${equippedHtml}
    </div>
    <div>
      <div class="gp-drawer-section__title">Your Goal</div>
      ${selectionHtml}
      ${manualHtml}
    </div>
    <div>
      <div class="gp-drawer-section__title">Drop Location</div>
      ${dropHtml}
    </div>
    <div class="gp-drawer__bis-section">
      <div class="gp-drawer-section__title">BIS Recommendations</div>
      ${bisGridHtml}
    </div>`;
}

// ── Drawer action globals ─────────────────────────────────────────────────

window.setDesiredItem = async function(slot, blizzardItemId, itemName) {
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/slot/${slot}`, {
    method: 'PUT',
    body: JSON.stringify({ blizzard_item_id: blizzardItemId, item_name: itemName }),
  });
  if (resp.ok) { showStatus('Goal updated', 'ok'); await reloadPlan(); }
  else showStatus(resp.error || 'Failed', 'err');
};

window.clearSlot = async function(slot) {
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/slot/${slot}`, {
    method: 'PUT',
    body: JSON.stringify({ blizzard_item_id: null }),
  });
  if (resp.ok) { showStatus('Slot cleared', 'ok'); await reloadPlan(); }
  else showStatus(resp.error || 'Failed', 'err');
};

window.toggleLock = async function(slot, currentlyLocked) {
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/slot/${slot}`, {
    method: 'PUT',
    body: JSON.stringify({ is_locked: !currentlyLocked }),
  });
  if (resp.ok) { showStatus(!currentlyLocked ? 'Slot locked' : 'Slot unlocked', 'ok'); await reloadPlan(); }
  else showStatus(resp.error || 'Failed', 'err');
};

window.fetchAndSetItem = async function(slot) {
  const input = document.getElementById(`gp-mid-${slot}`);
  const itemId = parseInt(input?.value, 10);
  if (!itemId) return;
  showStatus('Fetching item…', 'info');
  const itemResp = await apiFetch(`/api/v1/items/${itemId}`);
  if (!itemResp.ok) { showStatus(itemResp.error || 'Item not found', 'err'); return; }
  await window.setDesiredItem(slot, itemResp.data.blizzard_item_id, itemResp.data.name);
};

// ── Plan actions ──────────────────────────────────────────────────────────

async function onSyncGear() {
  showStatus('Syncing equipped gear…', 'info');
  const resp = await apiFetch(
    `/api/v1/me/gear-plan/${_state.activeCharId}/sync-equipment`,
    { method: 'POST' },
  );
  if (resp.ok) {
    showStatus('Gear synced — reloading…', 'ok');
    setTimeout(() => loadPlan(_state.activeCharId), 800);
  } else {
    showStatus(resp.error || 'Sync failed', 'err');
  }
}

async function onPopulate() {
  const srcId = $('gp-source-select').value ? parseInt($('gp-source-select').value, 10) : null;
  const htId  = $('gp-ht-select').value     ? parseInt($('gp-ht-select').value, 10)     : null;
  showStatus('Filling unlocked slots from BIS…', 'info');
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/populate`, {
    method: 'POST',
    body: JSON.stringify({ source_id: srcId, hero_talent_id: htId }),
  });
  if (resp.ok) {
    showStatus(`${resp.data?.populated || 0} slots filled`, 'ok');
    await reloadPlan();
  } else {
    showStatus(resp.error || 'Populate failed', 'err');
  }
}

async function onDeletePlan() {
  if (!confirm('Reset this gear plan? All goal items will be cleared.')) return;
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}`, { method: 'DELETE' });
  if (resp.ok) { showStatus('Plan reset', 'ok'); closeDrawer(); await loadPlan(_state.activeCharId); }
  else showStatus(resp.error || 'Failed', 'err');
}

async function onExportSimc() {
  showStatus('Generating SimC…', 'info');
  try {
    const resp = await fetch(`/api/v1/me/gear-plan/${_state.activeCharId}/export-simc`, {
      credentials: 'include',
    });
    if (!resp.ok) { const d = await resp.json().catch(() => ({})); showStatus(d.error || 'Export failed', 'err'); return; }
    const text = await resp.text();
    const a = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(new Blob([text], { type: 'text/plain' })),
      download: 'gear_plan.simc',
    });
    a.click();
    URL.revokeObjectURL(a.href);
    clearStatus();
  } catch (err) { showStatus(err.message, 'err'); }
}

// ── SimC modal ────────────────────────────────────────────────────────────

function showSimcModal() { $('gp-simc-modal').hidden = false; $('gp-simc-text').value = ''; $('gp-simc-text').focus(); }
function hideSimcModal() { $('gp-simc-modal').hidden = true; }

async function onSimcImport() {
  const text = $('gp-simc-text').value.trim();
  if (!text) return;
  hideSimcModal();
  showStatus('Importing…', 'info');
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/import-simc`, {
    method: 'POST',
    body: JSON.stringify({ simc_text: text }),
  });
  if (resp.ok) {
    const d = resp.data || {};
    showStatus(`Imported: ${d.populated||0} slots set${d.skipped_locked ? `, ${d.skipped_locked} locked skipped` : ''}`, 'ok');
    await reloadPlan();
  } else {
    showStatus(resp.error || 'Import failed', 'err');
  }
}

// ── Reload helper ─────────────────────────────────────────────────────────

async function reloadPlan() {
  const openSlot = _state.openSlot;
  closeDrawer();
  await loadPlan(_state.activeCharId);
  if (openSlot && _state.slots[openSlot]) openDrawer(openSlot);
}
