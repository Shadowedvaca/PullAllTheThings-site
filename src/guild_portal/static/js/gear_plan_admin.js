/**
 * gear_plan_admin.js — Admin BIS Sync Dashboard interactions.
 *
 * Manages:
 *   - BIS source × spec matrix rendering
 *   - Discover URLs, Sync Source, Sync All controls
 *   - Cell drill-down (per-slot BIS entries)
 *   - Cross-reference panel
 *   - Scrape log panel
 *   - SimC import modal
 */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _sources = [];
let _specs = [];
let _cells = {};        // {spec_id: {source_id: {status, items_found, ...}}}
let _htBySpec = {};     // {spec_id: [{id, name, slug}]}
let _logVisible = false;

// Active drill-down target
let _drillSpecId = null;
let _drillSourceId = null;
let _drillHtId = null;

// ---------------------------------------------------------------------------
// Status bar helpers
// ---------------------------------------------------------------------------

function setStatus(msg, type = '') {
    const bar = document.getElementById('gp-status');
    bar.className = 'gp-status-bar' + (type ? ` gp-status-bar--${type}` : '');
    bar.querySelector('.gp-status-bar__msg').textContent = msg;
}

function setStatusHtml(html, type = '') {
    const bar = document.getElementById('gp-status');
    bar.className = 'gp-status-bar' + (type ? ` gp-status-bar--${type}` : '');
    bar.querySelector('.gp-status-bar__msg').innerHTML = html;
}

// ---------------------------------------------------------------------------
// Load matrix
// ---------------------------------------------------------------------------

async function loadMatrix() {
    setStatus('Loading matrix…');
    try {
        const r = await fetch('/api/v1/admin/bis/matrix');
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        _sources = d.sources || [];
        _specs   = d.specs   || [];
        _cells   = d.cells   || {};

        // Build hero talent lookup
        _htBySpec = {};
        for (const sp of _specs) {
            _htBySpec[sp.id] = sp.hero_talents || [];
        }

        renderMatrix();
        populateSpecSelectors();
        populateSourceSelector();
        setStatus(`Matrix loaded — ${_specs.length} specs × ${_sources.length} sources.`);
    } catch (err) {
        setStatus('Error loading matrix: ' + err.message, 'error');
    }
}

const _CONTENT_TYPE_LABELS = {
    raid: 'Raid',
    mythic_plus: 'M+',
    overall: 'Overall',
};

