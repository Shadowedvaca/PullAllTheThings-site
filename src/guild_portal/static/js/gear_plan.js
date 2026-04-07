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

// WoW paperdoll layout — left column then right column
const LEFT_SLOTS  = ['head','neck','shoulder','back','chest','wrist','hands','waist'];
const RIGHT_SLOTS = ['legs','feet','ring_1','ring_2','trinket_1','trinket_2','main_hand','off_hand'];

const SLOT_LABELS = {
  head:'Head', neck:'Neck', shoulder:'Shoulder', back:'Back',
  chest:'Chest', wrist:'Wrist', hands:'Hands', waist:'Waist',
  legs:'Legs', feet:'Feet', ring_1:'Ring 1', ring_2:'Ring 2',
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
    'gp-col-left','gp-col-right','gp-center','gp-status',
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
  $('gp-char-badge__name') && ($('gp-char-badge__name').textContent = char.character_name);
  const nameEl = document.getElementById('gp-char-name');
  const metaEl = document.getElementById('gp-char-meta');
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
  const leftEl  = $('gp-col-left');
  const rightEl = $('gp-col-right');
  leftEl.innerHTML  = '';
  rightEl.innerHTML = '';

  for (const slot of LEFT_SLOTS)  leftEl.appendChild(buildSlotCard(slot));
  for (const slot of RIGHT_SLOTS) rightEl.appendChild(buildSlotCard(slot));
}

function buildSlotCard(slotKey) {
  const sd = _state.slots[slotKey] || {};
  const eq = sd.equipped;
  const desired = sd.desired;
  const upgrades = sd.upgrade_tracks || [];
  const bisRecs  = sd.bis_recommendations || [];

  // Determine icon + name to display (equipped item takes priority)
  let iconSrc = null, dispName = null, dispIlvl = null, dispTrack = null;
  if (eq && eq.blizzard_item_id) {
    iconSrc   = eq.icon_url;
    dispName  = eq.item_name;
    dispIlvl  = eq.item_level;
    dispTrack = eq.quality_track;
  }

  // Goal item (desired if different from equipped, or primary BIS rec if no desired set)
  const primaryBis = bisRecs.find(r => r.source_id === _state.plan?.bis_source_id) || bisRecs[0];
  const goalItem = desired || primaryBis;
  const showGoal = goalItem && (!eq || goalItem.blizzard_item_id !== eq?.blizzard_item_id);

  const card = document.createElement('div');
  card.className = 'gp-slot-card';
  card.dataset.slot = slotKey;
  if (_state.openSlot === slotKey) card.classList.add('is-open');
  if (sd.is_bis && !sd.needs_upgrade) card.classList.add('is-bis');
  else if (sd.needs_upgrade) card.classList.add('needs-upgrade');

  // Icon
  const iconEl = document.createElement('div');
  if (iconSrc) {
    const img = document.createElement('img');
    img.className = 'gp-slot-card__icon';
    img.src = iconSrc;
    img.alt = '';
    img.loading = 'lazy';
    iconEl.appendChild(img);
  } else {
    const empty = document.createElement('div');
    empty.className = 'gp-slot-card__icon--empty';
    empty.textContent = SLOT_LABELS[slotKey] || slotKey;
    iconEl.appendChild(empty);
  }

  // Body
  const body = document.createElement('div');
  body.className = 'gp-slot-card__body';

  const label = document.createElement('div');
  label.className = 'gp-slot-card__label';
  label.textContent = SLOT_LABELS[slotKey] || slotKey;

  const name = document.createElement('div');
  name.className = 'gp-slot-card__name';
  name.title = dispName || '—';
  name.textContent = dispName || '—';

  const meta = document.createElement('div');
  meta.className = 'gp-slot-card__meta';
  if (dispIlvl) {
    const ilvl = document.createElement('span');
    ilvl.className = 'gp-slot-card__ilvl';
    ilvl.textContent = dispIlvl;
    meta.appendChild(ilvl);
  }
  if (dispTrack) {
    meta.innerHTML += trackBadge(dispTrack);
  }

  body.appendChild(label);
  body.appendChild(name);
  body.appendChild(meta);

  // Goal row
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

  // Upgrade track row
  if (upgrades.length) {
    const upgradeRow = document.createElement('div');
    upgradeRow.className = 'gp-upgrade-row';
    upgradeRow.innerHTML = upgrades.map(t => trackBadge(t)).join('');
    body.appendChild(upgradeRow);
  }

  card.appendChild(iconEl);
  card.appendChild(body);
  card.addEventListener('click', () => toggleDrawer(slotKey));
  return card;
}

