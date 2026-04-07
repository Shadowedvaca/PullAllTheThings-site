/**
 * gear_plan.js — Personal Gear Plan member page
 *
 * Manages: character selector, plan config (spec/HT/source),
 * 16-slot table with expand/collapse drawer, SimC import/export.
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────

let _state = {
  characters: [],         // [{id, character_name, realm_slug, class_name, class_color, spec_name}]
  activeCharId: null,
  plan: null,             // plan row from API
  slots: {},              // slot key → slot detail
  bisSources: [],         // [{id, name, short_label, content_type}]
  heroTalents: [],        // [{id, name, slug}]
  trackColors: {},        // {V: '#...', C: '#...', ...}
  openSlot: null,         // currently open drawer slot key
};

const SLOT_LABELS = {
  head: 'Head', neck: 'Neck', shoulder: 'Shoulder', back: 'Back',
  chest: 'Chest', wrist: 'Wrist', hands: 'Hands', waist: 'Waist',
  legs: 'Legs', feet: 'Feet',
  ring_1: 'Ring 1', ring_2: 'Ring 2',
  trinket_1: 'Trinket 1', trinket_2: 'Trinket 2',
  main_hand: 'Main Hand', off_hand: 'Off Hand',
};

const WOW_SLOTS = [
  'head','neck','shoulder','back','chest','wrist','hands','waist','legs','feet',
  'ring_1','ring_2','trinket_1','trinket_2','main_hand','off_hand',
];

// ── DOM refs ──────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

// ── Init ──────────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', init);

async function init() {
  await loadCharacters();

  $('gp-char-select').addEventListener('change', onCharChange);
  $('gp-ht-select').addEventListener('change', onConfigChange);
  $('gp-source-select').addEventListener('change', onConfigChange);
  $('gp-btn-sync').addEventListener('click', onSyncGear);
  $('gp-btn-export-simc').addEventListener('click', onExportSimc);
  $('gp-btn-populate').addEventListener('click', onPopulate);
  $('gp-btn-import-simc').addEventListener('click', () => showSimcModal());
  $('gp-btn-delete-plan').addEventListener('click', onDeletePlan);
  $('gp-simc-submit').addEventListener('click', onSimcImport);
  $('gp-simc-cancel').addEventListener('click', hideSimcModal);
  $('gp-simc-close').addEventListener('click', hideSimcModal);
  $('gp-drawer-close').addEventListener('click', closeDrawer);
  $('gp-simc-modal').querySelector('.gp-modal__backdrop')
    .addEventListener('click', hideSimcModal);
}

// ── Character loading ─────────────────────────────────────────────────────

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
    $('gp-config-bar').hidden = false;

    const firstId = inGuild[0].id;
    $('gp-char-select').value = firstId;
    await loadPlan(firstId);
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

// ── Plan loading ──────────────────────────────────────────────────────────

async function loadPlan(charId) {
  _state.activeCharId = charId;
  $('gp-slots-container').hidden = true;
  showStatus('Loading plan…', 'info');

  try {
    const resp = await apiFetch(`/api/v1/me/gear-plan/${charId}`);
    if (!resp.ok) throw new Error(resp.error || 'Failed to load plan');
    const data = resp.data;
    _state.plan = data.plan;
    _state.slots = data.slots;
    _state.bisSources = data.bis_sources || [];
    _state.heroTalents = data.hero_talents || [];
    _state.trackColors = data.track_colors || {};

    updateSpecDisplay();
    updateHtSelect();
    updateSourceSelect();
    renderSlots();
    $('gp-slots-container').hidden = false;
    clearStatus();
  } catch (err) {
    showError(err.message);
  }
}

// ── Config display ────────────────────────────────────────────────────────

function updateSpecDisplay() {
  const specName = _state.plan?.spec_name || '—';
  $('gp-spec-display').textContent = specName;
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

// ── Config change ─────────────────────────────────────────────────────────

async function onConfigChange() {
  const heroTalentId = $('gp-ht-select').value ? parseInt($('gp-ht-select').value, 10) : null;
  const bisSourceId  = $('gp-source-select').value ? parseInt($('gp-source-select').value, 10) : null;

  try {
    const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/config`, {
      method: 'PATCH',
      body: JSON.stringify({ hero_talent_id: heroTalentId, bis_source_id: bisSourceId }),
    });
    if (!resp.ok) throw new Error(resp.error || 'Config update failed');
    // Reload to refresh BIS recommendations
    await loadPlan(_state.activeCharId);
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

// ── Slot rendering ────────────────────────────────────────────────────────

function renderSlots() {
  const body = $('gp-slots-body');
  body.innerHTML = '';

  for (const slotKey of WOW_SLOTS) {
    const sd = _state.slots[slotKey];
    if (!sd) continue;
    const row = buildSlotRow(sd);
    body.appendChild(row);
  }
}

function buildSlotRow(sd) {
  const row = document.createElement('div');
  row.className = 'gp-slot-row';
  row.dataset.slot = sd.slot;
  if (_state.openSlot === sd.slot) row.classList.add('is-open');

  // Col 1: Slot label
  const colSlot = document.createElement('span');
  colSlot.className = 'gp-col-slot';
  colSlot.textContent = SLOT_LABELS[sd.slot] || sd.slot;

  // Col 2: Equipped
  const colEquipped = document.createElement('div');
  colEquipped.className = 'gp-col-equipped';
  colEquipped.innerHTML = renderItemCell(sd.equipped, 'equipped');

  // Col 3: Desired / BIS
  const colDesired = document.createElement('div');
  colDesired.className = 'gp-col-desired';
  const desiredItem = sd.desired || (sd.bis_recommendations.length ? _primaryBis(sd) : null);
  colDesired.innerHTML = renderItemCell(desiredItem, 'desired');

  // Col 4: Status
  const colStatus = document.createElement('div');
  colStatus.className = 'gp-col-status';
  colStatus.innerHTML = renderStatusBadges(sd);

  row.appendChild(colSlot);
  row.appendChild(colEquipped);
  row.appendChild(colDesired);
  row.appendChild(colStatus);

  row.addEventListener('click', () => toggleDrawer(sd.slot));
  return row;
}

function _primaryBis(sd) {
  // Return the BIS recommendation from the selected source
  const srcId = _state.plan?.bis_source_id;
  const recs = sd.bis_recommendations || [];
  return recs.find(r => r.source_id === srcId) || recs[0] || null;
}

function renderItemCell(item, type) {
  if (!item || !item.blizzard_item_id) {
    return `<div class="gp-item-icon--placeholder"></div><span class="gp-empty-slot">—</span>`;
  }
  const iconSrc = item.icon_url
    ? `<img class="gp-item-icon" src="${esc(item.icon_url)}" alt="" loading="lazy">`
    : `<div class="gp-item-icon--placeholder"></div>`;

  let meta = '';
  if (type === 'equipped' && item.item_level) {
    const trackBadge = item.quality_track
      ? `<span class="gp-track-badge" style="background:${esc(trackColor(item.quality_track))}">${esc(item.quality_track)}</span>`
      : '';
    meta = `<span class="gp-item-meta">${item.item_level} ${trackBadge}</span>`;
  }

  return `${iconSrc}<div class="gp-item-info">
    <div class="gp-item-name">${esc(item.item_name || 'Unknown')}</div>
    ${meta}
  </div>`;
}

function renderStatusBadges(sd) {
  let html = '';
  const locked = sd.desired?.is_locked;

  if (locked) {
    html += `<span class="gp-status-badge gp-status-badge--locked">🔒 Locked</span>`;
  }

  if (sd.is_bis && !sd.needs_upgrade) {
    html += `<span class="gp-status-badge gp-status-badge--bis">✓ BIS</span>`;
  } else if (sd.needs_upgrade) {
    const tracks = sd.upgrade_tracks || [];
    if (tracks.length) {
      const pips = tracks.map(t =>
        `<span class="gp-track-badge" style="background:${esc(trackColor(t))}">${esc(t)}</span>`
      ).join('');
      html += `<span class="gp-status-badge gp-status-badge--need">Need</span>`;
      html += `<div class="gp-upgrade-tracks">${pips}</div>`;
    }
  } else if (!sd.equipped && !sd.desired) {
    html += `<span class="gp-status-badge gp-status-badge--none">—</span>`;
  }

  return html;
}

function trackColor(track) {
  return _state.trackColors[track] || '#888';
}

// ── Drawer ────────────────────────────────────────────────────────────────

function toggleDrawer(slotKey) {
  if (_state.openSlot === slotKey) {
    closeDrawer();
  } else {
    openDrawer(slotKey);
  }
}

function openDrawer(slotKey) {
  _state.openSlot = slotKey;
  const sd = _state.slots[slotKey];
  if (!sd) return;

  // Mark open row
  document.querySelectorAll('.gp-slot-row').forEach(r => {
    r.classList.toggle('is-open', r.dataset.slot === slotKey);
  });

  $('gp-drawer-title').textContent = `${SLOT_LABELS[slotKey] || slotKey} — Details`;
  $('gp-drawer-body').innerHTML = renderDrawerBody(sd);
  $('gp-drawer').hidden = false;

  // Insert drawer after the open row
  const openRow = document.querySelector(`.gp-slot-row[data-slot="${slotKey}"]`);
  const drawer = $('gp-drawer');
  const body = $('gp-slots-body');
  if (openRow && body.contains(openRow)) {
    openRow.after(drawer);
    drawer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // Wire up drawer controls
  wireDrawerControls(sd);
}

function closeDrawer() {
  _state.openSlot = null;
  document.querySelectorAll('.gp-slot-row').forEach(r => r.classList.remove('is-open'));
  const drawer = $('gp-drawer');
  drawer.hidden = true;
  $('gp-slots-body').appendChild(drawer); // return to end
}

function renderDrawerBody(sd) {
  const equip = sd.equipped;
  const desired = sd.desired;
  const bis = sd.bis_recommendations || [];
  const sources = sd.item_sources || [];
  const tracks = sd.available_tracks || [];
  const upgrades = sd.upgrade_tracks || [];

  // Section 1: Equipped
  let equippedHtml = '';
  if (equip) {
    const enchant = equip.enchant_id ? `<div class="gp-drop-source">Enchant: ${equip.enchant_id}</div>` : '';
    equippedHtml = `
      <div class="gp-bis-row">
        ${equip.icon_url ? `<img class="gp-item-icon" src="${esc(equip.icon_url)}" alt="" loading="lazy">` : ''}
        <div class="gp-item-info">
          <div class="gp-item-name">${esc(equip.item_name || 'Unknown')}</div>
          <div class="gp-item-meta">
            ${equip.item_level || ''}&nbsp;
            ${equip.quality_track ? `<span class="gp-track-badge" style="background:${esc(trackColor(equip.quality_track))}">${esc(equip.quality_track)}</span>` : ''}
          </div>
          ${enchant}
        </div>
      </div>`;
  } else {
    equippedHtml = '<div class="gp-empty-slot">Nothing equipped</div>';
  }

  // Section 2: BIS Recommendations
  let bisHtml = '';
  if (bis.length) {
    bisHtml = bis.map(r => `
      <div class="gp-bis-row">
        ${r.icon_url ? `<img class="gp-item-icon" src="${esc(r.icon_url)}" alt="" loading="lazy">` : ''}
        <span class="gp-bis-source-label">${esc(r.short_label || r.source_name)}</span>
        <span class="gp-bis-item-name">${esc(r.item_name)}</span>
        <button class="btn btn--sm btn--secondary" onclick="setDesiredFromBis('${esc(sd.slot)}', ${r.blizzard_item_id}, '${esc(r.item_name)}')">
          Use
        </button>
      </div>`).join('');
  } else {
    bisHtml = '<div class="gp-empty-slot">No BIS data for this slot</div>';
  }

  // Section 3: Selection + lock
  let selectionHtml = '';
  if (desired) {
    selectionHtml = `
      <div class="gp-bis-row">
        ${desired.icon_url ? `<img class="gp-item-icon" src="${esc(desired.icon_url)}" alt="" loading="lazy">` : ''}
        <span class="gp-bis-item-name">${esc(desired.item_name || 'Unknown')}</span>
        <button class="gp-lock-btn ${desired.is_locked ? 'locked' : ''}" onclick="toggleLock('${esc(sd.slot)}', ${desired.is_locked})">
          ${desired.is_locked ? '🔒 Locked' : '🔓 Lock'}
        </button>
        <button class="btn btn--sm btn--secondary" onclick="clearSlot('${esc(sd.slot)}')">Clear</button>
      </div>`;
  } else {
    selectionHtml = '<div class="gp-empty-slot">No desired item set</div>';
  }

  // Manual lookup
  const manualHtml = `
    <div class="gp-manual-lookup" style="margin-top:0.5rem">
      <input type="number" class="gp-manual-lookup__input" id="gp-manual-id-${esc(sd.slot)}"
             placeholder="Item ID" min="1">
      <button class="btn btn--sm btn--secondary" onclick="fetchAndSetItem('${esc(sd.slot)}')">Fetch</button>
    </div>`;

  // Drop location
  let dropHtml = '';
  if (sources.length) {
    const loc = sources[0];
    const trackPills = tracks.map(t =>
      `<span class="gp-track-badge" style="background:${esc(trackColor(t))}">${esc(t)}</span>`
    ).join(' ');
    dropHtml = `<div class="gp-drop-source">
      ${esc(loc.source_name)}${loc.source_instance ? ` — ${esc(loc.source_instance)}` : ''}
      &nbsp;${trackPills}
    </div>`;
    if (upgrades.length) {
      const upgPills = upgrades.map(t =>
        `<span class="gp-track-badge" style="background:${esc(trackColor(t))}">${esc(t)}</span>`
      ).join(' ');
      dropHtml += `<div class="gp-drop-source" style="margin-top:0.2rem">
        Upgrade tracks: ${upgPills}
      </div>`;
    }
  }

  return `
    <div class="gp-drawer-section">
      <div class="gp-drawer-section__title">Currently Equipped</div>
      ${equippedHtml}
    </div>
    <div class="gp-drawer-section">
      <div class="gp-drawer-section__title">BIS Recommendations</div>
      ${bisHtml}
    </div>
    <div class="gp-drawer-section">
      <div class="gp-drawer-section__title">Your Selection</div>
      ${selectionHtml}
      ${manualHtml}
    </div>
    <div class="gp-drawer-section">
      <div class="gp-drawer-section__title">Drop Location &amp; Tracks</div>
      ${dropHtml || '<div class="gp-empty-slot">No source data</div>'}
    </div>`;
}

function wireDrawerControls() {
  // Controls are wired via inline onclick for simplicity; global functions below
}

// ── Drawer actions (globals for inline handlers) ──────────────────────────

window.setDesiredFromBis = async function(slot, blizzardItemId, itemName) {
  await setSlot(slot, blizzardItemId, itemName);
};

window.clearSlot = async function(slot) {
  await setSlot(slot, null, null);
};

window.toggleLock = async function(slot, currentlyLocked) {
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/slot/${slot}`, {
    method: 'PUT',
    body: JSON.stringify({ is_locked: !currentlyLocked }),
  });
  if (resp.ok) {
    showStatus(!currentlyLocked ? 'Slot locked' : 'Slot unlocked', 'ok');
    await reloadSlots();
  } else {
    showStatus(resp.error || 'Failed to update lock', 'err');
  }
};

window.fetchAndSetItem = async function(slot) {
  const input = document.getElementById(`gp-manual-id-${slot}`);
  const itemId = parseInt(input?.value, 10);
  if (!itemId) return;

  showStatus('Fetching item…', 'info');
  const itemResp = await apiFetch(`/api/v1/items/${itemId}`);
  if (!itemResp.ok) {
    showStatus(itemResp.error || 'Item not found', 'err');
    return;
  }
  const item = itemResp.data;
  await setSlot(slot, item.blizzard_item_id, item.name);
};

async function setSlot(slot, blizzardItemId, itemName) {
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/slot/${slot}`, {
    method: 'PUT',
    body: JSON.stringify({ blizzard_item_id: blizzardItemId, item_name: itemName }),
  });
  if (resp.ok) {
    showStatus('Slot updated', 'ok');
    await reloadSlots();
  } else {
    showStatus(resp.error || 'Failed to update slot', 'err');
  }
}

// ── Plan actions ──────────────────────────────────────────────────────────

async function onSyncGear() {
  showStatus('Syncing gear from Blizzard…', 'info');
  // Trigger character refresh via existing endpoint
  try {
    const resp = await apiFetch('/api/v1/me/refresh', { method: 'POST' });
    if (resp.ok) {
      showStatus('Gear synced — reloading plan…', 'ok');
      setTimeout(() => loadPlan(_state.activeCharId), 1500);
    } else {
      showStatus(resp.error || 'Sync failed', 'err');
    }
  } catch {
    showStatus('Sync request failed', 'err');
  }
}

async function onPopulate() {
  const srcId = $('gp-source-select').value ? parseInt($('gp-source-select').value, 10) : null;
  const htId  = $('gp-ht-select').value ? parseInt($('gp-ht-select').value, 10) : null;
  showStatus('Filling unlocked slots from BIS…', 'info');
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/populate`, {
    method: 'POST',
    body: JSON.stringify({ source_id: srcId, hero_talent_id: htId }),
  });
  if (resp.ok) {
    showStatus(`${resp.data?.populated || 0} slots filled`, 'ok');
    await reloadSlots();
  } else {
    showStatus(resp.error || 'Populate failed', 'err');
  }
}

async function onDeletePlan() {
  if (!confirm('Delete this gear plan? All slot selections will be lost.')) return;
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}`, { method: 'DELETE' });
  if (resp.ok) {
    showStatus('Plan deleted', 'ok');
    closeDrawer();
    await loadPlan(_state.activeCharId);
  } else {
    showStatus(resp.error || 'Delete failed', 'err');
  }
}

async function onExportSimc() {
  showStatus('Generating SimC profile…', 'info');
  try {
    const resp = await fetch(`/api/v1/me/gear-plan/${_state.activeCharId}/export-simc`, {
      headers: { 'Accept': 'text/plain' },
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      showStatus(data.error || 'Export failed', 'err');
      return;
    }
    const text = await resp.text();
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'gear_plan.simc';
    a.click();
    URL.revokeObjectURL(url);
    clearStatus();
  } catch (err) {
    showStatus(err.message, 'err');
  }
}

// ── SimC import modal ─────────────────────────────────────────────────────

function showSimcModal() {
  $('gp-simc-modal').hidden = false;
  $('gp-simc-text').value = '';
  $('gp-simc-text').focus();
}

function hideSimcModal() {
  $('gp-simc-modal').hidden = true;
}

async function onSimcImport() {
  const text = $('gp-simc-text').value.trim();
  if (!text) return;
  showStatus('Importing SimC profile…', 'info');
  hideSimcModal();
  const resp = await apiFetch(`/api/v1/me/gear-plan/${_state.activeCharId}/import-simc`, {
    method: 'POST',
    body: JSON.stringify({ simc_text: text }),
  });
  if (resp.ok) {
    const d = resp.data || {};
    showStatus(
      `Imported: ${d.populated || 0} slots set` +
      (d.skipped_locked ? `, ${d.skipped_locked} locked skipped` : ''),
      'ok'
    );
    await reloadSlots();
  } else {
    showStatus(resp.error || 'Import failed', 'err');
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────

async function reloadSlots() {
  const openSlot = _state.openSlot;
  closeDrawer();
  await loadPlan(_state.activeCharId);
  if (openSlot) {
    openDrawer(openSlot);
  }
}

async function apiFetch(url, options = {}) {
  const defaults = {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  };
  const resp = await fetch(url, { ...defaults, ...options });
  try {
    return await resp.json();
  } catch {
    return { ok: false, error: `HTTP ${resp.status}` };
  }
}

function setLoading(on) {
  $('gp-loading').style.display = on ? '' : 'none';
}

function showStatus(msg, type) {
  const el = $('gp-status');
  el.textContent = msg;
  el.className = `gp-status gp-status--${type}`;
  el.hidden = false;
}

function clearStatus() {
  const el = $('gp-status');
  el.hidden = true;
  el.textContent = '';
}

function showError(msg) {
  $('gp-error').textContent = msg;
  $('gp-error').hidden = false;
}

function esc(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