function renderMatrix() {
    const head = document.getElementById('gp-matrix-head');
    const body = document.getElementById('gp-matrix-body');

    // Two-row header: row 1 = Spec (rowspan2) + HT (rowspan2) + website groups (colspan)
    //                 row 2 = individual content-type sub-columns per website

    // Build website groups in source order (dedup by origin, preserve first-seen order)
    const originOrder = [];
    const originSources = {};  // origin → [source, ...]
    for (const src of _sources) {
        const origin = src.origin || 'other';
        if (!originSources[origin]) {
            originSources[origin] = [];
            originOrder.push(origin);
        }
        originSources[origin].push(src);
    }

    const row1 = document.createElement('tr');

    // Spec — rowspan 2
    const thSpec = document.createElement('th');
    thSpec.className = 'gp-th-spec';
    thSpec.rowSpan = 2;
    thSpec.textContent = 'Spec';
    row1.appendChild(thSpec);

    // Hero Talent — rowspan 2
    const thHt = document.createElement('th');
    thHt.className = 'gp-th-ht';
    thHt.rowSpan = 2;
    thHt.textContent = 'Hero Talent';
    row1.appendChild(thHt);

    // Website group headers (row 1)
    for (const origin of originOrder) {
        const srcs = originSources[origin];
        const th = document.createElement('th');
        th.colSpan = srcs.length;
        th.textContent = _ORIGIN_LABELS[origin] || origin;
        th.style.cssText = 'text-align:center; border-left:1px solid #333;';
        row1.appendChild(th);
    }

    // Row 2: content-type sub-headers
    const row2 = document.createElement('tr');
    for (const origin of originOrder) {
        const srcs = originSources[origin];
        srcs.forEach((src, idx) => {
            const th = document.createElement('th');
            th.textContent = _CONTENT_TYPE_LABELS[src.content_type] || src.short_label || src.name;
            th.title = src.name;
            th.style.cssText = 'font-weight:400; font-size:0.72rem;' + (idx === 0 ? 'border-left:1px solid #333;' : '');
            row2.appendChild(th);
        });
    }

    head.innerHTML = '';
    head.appendChild(row1);
    head.appendChild(row2);

    // Body rows — group by class; each spec becomes N sub-rows (one per HT)
    body.innerHTML = '';
    let lastClass = null;

    for (const sp of _specs) {
        // Class group divider
        if (sp.class_name !== lastClass) {
            lastClass = sp.class_name;
            const divRow = document.createElement('tr');
            const divTd = document.createElement('td');
            divTd.colSpan = _sources.length + 2;
            divTd.style.cssText = 'padding:0.25rem 0.75rem; background:#111114; color:var(--color-text-muted); font-size:0.75rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em;';
            divTd.textContent = sp.class_name;
            divRow.appendChild(divTd);
            body.appendChild(divRow);
        }

        // Flatten sources in origin-group order (same as header)
        const orderedSources = [];
        for (const origin of originOrder) {
            for (const src of (originSources[origin] || [])) {
                orderedSources.push(src);
            }
        }

        const htOptions = (_htBySpec[sp.id] || []);

        if (htOptions.length === 0) {
            // No hero talents — single row, HT cell shows "—"
            const row = document.createElement('tr');

            const tdSpec = document.createElement('td');
            tdSpec.className = 'gp-td-spec';
            tdSpec.textContent = sp.spec_name;
            row.appendChild(tdSpec);

            const tdHt = document.createElement('td');
            tdHt.className = 'gp-td-ht';
            tdHt.textContent = '—';
            row.appendChild(tdHt);

            orderedSources.forEach((src, idx) => {
                const td = document.createElement('td');
                if (idx === 0 || orderedSources[idx - 1].origin !== src.origin) {
                    td.style.borderLeft = '1px solid #333';
                }
                td.appendChild(renderCell(sp.id, src.id));
                td.addEventListener('click', () => drillDown(sp.id, src.id));
                row.appendChild(td);
            });
            body.appendChild(row);
        } else {
            // One sub-row per hero talent; spec name spans all sub-rows
            htOptions.forEach((ht, idx) => {
                const row = document.createElement('tr');

                if (idx === 0) {
                    // Spec name cell spans all HT rows
                    const tdSpec = document.createElement('td');
                    tdSpec.className = 'gp-td-spec';
                    tdSpec.rowSpan = htOptions.length;
                    tdSpec.textContent = sp.spec_name;
                    row.appendChild(tdSpec);
                }

                const tdHt = document.createElement('td');
                tdHt.className = 'gp-td-ht';
                tdHt.textContent = ht.name;
                row.appendChild(tdHt);

                orderedSources.forEach((src, srcIdx) => {
                    const td = document.createElement('td');
                    if (srcIdx === 0 || orderedSources[srcIdx - 1].origin !== src.origin) {
                        td.style.borderLeft = '1px solid #333';
                    }
                    td.appendChild(renderCell(sp.id, src.id, ht.id));
                    td.addEventListener('click', () => drillDown(sp.id, src.id, ht.id));
                    row.appendChild(td);
                });
                body.appendChild(row);
            });
        }
    }
}

function renderCell(specId, sourceId, htId) {
    // _cells keyed by spec_id → source_id (the matrix endpoint aggregates across HTs)
    const cellData = (_cells[specId] || {})[sourceId];
    const wrapper = document.createElement('span');

    if (!cellData) {
        wrapper.className = 'gp-cell gp-cell--empty';
        wrapper.textContent = '—';
        return wrapper;
    }

    const status = cellData.status || 'pending';
    wrapper.className = `gp-cell gp-cell--${status}`;
    wrapper.dataset.targetId = cellData.target_id || '';
    wrapper.dataset.specId = specId;
    wrapper.dataset.sourceId = sourceId;

    const count = document.createElement('span');
    count.className = 'gp-cell__count';
    count.textContent = (cellData.items_found || 0) + '/16';
    wrapper.appendChild(count);

    if (cellData.technique) {
        const tech = document.createElement('span');
        tech.className = 'gp-cell__tech';
        tech.textContent = _techIcon(cellData.technique);
        wrapper.appendChild(tech);
    }

    if (cellData.last_fetched) {
        const d = new Date(cellData.last_fetched);
        wrapper.title = `Last synced: ${d.toLocaleString()} • ${cellData.technique || ''}`;
    }

    return wrapper;
}

function _techIcon(technique) {
    const icons = {
        json_embed: '[JSON]',
        wh_gatherer: '[WH]',
        html_parse: '[HTML]',
        simc: '[SimC]',
        manual: '[Manual]',
    };
    return icons[technique] || technique;
}

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

function populateSpecSelectors() {
    const xrefSel = document.getElementById('xref-spec-select');
    const simcSel = document.getElementById('simc-spec-select');

    xrefSel.innerHTML = '<option value="">— select spec —</option>';
    simcSel.innerHTML = '<option value="">— select spec —</option>';

    let lastClass = null;
    for (const sp of _specs) {
        const label = sp.class_name + ' — ' + sp.spec_name;

        const opt1 = document.createElement('option');
        opt1.value = sp.id;
        opt1.textContent = label;
        xrefSel.appendChild(opt1);

        const opt2 = document.createElement('option');
        opt2.value = sp.id;
        opt2.textContent = label;
        simcSel.appendChild(opt2);
    }
}

