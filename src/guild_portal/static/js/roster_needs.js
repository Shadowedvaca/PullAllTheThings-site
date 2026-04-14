/**
 * roster_needs.js — Roster Needs section on the public /roster page.
 *
 * Fetches aggregated gear needs from:
 *   GET /api/v1/gear-needs/raid
 *   GET /api/v1/gear-needs/dungeon
 *
 * Phase 1E.1: Raid + M+ tables with expand/collapse, auto-hide empty tracks,
 *             filters, and color-coded chips.
 * Phase 1E.2: Drill panel (slide-in from right, By Item / By Player views,
 *             Wowhead tooltips, active-chip highlight).
 */

'use strict';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TRACK_ORDER  = ['V', 'C', 'H', 'M'];
const TRACK_LABEL  = { V: 'Veteran', C: 'Champion', H: 'Hero', M: 'Myth' };
const TRACK_COLORS = { V: '#94a3b8', C: '#60a5fa', H: '#a78bfa', M: '#fb923c' };

const SLOT_NAMES = {
    HEAD: 'Head', NECK: 'Neck', SHOULDER: 'Shoulder', BACK: 'Back',
    CHEST: 'Chest', WRIST: 'Wrist', HANDS: 'Hands', WAIST: 'Waist',
    LEGS: 'Legs', FEET: 'Feet', FINGER_1: 'Ring 1', FINGER_2: 'Ring 2',
    TRINKET_1: 'Trinket 1', TRINKET_2: 'Trinket 2',
    MAIN_HAND: 'Main Hand', OFF_HAND: 'Off Hand',
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const _openInst = new Set(); // instance names currently expanded
let _raidData   = null;
let _dungData   = null;
let _lastParams = null;

// Drill panel
let _drillCtx   = null;  // { type:'boss'|'inst'|'dungeon', instName?, bossName?, dungName?, track }
let _drillView  = 'item'; // 'item' | 'player'

// Click-context map: integer key → ctx object (rebuilt on each render)
let _drillMap   = {};
let _drillMapIdx = 0;

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function _esc(s) {
    const d = document.createElement('div');
    d.textContent = String(s || '');
    return d.innerHTML;
}

function _chipLevel(playerCount) {
    if (playerCount >= 6) return 'l3';
    if (playerCount >= 3) return 'l2';
    return 'l1';
}

function _chipHtml(playerCount, itemCount) {
    const lvl = _chipLevel(playerCount);
    const tip = `${playerCount} player${playerCount !== 1 ? 's' : ''}, ${itemCount} item slot${itemCount !== 1 ? 's' : ''} needed`;
    return `<span class="rn-chip ${lvl}" title="${tip}">${playerCount}<span class="rn-chip__sep">|</span>${itemCount}</span>`;
}

function _setStatus(msg, type) {
    const el = document.getElementById('rn-status');
    if (!el) return;
    el.textContent = msg;
    el.className = 'rn-status' + (type ? ` rn-status--${type}` : '');
    el.style.display = msg ? '' : 'none';
}

// Item icon <img> tag; icon_url may be a full URL or just a slug.
function _iconImg(iconUrl, size) {
    size = size || 18;
    if (!iconUrl) return `<span style="display:inline-block;width:${size}px;height:${size}px;background:#333;border-radius:2px;flex-shrink:0"></span>`;
    const src = iconUrl.startsWith('http') ? iconUrl
        : `https://wow.zamimg.com/images/wow/icons/medium/${iconUrl}.jpg`;
    return `<img src="${_esc(src)}" width="${size}" height="${size}" style="border-radius:2px;flex-shrink:0" loading="lazy" alt="">`;
}

// Wowhead item link with tooltip data attribute
function _whLink(bid, name) {
    return `<a href="https://www.wowhead.com/item=${bid}" data-wowhead="item=${bid}" target="_blank" rel="noopener" class="rn-wh-link">${_esc(name)}</a>`;
}

// Class color lookup (CLASS_COLORS is defined in the inline script in roster.html)
function _classColor(className) {
    if (typeof CLASS_COLORS !== 'undefined' && className && CLASS_COLORS[className]) {
        return CLASS_COLORS[className];
    }
    return '#e8e8e8';
}

function _slotLabel(slot) {
    return SLOT_NAMES[slot] || slot;
}

// ---------------------------------------------------------------------------
// Drill panel — data gathering
// ---------------------------------------------------------------------------

function _gatherBossEntries(inst, bossName, track) {
    for (const boss of inst.bosses) {
        if (boss.name === bossName) {
            return (boss.tracks[track] && boss.tracks[track].entries) || [];
        }
    }
    return [];
}

function _gatherInstEntries(inst, track) {
    // Merge items across all bosses per player, dedup by (player_id, bid, slot)
    const seen      = new Set();
    const playerMap = {};
    for (const boss of inst.bosses) {
        const td = boss.tracks[track];
        if (!td) continue;
        for (const entry of td.entries) {
            const pid = entry.player_id;
            if (!playerMap[pid]) playerMap[pid] = Object.assign({}, entry, { items: [] });
            for (const item of (entry.items || [])) {
                const k = `${pid}-${item.bid}-${item.slot}`;
                if (!seen.has(k)) {
                    seen.add(k);
                    playerMap[pid].items.push(item);
                }
            }
        }
    }
    return Object.values(playerMap).sort(
        (a, b) => a.player_name.localeCompare(b.player_name) || (a.player_id - b.player_id)
    );
}

function _gatherDungEntries(dungName, track) {
    if (!_dungData) return [];
    for (const d of _dungData) {
        if (d.name === dungName) {
            return (d.tracks[track] && d.tracks[track].entries) || [];
        }
    }
    return [];
}

function _getEntriesForCtx(ctx) {
    if (!ctx) return [];
    if (ctx.type === 'boss') {
        for (const inst of (_raidData || [])) {
            if (inst.name === ctx.instName) return _gatherBossEntries(inst, ctx.bossName, ctx.track);
        }
    } else if (ctx.type === 'inst') {
        for (const inst of (_raidData || [])) {
            if (inst.name === ctx.instName) return _gatherInstEntries(inst, ctx.track);
        }
    } else if (ctx.type === 'dungeon') {
        return _gatherDungEntries(ctx.dungName, ctx.track);
    }
    return [];
}

// ---------------------------------------------------------------------------
// Drill panel — open / close / render
// ---------------------------------------------------------------------------

// Called from inline onclick on drillable cells (exposed on window)
window._rnOpenDrill = function(el, dkey) {
    const ctx = _drillMap[dkey];
    if (!ctx) return;

    // Toggle off if clicking the already-active cell
    if (_drillCtx &&
        _drillCtx.type === ctx.type &&
        _drillCtx.instName === ctx.instName &&
        _drillCtx.bossName === ctx.bossName &&
        _drillCtx.dungName === ctx.dungName &&
        _drillCtx.track   === ctx.track) {
        closeDrillPanel();
        return;
    }

    _drillCtx = ctx;

    // Re-apply active class (remove old, add to clicked cell)
    document.querySelectorAll('.rn-cell--active').forEach(c => c.classList.remove('rn-cell--active'));
    el.classList.add('rn-cell--active');

    _renderDrillPanel();

    const panel = document.getElementById('rn-drill-panel');
    if (panel && !panel.classList.contains('is-open')) panel.classList.add('is-open');
};

// Called from close button and filter changes when no matching data
function closeDrillPanel() {
    _drillCtx = null;
    document.querySelectorAll('.rn-cell--active').forEach(c => c.classList.remove('rn-cell--active'));
    const panel = document.getElementById('rn-drill-panel');
    if (panel) panel.classList.remove('is-open');
}
window.closeDrillPanel = closeDrillPanel;

function _renderDrillPanel() {
    const titleEl = document.getElementById('rn-panel-title');
    const bodyEl  = document.getElementById('rn-panel-body');
    if (!titleEl || !bodyEl) return;

    // Title
    let title = '';
    if (_drillCtx) {
        const tLabel = TRACK_LABEL[_drillCtx.track] || _drillCtx.track;
        if (_drillCtx.type === 'boss') {
            title = `${_drillCtx.bossName} — ${tLabel}`;
        } else if (_drillCtx.type === 'inst') {
            title = `${_drillCtx.instName} — ${tLabel}`;
        } else if (_drillCtx.type === 'dungeon') {
            title = `${_drillCtx.dungName} — ${tLabel}`;
        }
    }
    titleEl.textContent = title;

    // View toggle button states
    const btnItem   = document.getElementById('rn-view-item');
    const btnPlayer = document.getElementById('rn-view-player');
    if (btnItem)   btnItem.classList.toggle('active', _drillView === 'item');
    if (btnPlayer) btnPlayer.classList.toggle('active', _drillView === 'player');

    const entries = _getEntriesForCtx(_drillCtx);
    if (!entries.length) {
        bodyEl.innerHTML = '<p class="rn-panel-empty">No needs found for current filters.</p>';
        return;
    }

    bodyEl.innerHTML = _drillView === 'item' ? _renderByItem(entries) : _renderByPlayer(entries);

    // Fire Wowhead tooltip refresh
    if (typeof $WowheadPower !== 'undefined') {
        try { $WowheadPower.refreshLinks(); } catch (_) {}
    }
}

// ---------------------------------------------------------------------------
// Drill panel — By Item view
// ---------------------------------------------------------------------------

function _renderByItem(entries) {
    // Group by item bid
    const itemMap = new Map();
    for (const entry of entries) {
        for (const item of (entry.items || [])) {
            if (!itemMap.has(item.bid)) {
                itemMap.set(item.bid, Object.assign({}, item, { players: [] }));
            }
            itemMap.get(item.bid).players.push(entry);
        }
    }
    if (!itemMap.size) return '<p class="rn-panel-empty">No items found.</p>';

    let html = '';
    for (const [bid, item] of itemMap) {
        html += `<div class="rn-hub">`;
        html += `<div class="rn-hub__header">`;
        html += `<div class="rn-hub__icon">${_iconImg(item.icon_url, 28)}</div>`;
        html += `<div class="rn-hub__meta">`;
        html += `<div class="rn-hub__name">${_whLink(bid, item.name)}</div>`;
        html += `<div class="rn-hub__sub">${_esc(_slotLabel(item.slot))}</div>`;
        html += `</div></div>`;

        for (const player of item.players) {
            const color = _classColor(player.class_name);
            const osBadge = player.is_offspec ? '<span class="rn-os-badge">OS</span>' : '';
            html += `<div class="rn-spoke">`;
            html += `<span class="rn-spoke__player" style="color:${color}">${_esc(player.player_name)} &mdash; ${_esc(player.character_name)}</span>`;
            html += `<span class="rn-spoke__spec">${_esc(player.spec_name || '')}${osBadge}</span>`;
            html += `</div>`;
        }
        html += `</div>`;
    }
    return html;
}

// ---------------------------------------------------------------------------
// Drill panel — By Player view
// ---------------------------------------------------------------------------

function _renderByPlayer(entries) {
    if (!entries.length) return '<p class="rn-panel-empty">No players found.</p>';

    const sorted = [...entries].sort(
        (a, b) => a.player_name.localeCompare(b.player_name) || (a.player_id - b.player_id)
    );
    let html = '';
    for (const entry of sorted) {
        const color  = _classColor(entry.class_name);
        const osBadge = entry.is_offspec ? '<span class="rn-os-badge">OS</span>' : '';
        html += `<div class="rn-hub" style="border-left:3px solid ${color}">`;
        html += `<div class="rn-hub__header">`;
        html += `<div class="rn-hub__meta">`;
        html += `<div class="rn-hub__name" style="color:${color}">${_esc(entry.player_name)} &mdash; ${_esc(entry.character_name)}</div>`;
        html += `<div class="rn-hub__sub">${_esc(entry.spec_name || '')}${osBadge}</div>`;
        html += `</div></div>`;

        for (const item of (entry.items || [])) {
            html += `<div class="rn-spoke">`;
            html += _iconImg(item.icon_url, 16);
            html += `<span class="rn-spoke__item">${_whLink(item.bid, item.name)}</span>`;
            html += `<span class="rn-spoke__slot">${_esc(_slotLabel(item.slot))}</span>`;
            html += `</div>`;
        }
        html += `</div>`;
    }
    return html;
}

// ---------------------------------------------------------------------------
// Render raid table
// ---------------------------------------------------------------------------

function _renderRaidTable(instances) {
    const container = document.getElementById('rn-raid-table-wrap');
    if (!container) return;

    if (!instances || instances.length === 0) {
        container.innerHTML = '<p class="rn-empty">No raid needs found. Make sure players have active gear plans with items set as goals.</p>';
        return;
    }

    // Determine which tracks have any data
    const activeTrackSet = new Set();
    for (const inst of instances) {
        for (const boss of inst.bosses) {
            for (const t of Object.keys(boss.tracks)) activeTrackSet.add(t);
        }
    }
    const activeTracks = TRACK_ORDER.filter(t => activeTrackSet.has(t));

    let html = '<table class="rn-table"><thead><tr>';
    html += '<th class="col-name">Boss</th>';
    for (const t of activeTracks) {
        html += `<th data-t="${t}" style="color:${TRACK_COLORS[t]}">`;
        html += `${t}<div class="th-sub">${TRACK_LABEL[t]}</div></th>`;
    }
    html += '</tr></thead><tbody>';

    for (const inst of instances) {
        const isOpen = _openInst.has(inst.name);

        // Instance summary row — cells drillable only when data exists
        html += `<tr class="row-inst${isOpen ? ' is-open' : ''}" data-inst="${_esc(inst.name)}" onclick="_toggleInst(this.dataset.inst)">`;
        html += `<td class="col-name"><span class="expand-btn">▶</span>${_esc(inst.name)}</td>`;
        for (const t of activeTracks) {
            const roll = inst.rollup[t];
            if (roll) {
                const ctx = { type: 'inst', instName: inst.name, track: t };
                const dkey = _drillMapIdx++;
                _drillMap[dkey] = ctx;
                const isActive = _isDrillActive(ctx);
                html += `<td class="rn-cell--drillable${isActive ? ' rn-cell--active' : ''}" onclick="event.stopPropagation();_rnOpenDrill(this,${dkey})">`;
                html += _chipHtml(roll.player_count, roll.item_count);
                html += `</td>`;
            } else {
                html += '<td></td>';
            }
        }
        html += '</tr>';

        // Boss detail rows
        for (const boss of inst.bosses) {
            html += `<tr class="row-boss${isOpen ? '' : ' hidden'}" data-inst="${_esc(inst.name)}">`;
            html += `<td class="col-name">${_esc(boss.name)}</td>`;
            for (const t of activeTracks) {
                const td = boss.tracks[t];
                if (td) {
                    const ctx = { type: 'boss', instName: inst.name, bossName: boss.name, track: t };
                    const dkey = _drillMapIdx++;
                    _drillMap[dkey] = ctx;
                    const isActive = _isDrillActive(ctx);
                    html += `<td class="rn-cell--drillable${isActive ? ' rn-cell--active' : ''}" onclick="_rnOpenDrill(this,${dkey})">`;
                    html += _chipHtml(td.player_count, td.item_count);
                    html += `</td>`;
                } else {
                    html += '<td></td>';
                }
            }
            html += '</tr>';
        }
    }

    html += '</tbody></table>';
    container.innerHTML = html;
}

function _toggleInst(instName) {
    if (_openInst.has(instName)) _openInst.delete(instName);
    else _openInst.add(instName);
    if (_raidData) _renderRaidTable(_raidData);
}
window._toggleInst = _toggleInst;

// ---------------------------------------------------------------------------
// Render dungeon table
// ---------------------------------------------------------------------------

function _renderDungeonTable(dungeons) {
    const container = document.getElementById('rn-dung-table-wrap');
    if (!container) return;

    if (!dungeons || dungeons.length === 0) {
        container.innerHTML = '<p class="rn-empty">No M+ needs found. Make sure players have active gear plans with M+ dungeon items set as goals.</p>';
        return;
    }

    const activeTrackSet = new Set();
    for (const d of dungeons) {
        for (const t of Object.keys(d.tracks)) activeTrackSet.add(t);
    }
    // M+ table: C/H only (M-track vault deferred)
    const activeTracks = ['C', 'H'].filter(t => activeTrackSet.has(t));

    let html = '<table class="rn-table"><thead><tr>';
    html += '<th class="col-name">Dungeon</th>';
    for (const t of activeTracks) {
        html += `<th data-t="${t}" style="color:${TRACK_COLORS[t]}">`;
        html += `${t}<div class="th-sub">${TRACK_LABEL[t]}</div></th>`;
    }
    html += '</tr></thead><tbody>';

    for (const d of dungeons) {
        html += '<tr class="row-dung">';
        html += `<td class="col-name">${_esc(d.name)}</td>`;
        for (const t of activeTracks) {
            const td = d.tracks[t];
            if (td) {
                const ctx = { type: 'dungeon', dungName: d.name, track: t };
                const dkey = _drillMapIdx++;
                _drillMap[dkey] = ctx;
                const isActive = _isDrillActive(ctx);
                html += `<td class="rn-cell--drillable${isActive ? ' rn-cell--active' : ''}" onclick="_rnOpenDrill(this,${dkey})">`;
                html += _chipHtml(td.player_count, td.item_count);
                html += `</td>`;
            } else {
                html += '<td></td>';
            }
        }
        html += '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;
}

// Check if a cell's context matches the currently active drill context
function _isDrillActive(ctx) {
    if (!_drillCtx) return false;
    return _drillCtx.type     === ctx.type &&
           _drillCtx.track    === ctx.track &&
           _drillCtx.instName === ctx.instName &&
           _drillCtx.bossName === ctx.bossName &&
           _drillCtx.dungName === ctx.dungName;
}

// ---------------------------------------------------------------------------
// Fetch + render
// ---------------------------------------------------------------------------

async function loadRosterNeeds() {
    const includeInitiates = document.getElementById('cb-init')?.checked ?? true;
    const includeOffspec   = document.getElementById('cb-os')?.checked   ?? false;
    const params = `?include_initiates=${includeInitiates}&include_offspec=${includeOffspec}`;

    const raidWrap = document.getElementById('rn-raid-table-wrap');
    const dungWrap = document.getElementById('rn-dung-table-wrap');
    const spin = '<p class="rn-loading"><span class="spinner"></span> Loading\u2026</p>';
    if (raidWrap) raidWrap.innerHTML = spin;
    if (dungWrap) dungWrap.innerHTML = spin;
    _setStatus('');

    try {
        const [raidResp, dungResp] = await Promise.all([
            fetch(`/api/v1/gear-needs/raid${params}`),
            fetch(`/api/v1/gear-needs/dungeon${params}`),
        ]);

        if (!raidResp.ok) {
            const err = await raidResp.json().catch(() => ({}));
            throw new Error(err.detail || `Raid fetch failed (${raidResp.status})`);
        }
        if (!dungResp.ok) {
            const err = await dungResp.json().catch(() => ({}));
            throw new Error(err.detail || `Dungeon fetch failed (${dungResp.status})`);
        }

        const raidData = await raidResp.json();
        const dungData = await dungResp.json();

        _raidData = raidData.instances || [];
        _dungData = dungData.dungeons  || [];
        _lastParams = params;

        // Expand all instances by default on first load or filter change
        _openInst.clear();
        for (const inst of _raidData) _openInst.add(inst.name);

        // Reset drill map (rebuilt during render)
        _drillMap = {};
        _drillMapIdx = 0;

        _renderRaidTable(_raidData);
        _renderDungeonTable(_dungData);

        // Re-render drill panel if it was open
        if (_drillCtx) {
            const entries = _getEntriesForCtx(_drillCtx);
            if (entries.length > 0) {
                _renderDrillPanel();
            } else {
                closeDrillPanel();
            }
        }
    } catch (err) {
        const errHtml = `<p class="rn-error">Error: ${_esc(err.message)}</p>`;
        if (raidWrap) raidWrap.innerHTML = errHtml;
        if (dungWrap) dungWrap.innerHTML = errHtml;
        _setStatus('Load failed — ' + err.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

window.addEventListener('DOMContentLoaded', () => {
    document.getElementById('cb-init')?.addEventListener('change', loadRosterNeeds);
    document.getElementById('cb-os')?.addEventListener('change', loadRosterNeeds);

    document.getElementById('rn-view-item')?.addEventListener('click', () => {
        _drillView = 'item';
        _renderDrillPanel();
    });
    document.getElementById('rn-view-player')?.addEventListener('click', () => {
        _drillView = 'player';
        _renderDrillPanel();
    });

    document.getElementById('rn-panel-close')?.addEventListener('click', closeDrillPanel);

    loadRosterNeeds();
});