// ── Drawer ────────────────────────────────────────────────────────────────

function toggleDrawer(slotKey) {
  if (_state.openSlot === slotKey) closeDrawer();
  else openDrawer(slotKey);
}

function openDrawer(slotKey) {
  _state.openSlot = slotKey;

  // Highlight open card
  document.querySelectorAll('.gp-slot-card').forEach(c => {
    c.classList.toggle('is-open', c.dataset.slot === slotKey);
  });

  const sd = _state.slots[slotKey] || {};
  $('gp-drawer-title').textContent = `${SLOT_LABELS[slotKey] || slotKey}`;
  $('gp-drawer-body').innerHTML = renderDrawerBody(slotKey, sd);
  $('gp-drawer').hidden = false;
  $('gp-drawer').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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
    equippedHtml = `
      <div class="gp-drawer-item">
        ${eq.icon_url ? `<img class="gp-drawer-item__icon" src="${esc(eq.icon_url)}" alt="" loading="lazy">` : ''}
        <div class="gp-drawer-item__info">
          <div class="gp-drawer-item__name">${esc(eq.item_name || 'Unknown')}</div>
          <div class="gp-drawer-item__meta">${eq.item_level || ''}&nbsp;${track}</div>
          ${eq.enchant_id ? `<div class="gp-drawer-item__meta">Enchant: ${eq.enchant_id}</div>` : ''}
        </div>
      </div>`;
  } else {
    equippedHtml = '<div class="gp-drawer-empty">Nothing equipped</div>';
  }

  // Section 2: BIS recommendations
  let bisHtml;
  if (bis.length) {
    bisHtml = bis.map(r => `
      <div class="gp-bis-row">
        ${r.icon_url ? `<img class="gp-drawer-item__icon" style="width:24px;height:24px" src="${esc(r.icon_url)}" alt="" loading="lazy">` : ''}
        <span class="gp-bis-row__source">${esc(r.short_label || r.source_name)}</span>
        <span class="gp-bis-row__name">${esc(r.item_name)}</span>
        <button class="btn btn--sm btn--secondary" style="padding:0.1rem 0.4rem;font-size:0.72rem"
                onclick="setDesiredItem('${esc(slotKey)}',${r.blizzard_item_id},'${esc(r.item_name)}')">Use</button>
      </div>`).join('');
  } else {
    bisHtml = '<div class="gp-drawer-empty">No BIS data for this slot</div>';
  }

  // Section 3: Your selection
  let selectionHtml;
  if (desired && desired.blizzard_item_id) {
    const locked = desired.is_locked;
    selectionHtml = `
      <div class="gp-drawer-item" style="margin-bottom:0.5rem">
        ${desired.icon_url ? `<img class="gp-drawer-item__icon" src="${esc(desired.icon_url)}" alt="" loading="lazy">` : ''}
        <div class="gp-drawer-item__info">
          <div class="gp-drawer-item__name">${esc(desired.item_name || 'Unknown')}</div>
        </div>
      </div>
      <div style="display:flex;gap:0.4rem;flex-wrap:wrap">
        <button class="gp-lock-btn ${locked ? 'locked' : ''}"
                onclick="toggleLock('${esc(slotKey)}',${locked})">
          ${locked ? '🔒 Locked' : '🔓 Lock'}
        </button>
        <button class="btn btn--sm btn--secondary"
                onclick="clearSlot('${esc(slotKey)}')">Clear</button>
      </div>`;
  } else {
    selectionHtml = '<div class="gp-drawer-empty">No goal item set</div>';
  }

  // Manual lookup
  const manualHtml = `
    <div class="gp-manual-row">
      <input type="number" class="gp-manual-input" id="gp-mid-${esc(slotKey)}" placeholder="Item ID" min="1">
      <button class="btn btn--sm btn--secondary" onclick="fetchAndSetItem('${esc(slotKey)}')">Fetch</button>
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
      <div class="gp-drawer-section__title">BIS Recommendations</div>
      ${bisHtml}
    </div>
    <div>
      <div class="gp-drawer-section__title">Your Goal</div>
      ${selectionHtml}
      ${manualHtml}
    </div>
    <div>
      <div class="gp-drawer-section__title">Drop Location</div>
      ${dropHtml}
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
  showStatus('Syncing characters…', 'info');
  const resp = await apiFetch('/api/v1/me/bnet-sync', { method: 'POST' });
  if (resp.ok) {
    showStatus('Sync complete — reloading…', 'ok');
    setTimeout(() => loadPlan(_state.activeCharId), 1200);
  } else {
    showStatus(resp.error || 'Sync failed (Battle.net link required)', 'err');
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