const _ORIGIN_LABELS = {
    archon:    'Archon',
    wowhead:   'Wowhead',
    icy_veins: 'Icy Veins',
};

function populateSourceSelector() {
    // Website (origin) dropdown — deduplicated, sorted by first appearance
    const originSel = document.getElementById('sync-origin-select');
    if (originSel) {
        const seen = new Set();
        const origins = [];
        for (const src of _sources) {
            if (src.origin && !seen.has(src.origin)) {
                seen.add(src.origin);
                origins.push(src.origin);
            }
        }
        originSel.innerHTML = '<option value="">— select —</option>';
        for (const origin of origins) {
            const opt = document.createElement('option');
            opt.value = origin;
            opt.textContent = _ORIGIN_LABELS[origin] || origin;
            originSel.appendChild(opt);
        }
    }

    // SimC modal source dropdown (keeps full source list)
    const simcSel = document.getElementById('simc-source-select');
    if (!simcSel) return;
    simcSel.innerHTML = '';
    for (const src of _sources) {
        const opt = document.createElement('option');
        opt.value = src.id;
        opt.textContent = src.name;
        simcSel.appendChild(opt);
    }
}

// ---------------------------------------------------------------------------
// Load sources separately (for source dropdown)
// ---------------------------------------------------------------------------

async function loadSources() {
    try {
        const r = await fetch('/api/v1/admin/bis/sources');
        const d = await r.json();
        if (d.ok) {
            _sources = d.sources || _sources;
            populateSourceSelector();
        }
    } catch (_) {}
}

// ---------------------------------------------------------------------------
// Control actions
// ---------------------------------------------------------------------------

async function discoverTargets() {
    setStatusHtml('<span class="spinner"></span> Discovering targets…', 'running');
    try {
        const r = await fetch('/api/v1/admin/bis/targets/discover', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadMatrix();
        setStatusHtml(
            `${d.inserted} targets added, ${d.skipped} already existed. ` +
            `<span class="spinner"></span> Icy Veins areas discovering in background…`,
            'running'
        );
        _pollIvDiscovery();
    } catch (err) {
        setStatus('Discovery failed: ' + err.message, 'error');
    }
}

// Poll for IV targets until the count stabilises, then refresh matrix + clear spinner.
async function _pollIvDiscovery() {
    let lastCount = -1;
    let stableRounds = 0;
    let zeroRounds = 0;
    const MAX_POLLS = 18;   // 18 × 10s = 3 min hard cap
    const ZERO_LIMIT = 6;   // give up after 60s with 0 targets found

    for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise(res => setTimeout(res, 10000));
        try {
            const r = await fetch('/api/v1/admin/bis/targets');
            const d = await r.json();
            if (!d.ok) break;
            const ivCount = (d.targets || []).filter(t => t.origin === 'icy_veins').length;

            if (ivCount === 0) {
                zeroRounds++;
                if (zeroRounds >= ZERO_LIMIT) {
                    // Still nothing after 60s — discovery likely failed or is very slow
                    setStatus(
                        'Icy Veins area discovery returned no targets. ' +
                        'The site may have blocked the requests — you can edit URLs manually in the Targets panel.',
                        'error'
                    );
                    if (_targetsVisible) loadTargets();
                    return;
                }
                // Keep waiting — update message with elapsed time
                setStatusHtml(
                    `<span class="spinner"></span> Icy Veins areas discovering… (${zeroRounds * 10}s elapsed)`,
                    'running'
                );
            } else if (ivCount === lastCount) {
                stableRounds++;
                if (stableRounds >= 2) {
                    await loadMatrix();
                    setStatus(`Discovery complete — ${ivCount} Icy Veins targets found.`, 'success');
                    if (_targetsVisible) loadTargets();
                    return;
                }
            } else {
                stableRounds = 0;
                zeroRounds = 0;
                setStatusHtml(
                    `<span class="spinner"></span> Icy Veins areas discovering… ${ivCount} found so far.`,
                    'running'
                );
            }
            lastCount = ivCount;
        } catch (_) {
            break;
        }
    }
    await loadMatrix();
    setStatus('Icy Veins discovery timed out — check Targets panel for what was found.', 'success');
    if (_targetsVisible) loadTargets();
}

