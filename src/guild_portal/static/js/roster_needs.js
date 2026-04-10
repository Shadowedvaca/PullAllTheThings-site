/**
 * roster_needs.js — Roster Needs admin page.
 *
 * Fetches aggregated gear needs from:
 *   GET /api/v1/admin/gear-needs/raid
 *   GET /api/v1/admin/gear-needs/dungeon
 *
 * Renders two tables: hierarchical raid (instance→boss) and flat M+ dungeon.
 * Expand/collapse for raid instances; track columns auto-hide when empty.
 * Phase 1E.1: tables only. Drill panel is Phase 1E.2.
 */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const TRACK_ORDER  = ['V', 'C', 'H', 'M'];
const TRACK_LABEL  = { V: 'Veteran', C: 'Champion', H: 'Hero', M: 'Myth' };
const TRACK_COLORS = { V: '#94a3b8', C: '#60a5fa', H: '#a78bfa', M: '#fb923c' };

// Set of instance names currently expanded (all expanded by default after load)
const _openInst = new Set();

// Cached API responses (used for re-render on instance toggle)
let _raidData   = null;
let _dungData   = null;
let _lastParams = null;

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

    // Determine which tracks have any data across all bosses
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

        // Instance summary row
        html += `<tr class="row-inst${isOpen ? ' is-open' : ''}" onclick="_toggleInst(${JSON.stringify(inst.name)})">`;
        html += `<td class="col-name"><span class="expand-btn">▶</span>${_esc(inst.name)}</td>`;
        for (const t of activeTracks) {
            const roll = inst.rollup[t];
            html += '<td>' + (roll ? _chipHtml(roll.player_count, roll.item_count) : '') + '</td>';
        }
        html += '</tr>';

        // Boss rows (hidden when instance collapsed)
        for (const boss of inst.bosses) {
            html += `<tr class="row-boss${isOpen ? '' : ' hidden'}" data-inst="${_esc(inst.name)}">`;
            html += `<td class="col-name">${_esc(boss.name)}</td>`;
            for (const t of activeTracks) {
                const td = boss.tracks[t];
                html += '<td>' + (td ? _chipHtml(td.player_count, td.item_count) : '') + '</td>';
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
            html += '<td>' + (td ? _chipHtml(td.player_count, td.item_count) : '') + '</td>';
        }
        html += '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Fetch + render
// ---------------------------------------------------------------------------

async function loadRosterNeeds() {
    const includeInitiates = document.getElementById('cb-init')?.checked ?? true;
    const includeOffspec   = document.getElementById('cb-os')?.checked   ?? false;
    const params = `?include_initiates=${includeInitiates}&include_offspec=${includeOffspec}`;

    if (params === _lastParams) {
        // Re-render from cache (e.g., after instance toggle) — only happens internally
    }

    const raidWrap = document.getElementById('rn-raid-table-wrap');
    const dungWrap = document.getElementById('rn-dung-table-wrap');
    const spin = '<p class="rn-loading"><span class="spinner"></span> Loading\u2026</p>';
    if (raidWrap) raidWrap.innerHTML = spin;
    if (dungWrap) dungWrap.innerHTML = spin;
    _setStatus('');

    try {
        const [raidResp, dungResp] = await Promise.all([
            fetch(`/api/v1/admin/gear-needs/raid${params}`),
            fetch(`/api/v1/admin/gear-needs/dungeon${params}`),
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

        // Expand all instances by default on first load (or after a filter change)
        _openInst.clear();
        for (const inst of _raidData) _openInst.add(inst.name);

        _renderRaidTable(_raidData);
        _renderDungeonTable(_dungData);
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
    loadRosterNeeds();
});