async function syncSource() {
    const originSel   = document.getElementById('sync-origin-select');
    const planTypeSel = document.getElementById('sync-plan-type-select');

    const origin      = originSel?.value;
    const contentType = planTypeSel?.value;

    if (!origin) {
        setStatus('Select a website first.', 'error');
        return;
    }
    if (!contentType) {
        setStatus('Select a plan type first.', 'error');
        return;
    }

    // Resolve to source_id
    const src = _sources.find(s => s.origin === origin && s.content_type === contentType);
    if (!src) {
        const originLabel = _ORIGIN_LABELS[origin] || origin;
        setStatus(`No source exists for ${originLabel} + ${planTypeSel.options[planTypeSel.selectedIndex].text}.`, 'error');
        return;
    }

    const sourceName = src.name;
    setStatusHtml(`<span class="spinner"></span> Syncing ${sourceName}… (running in background)`, 'running');
    try {
        const r = await fetch(`/api/v1/admin/bis/sync/${src.id}`, { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        setStatus(`${sourceName} sync started. Refresh matrix in a moment to see progress.`, 'success');
    } catch (err) {
        setStatus('Sync failed: ' + err.message, 'error');
    }
}

async function syncAll() {
    if (!confirm('Run full BIS sync for all sources and all specs? This may take several minutes.')) return;
    setStatusHtml('<span class="spinner"></span> Full BIS sync started… (running in background)', 'running');
    try {
        const r = await fetch('/api/v1/admin/bis/sync', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        setStatus('Full sync started in background. Refresh matrix to track progress.', 'success');
    } catch (err) {
        setStatus('Sync failed: ' + err.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Drill-down
// ---------------------------------------------------------------------------

async function drillDown(specId, sourceId, htId) {
    _drillSpecId = specId;
    _drillSourceId = sourceId;
    _drillHtId = htId || null;

    const specInfo = _specs.find(s => s.id == specId);
    const srcInfo  = _sources.find(s => s.id == sourceId);

    const panel = document.getElementById('gp-detail-panel');
    const title = document.getElementById('gp-detail-title');
    const slotsEl = document.getElementById('gp-detail-slots');
    const actionsEl = document.getElementById('gp-detail-actions');

    title.textContent = `BIS Entries — ${srcInfo?.name || sourceId} | ${specInfo?.class_name} ${specInfo?.spec_name}`;
    slotsEl.innerHTML = '<span style="color:var(--color-text-muted);">Loading…</span>';
    actionsEl.innerHTML = '';
    panel.classList.add('visible');

    // Scroll to panel
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    try {
        let url = `/api/v1/admin/bis/entries?source_id=${sourceId}&spec_id=${specId}`;
        if (_drillHtId) url += `&hero_talent_id=${_drillHtId}`;
        const r = await fetch(url);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        renderDrillDown(d.entries || [], specId, sourceId);

        // Actions row
        const cellData = (_cells[specId] || {})[sourceId];
        if (cellData?.target_id) {
            const resyncBtn = document.createElement('button');
            resyncBtn.className = 'btn-sm btn-secondary';
            resyncBtn.textContent = 'Re-sync this target';
            resyncBtn.onclick = () => resyncTarget(cellData.target_id);
            actionsEl.appendChild(resyncBtn);
        }
    } catch (err) {
        slotsEl.innerHTML = `<span style="color:#f87171;">Error: ${err.message}</span>`;
    }
}

// Canonical slot order
const SLOT_ORDER = [
    'head', 'neck', 'shoulder', 'back', 'chest', 'wrist',
    'hands', 'waist', 'legs', 'feet',
    'ring_1', 'ring_2', 'trinket_1', 'trinket_2',
    'main_hand', 'off_hand',
];

function renderDrillDown(entries, specId, sourceId) {
    const slotsEl = document.getElementById('gp-detail-slots');
    slotsEl.innerHTML = '';

    // Build slot → entries map
    const bySlot = {};
    for (const e of entries) {
        if (!bySlot[e.slot]) bySlot[e.slot] = [];
        bySlot[e.slot].push(e);
    }

    for (const slot of SLOT_ORDER) {
        const row = document.createElement('div');
        row.className = 'gp-slot-row';

        const label = document.createElement('span');
        label.className = 'gp-slot-row__label';
        label.textContent = _slotLabel(slot);
        row.appendChild(label);

        const itemEl = document.createElement('span');
        itemEl.className = 'gp-slot-row__item';

        const slotEntries = bySlot[slot] || [];
        if (slotEntries.length > 0) {
            const e = slotEntries[0];
            const link = document.createElement('a');
            link.href = `https://www.wowhead.com/item=${e.blizzard_item_id}`;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = e.item_name || `Item #${e.blizzard_item_id}`;
            itemEl.appendChild(link);
        } else {
            const miss = document.createElement('span');
            miss.className = 'gp-slot-row__missing';
            miss.textContent = '— missing —';
            itemEl.appendChild(miss);
        }
        row.appendChild(itemEl);
        slotsEl.appendChild(row);
    }
}

function _slotLabel(slot) {
    const labels = {
        head: 'Head', neck: 'Neck', shoulder: 'Shoulder', back: 'Back',
        chest: 'Chest', wrist: 'Wrist', hands: 'Hands', waist: 'Waist',
        legs: 'Legs', feet: 'Feet', ring_1: 'Ring 1', ring_2: 'Ring 2',
        trinket_1: 'Trinket 1', trinket_2: 'Trinket 2',
        main_hand: 'Main Hand', off_hand: 'Off Hand',
    };
    return labels[slot] || slot;
}

async function resyncTarget(targetId) {
    setStatusHtml(`<span class="spinner"></span> Re-syncing target ${targetId}…`, 'running');
    try {
        const r = await fetch(`/api/v1/admin/bis/sync/target/${targetId}`, { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        setStatus(`Re-sync complete — ${d.items_upserted} items, status: ${d.status}`, 'success');
        await loadMatrix();
        if (_drillSpecId && _drillSourceId) {
            await drillDown(_drillSpecId, _drillSourceId);
        }
    } catch (err) {
        setStatus('Re-sync failed: ' + err.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Cross-reference
// ---------------------------------------------------------------------------

async function loadXref() {
    const specId  = document.getElementById('xref-spec-select').value;
    const htId    = document.getElementById('xref-ht-select').value;
    const content = document.getElementById('gp-xref-content');

    if (!specId) {
        content.innerHTML = '<span style="color:var(--color-text-muted);">Select a spec above to compare sources.</span>';
        return;
    }

    content.innerHTML = '<span class="spinner"></span> Loading…';

    try {
        let url = `/api/v1/admin/bis/cross-reference?spec_id=${specId}`;
        if (htId) url += `&hero_talent_id=${htId}`;
        const r = await fetch(url);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        renderXref(d.by_slot || {});
    } catch (err) {
        content.innerHTML = `<span style="color:#f87171;">Error: ${err.message}</span>`;
    }
}

// Also update hero talent options when spec changes in xref
document.addEventListener('DOMContentLoaded', () => {
    const xrefSpecSel = document.getElementById('xref-spec-select');
    const xrefHtSel   = document.getElementById('xref-ht-select');

    xrefSpecSel.addEventListener('change', () => {
        const specId = xrefSpecSel.value;
        xrefHtSel.innerHTML = '<option value="">All builds</option>';
        if (specId && _htBySpec[specId]) {
            for (const ht of _htBySpec[specId]) {
                const opt = document.createElement('option');
                opt.value = ht.id;
                opt.textContent = ht.name;
                xrefHtSel.appendChild(opt);
            }
        }
        loadXref();
    });
});

function renderXref(bySlot) {
    const content = document.getElementById('gp-xref-content');

    if (Object.keys(bySlot).length === 0) {
        content.innerHTML = '<span style="color:var(--color-text-muted);">No BIS data available for this spec.</span>';
        return;
    }

    const table = document.createElement('table');
    table.className = 'gp-xref-table';

    // Header
    const thead = document.createElement('thead');
    const hRow = document.createElement('tr');
    hRow.innerHTML = '<th>Slot</th>';
    for (const src of _sources) {
        const th = document.createElement('th');
        th.textContent = src.short_label || src.name;
        hRow.appendChild(th);
    }
    hRow.innerHTML += '<th>Agreement</th>';
    thead.appendChild(hRow);
    table.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    for (const slot of SLOT_ORDER) {
        const entries = bySlot[slot] || [];
        const row = document.createElement('tr');

        const slotTd = document.createElement('td');
        slotTd.className = 'gp-xref-slot-label';
        slotTd.textContent = _slotLabel(slot);
        row.appendChild(slotTd);

        // Per-source cells
        const entryBySrc = {};
        for (const e of entries) {
            entryBySrc[e.source_id] = e;
        }

        for (const src of _sources) {
            const td = document.createElement('td');
            const e = entryBySrc[src.id];
            if (e) {
                const link = document.createElement('a');
                link.href = `https://www.wowhead.com/item=${e.blizzard_item_id}`;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.style.color = e.agrees ? '#4ade80' : '#fbbf24';
                link.textContent = e.item_name || `#${e.blizzard_item_id}`;
                td.appendChild(link);
            } else {
                td.style.color = '#444';
                td.textContent = '—';
            }
            row.appendChild(td);
        }

        // Agreement cell
        const agreeTd = document.createElement('td');
        if (entries.length === 0) {
            agreeTd.textContent = '—';
            agreeTd.style.color = '#444';
        } else {
            const allAgree = entries.every(e => e.agrees);
            agreeTd.textContent = allAgree ? '✓' : '!';
            agreeTd.className = allAgree ? 'gp-xref-agree' : 'gp-xref-disagree';
        }
        row.appendChild(agreeTd);
        tbody.appendChild(row);
    }
    table.appendChild(tbody);

    content.innerHTML = '';
    content.appendChild(table);
}

// ---------------------------------------------------------------------------
// Scrape targets
// ---------------------------------------------------------------------------

let _targetsVisible = false;
let _allTargets = [];  // full list fetched from API

function toggleTargets() {
    _targetsVisible = !_targetsVisible;
    document.getElementById('gp-targets-content').style.display = _targetsVisible ? 'block' : 'none';
    document.getElementById('targets-toggle-icon').textContent = _targetsVisible ? '▲' : '▼';
    if (_targetsVisible && _allTargets.length === 0) loadTargets();
}

async function loadTargets() {
    const tbody = document.getElementById('gp-targets-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="10" style="color:var(--color-text-muted);">Loading…</td></tr>';

    try {
        const r = await fetch('/api/v1/admin/bis/targets');
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        _allTargets = d.targets || [];
        _populateTargetsSourceFilter();
        _renderTargets();
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="10" style="color:#f87171;">Error: ${err.message}</td></tr>`;
    }
}

function _populateTargetsSourceFilter() {
    const sel = document.getElementById('targets-filter-source');
    if (!sel) return;
    const currentVal = sel.value;
    // Unique origins from loaded targets, in appearance order
    const seen = new Set();
    const origins = [];
    for (const t of _allTargets) {
        const origin = t.origin || '';
        if (origin && !seen.has(origin)) {
            seen.add(origin);
            origins.push(origin);
        }
    }
    sel.innerHTML = '<option value="">All websites</option>';
    for (const origin of origins) {
        const opt = document.createElement('option');
        opt.value = origin;
        opt.textContent = _ORIGIN_LABELS[origin] || origin;
        if (origin === currentVal) opt.selected = true;
        sel.appendChild(opt);
    }
}

function _renderTargets() {
    const tbody = document.getElementById('gp-targets-body');
    const countEl = document.getElementById('targets-count');
    if (!tbody) return;

    const filterOrigin = document.getElementById('targets-filter-source')?.value || '';
    const filterStatus = document.getElementById('targets-filter-status')?.value || '';

    const filtered = _allTargets.filter(t => {
        if (filterOrigin && t.origin !== filterOrigin) return false;
        if (filterStatus && t.status !== filterStatus) return false;
        return true;
    });

    if (countEl) countEl.textContent = `${filtered.length} / ${_allTargets.length} targets`;

    tbody.innerHTML = '';
    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" style="color:var(--color-text-muted); padding:1rem;">No targets match filter.</td></tr>';
        return;
    }

    for (const t of filtered) {
        const tr = document.createElement('tr');
        tr.dataset.targetId = t.id;

        const ts = t.last_fetched ? new Date(t.last_fetched).toLocaleDateString() : '—';
        const statusClass = `gp-log-status-${t.status || 'pending'}`;
        const isIV = t.origin === 'icy_veins';

        // Spec cell
        const specTd = document.createElement('td');
        specTd.textContent = `${t.class_name || ''} ${t.spec_name || ''}`;
        tr.appendChild(specTd);

        // Hero Talent cell — editable select for IV rows
        const htTd = document.createElement('td');
        htTd.style.cssText = 'font-size:0.78rem;';
        if (isIV && window._isGl) {
            _renderHtSelect(htTd, t);
        } else {
            htTd.style.color = 'var(--color-text-muted)';
            htTd.style.fontStyle = 'italic';
            htTd.textContent = t.hero_talent_name || '—';
        }
        tr.appendChild(htTd);

        // Area Label cell
        const areaLabelTd = document.createElement('td');
        areaLabelTd.className = 'gp-area-label';
        areaLabelTd.title = t.area_label || '';
        areaLabelTd.textContent = t.area_label || '—';
        tr.appendChild(areaLabelTd);

        // Source cell
        const srcTd = document.createElement('td');
        srcTd.textContent = _ORIGIN_LABELS[t.origin] || t.source_name || '—';
        tr.appendChild(srcTd);

        // Content Type cell — editable select for IV rows
        const ctTd = document.createElement('td');
        ctTd.style.cssText = 'font-size:0.78rem;';
        if (isIV && window._isGl) {
            _renderCtSelect(ctTd, t);
        } else {
            ctTd.textContent = _CONTENT_TYPE_LABELS[t.content_type] || t.content_type || '—';
        }
        tr.appendChild(ctTd);

        // URL cell
        const urlTd = document.createElement('td');
        urlTd.style.maxWidth = '340px';
        _renderTargetUrlCell(urlTd, t);
        tr.appendChild(urlTd);

        // Status, Items, Last Synced
        const statusTd = document.createElement('td');
        statusTd.className = statusClass;
        statusTd.textContent = t.status || 'pending';
        tr.appendChild(statusTd);

        const itemsTd = document.createElement('td');
        itemsTd.textContent = t.items_found || 0;
        tr.appendChild(itemsTd);

        const tsTd = document.createElement('td');
        tsTd.style.fontSize = '0.75rem';
        tsTd.textContent = ts;
        tr.appendChild(tsTd);

        // Actions cell (GL only)
        const actTd = document.createElement('td');
        actTd.style.cssText = 'white-space:nowrap;';
        if (window._isGl) {
            const editBtn = document.createElement('button');
            editBtn.className = 'btn-sm btn-secondary';
            editBtn.style.cssText = 'padding:0.2rem 0.5rem; font-size:0.75rem; margin-right:0.3rem;';
            editBtn.textContent = 'Edit URL';
            editBtn.onclick = () => _startEditUrl(tr, t);
            actTd.appendChild(editBtn);

            const syncBtn = document.createElement('button');
            syncBtn.className = 'btn-sm btn-secondary';
            syncBtn.style.cssText = 'padding:0.2rem 0.5rem; font-size:0.75rem;';
            syncBtn.textContent = 'Sync';
            syncBtn.onclick = () => resyncTarget(t.id);
            actTd.appendChild(syncBtn);
        }
        tr.appendChild(actTd);

        tbody.appendChild(tr);
    }
}

function _renderHtSelect(td, target) {
    const specHts = _htBySpec[target.spec_id] || [];
    const sel = document.createElement('select');
    sel.className = 'gp-target-inline-select';

    const none = document.createElement('option');
    none.value = '';
    none.textContent = '— any —';
    if (!target.hero_talent_id) none.selected = true;
    sel.appendChild(none);

    for (const ht of specHts) {
        const opt = document.createElement('option');
        opt.value = ht.id;
        opt.textContent = ht.name;
        if (ht.id === target.hero_talent_id) opt.selected = true;
        sel.appendChild(opt);
    }

    sel.onchange = async () => {
        const htId = sel.value ? parseInt(sel.value) : null;
        await _saveTargetMeta(target, { hero_talent_id: htId });
        target.hero_talent_id = htId;
        const htName = htId ? (specHts.find(h => h.id === htId)?.name || null) : null;
        target.hero_talent_name = htName;
    };

    td.appendChild(sel);
}

function _renderCtSelect(td, target) {
    const sel = document.createElement('select');
    sel.className = 'gp-target-inline-select';

    const opts = [
        { value: 'overall', label: 'Overall' },
        { value: 'raid', label: 'Raid' },
        { value: 'mythic_plus', label: 'M+' },
    ];
    for (const o of opts) {
        const opt = document.createElement('option');
        opt.value = o.value;
        opt.textContent = o.label;
        if (o.value === target.content_type) opt.selected = true;
        sel.appendChild(opt);
    }

    sel.onchange = async () => {
        await _saveTargetMeta(target, { content_type: sel.value });
        target.content_type = sel.value;
    };

    td.appendChild(sel);
}

async function _saveTargetMeta(target, updates) {
    try {
        const r = await fetch(`/api/v1/admin/bis/targets/${target.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        setStatus(`Target ${target.id} updated.`, 'success');
    } catch (err) {
        setStatus('Failed to update target: ' + err.message, 'error');
    }
}

function _renderTargetUrlCell(td, target) {
    td.innerHTML = '';
    const wrapper = document.createElement('div');
    wrapper.className = 'gp-target-url';

    if (target.url) {
        const a = document.createElement('a');
        a.href = target.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = target.url;
        a.title = target.url;
        wrapper.appendChild(a);
    } else {
        const empty = document.createElement('span');
        empty.style.cssText = 'color:#444; font-style:italic; font-size:0.78rem;';
        empty.textContent = '— no URL —';
        wrapper.appendChild(empty);
    }
    td.appendChild(wrapper);
}

function _startEditUrl(tr, target) {
    // Find the URL td (index 5 — after spec, HT, area_label, source, content_type)
    const tds = tr.querySelectorAll('td');
    const urlTd = tds[5];
    urlTd.innerHTML = '';

    const wrapper = document.createElement('div');
    wrapper.className = 'gp-target-url';

    const input = document.createElement('input');
    input.type = 'text';
    input.value = target.url || '';
    input.placeholder = 'Enter URL…';

    const saveBtn = document.createElement('button');
    saveBtn.className = 'btn-sm btn-primary';
    saveBtn.style.cssText = 'padding:0.2rem 0.5rem; font-size:0.75rem; white-space:nowrap;';
    saveBtn.textContent = 'Save';
    saveBtn.onclick = () => _saveTargetUrl(target, input.value.trim(), tr);

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn-sm btn-secondary';
    cancelBtn.style.cssText = 'padding:0.2rem 0.5rem; font-size:0.75rem;';
    cancelBtn.textContent = '✕';
    cancelBtn.onclick = () => _renderTargetUrlCell(urlTd, target);

    wrapper.appendChild(input);
    wrapper.appendChild(saveBtn);
    wrapper.appendChild(cancelBtn);
    urlTd.appendChild(wrapper);
    input.focus();
    input.select();
}

async function _saveTargetUrl(target, newUrl, tr) {
    try {
        const r = await fetch(`/api/v1/admin/bis/targets/${target.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: newUrl }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        // Update local state
        target.url = newUrl;
        const tds = tr.querySelectorAll('td');
        _renderTargetUrlCell(tds[5], target);
        setStatus(`URL updated for target ${target.id}.`, 'success');
    } catch (err) {
        setStatus('Failed to save URL: ' + err.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Scrape log
// ---------------------------------------------------------------------------

function toggleLog() {
    _logVisible = !_logVisible;
    document.getElementById('gp-log-content').style.display = _logVisible ? 'block' : 'none';
    document.getElementById('log-toggle-icon').textContent = _logVisible ? '▲' : '▼';
    if (_logVisible) loadLog();
}

async function loadLog() {
    const tbody = document.getElementById('gp-log-body');
    tbody.innerHTML = '<tr><td colspan="7" style="color:var(--color-text-muted);">Loading…</td></tr>';
    try {
        const r = await fetch('/api/v1/admin/bis/scrape-log?limit=30');
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        renderLog(d.log || []);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" style="color:#f87171;">Error: ${err.message}</td></tr>`;
    }
}

function renderLog(entries) {
    const tbody = document.getElementById('gp-log-body');
    tbody.innerHTML = '';

    if (entries.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="color:var(--color-text-muted); padding:1rem;">No extraction attempts yet.</td></tr>';
        return;
    }

    for (const e of entries) {
        const tr = document.createElement('tr');
        const ts = e.created_at ? new Date(e.created_at).toLocaleString() : '—';
        const statusClass = `gp-log-status-${e.status || 'pending'}`;
        tr.innerHTML = `
            <td>${ts}</td>
            <td>${e.class_name || ''} ${e.spec_name || ''}</td>
            <td>${e.source_name || ''}</td>
            <td>${e.technique || '—'}</td>
            <td class="${statusClass}">${e.status || '—'}</td>
            <td>${e.items_found || 0}</td>
            <td style="font-size:0.75rem; color:#f87171; max-width:200px; overflow:hidden; text-overflow:ellipsis;" title="${(e.error_message || '').replace(/"/g, '&quot;')}">
                ${e.error_message || ''}
            </td>
        `;
        tbody.appendChild(tr);
    }
}

// ---------------------------------------------------------------------------
// SimC import modal
// ---------------------------------------------------------------------------

function openSimcModal() {
    document.getElementById('simc-modal').classList.add('visible');
}

function closeSimcModal(event) {
    if (!event || event.target === document.getElementById('simc-modal')) {
        document.getElementById('simc-modal').classList.remove('visible');
        document.getElementById('simc-text').value = '';
    }
}

function onSimcSpecChange() {
    const specId = document.getElementById('simc-spec-select').value;
    const htSel  = document.getElementById('simc-ht-select');
    htSel.innerHTML = '<option value="">None / All builds</option>';
    if (specId && _htBySpec[specId]) {
        for (const ht of _htBySpec[specId]) {
            const opt = document.createElement('option');
            opt.value = ht.id;
            opt.textContent = ht.name;
            htSel.appendChild(opt);
        }
    }
}

async function submitSimcImport() {
    const sourceId  = document.getElementById('simc-source-select').value;
    const specId    = document.getElementById('simc-spec-select').value;
    const htId      = document.getElementById('simc-ht-select').value;
    const simcText  = document.getElementById('simc-text').value.trim();

    if (!sourceId || !specId) {
        setStatus('Please select a source and spec before importing.', 'error');
        return;
    }
    if (!simcText) {
        setStatus('No SimC text provided.', 'error');
        return;
    }

    closeSimcModal();
    setStatusHtml('<span class="spinner"></span> Importing SimC profile…', 'running');

    try {
        const body = {
            simc_text: simcText,
            source_id: parseInt(sourceId),
            spec_id:   parseInt(specId),
            hero_talent_id: htId ? parseInt(htId) : null,
        };
        const r = await fetch('/api/v1/admin/bis/import-simc', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        setStatus(`SimC import complete — ${d.items_upserted} slots imported.`, 'success');
        await loadMatrix();
    } catch (err) {
        setStatus('SimC import failed: ' + err.message, 'error');
    }
}
