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
let _cells = {};        // {spec_id: {source_id: {ht_key: {status, items_found, ...}}}}
let _htBySpec = {};     // {spec_id: [{id, name, slug}]}
let _logVisible = false;

// Source icon mapping — .svg placeholders; replace with real favicons when available
const SOURCE_ICONS = {
    wowhead:   '/static/img/sources/wowhead.svg',
    icy_veins: '/static/img/sources/icy-veins.svg',
    archon:    '/static/img/sources/archon.svg',
};

// Active drill-down target
let _drillSpecId = null;
let _drillSourceId = null;
let _drillHtId = null;

// Collapsible class groups — null means "not yet initialised; collapse all on first render"
let _collapsedClasses = null;

// Operation locks — prevent overlapping operations
let _syncInProgress      = false;
let _discoveryInProgress = false;

// ---------------------------------------------------------------------------
// Button state management
// ---------------------------------------------------------------------------

function _hasTargets() {
    // Returns true if at least one non-IV scrape target exists (any cell has data).
    // Used to gate sync buttons — sync does nothing useful before discover has run.
    for (const src of _sources) {
        if (src.origin === 'icy_veins') continue;
        for (const specId of Object.keys(_cells)) {
            if ((_cells[specId] || {})[src.id]) return true;
        }
    }
    return false;
}

function _updateButtonStates() {
    const busy      = _syncInProgress || _discoveryInProgress;
    const canSync   = !busy && _hasTargets();
    const canImport = !_syncInProgress;   // import ok during discover, not during sync

    const rules = {
        'discover-btn':     { disabled: busy,      title: busy ? 'Operation in progress — please wait.' : '' },
        'sync-source-btn':  { disabled: !canSync,  title: !_hasTargets() ? 'Run Discover URLs first.' : (busy ? 'Operation in progress — please wait.' : '') },
        'sync-all-btn':       { disabled: !canSync,  title: !_hasTargets() ? 'Run Discover URLs first.' : (busy ? 'Operation in progress — please wait.' : '') },
        'resync-errors-btn':  { disabled: !canSync,  title: !_hasTargets() ? 'Run Discover URLs first.' : (busy ? 'Operation in progress — please wait.' : '') },
        'sync-gaps-btn':      { disabled: !canSync,  title: !_hasTargets() ? 'Run Discover URLs first.' : (busy ? 'Operation in progress — please wait.' : '') },
        'import-simc-btn':    { disabled: !canImport, title: !canImport ? 'Sync in progress — please wait.' : '' },
    };

    for (const [id, state] of Object.entries(rules)) {
        const btn = document.getElementById(id);
        if (!btn) continue;
        btn.disabled = state.disabled;
        if (state.title) btn.title = state.title;
        else btn.removeAttribute('title');
    }
}

// ---------------------------------------------------------------------------
// Button running-state helpers
// ---------------------------------------------------------------------------

function _setBtnRunning(btn) {
    if (!btn) return;
    btn._originalHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner"></span> Running\u2026';
    btn.disabled = true;
}

function _setBtnDone(btn) {
    if (!btn) return;
    if (btn._originalHtml != null) {
        btn.innerHTML = btn._originalHtml;
        btn._originalHtml = null;
    }
    btn.disabled = false;
}

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
    const btn = document.getElementById('refresh-matrix-btn');
    _setBtnRunning(btn);
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
        _updateButtonStates();
        setStatus(`Matrix loaded — ${_specs.length} specs × ${_sources.length} sources.`);
    } catch (err) {
        setStatus('Error loading matrix: ' + err.message, 'error');
    } finally {
        _setBtnDone(btn);
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

    // Two-row header: row 1 = Spec (rowspan2) + website groups (colspan)
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

    // Website group headers (row 1)
    for (const origin of originOrder) {
        const srcs = originSources[origin];
        const th = document.createElement('th');
        th.colSpan = srcs.length;
        const label = _ORIGIN_LABELS[origin] || origin;
        th.textContent = label;
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

    // Flatten sources once (reused for every spec row)
    const orderedSources = [];
    for (const origin of originOrder) {
        for (const src of (originSources[origin] || [])) orderedSources.push(src);
    }

    // Initialise collapsed state once (all collapsed on first load)
    if (_collapsedClasses === null) {
        _collapsedClasses = new Set(_specs.map(sp => sp.class_name));
    }

    // Pre-compute G/Y/R counts per class (all specs are spec-level, no HT split)
    const classCountsMap = {};
    for (const sp of _specs) {
        const statuses = orderedSources.map(src => _cellStatus(sp.id, src.id, null));
        const c = _countStatuses(statuses);
        const acc = classCountsMap[sp.class_name] ||= { success: 0, partial: 0, failed: 0, total: 0 };
        acc.success += c.success; acc.partial += c.partial;
        acc.failed  += c.failed;  acc.total   += c.total;
    }

    for (const sp of _specs) {
        // Class group divider row
        if (sp.class_name !== lastClass) {
            lastClass = sp.class_name;
            const divRow = document.createElement('tr');
            divRow.className = 'gp-class-divider';
            divRow.setAttribute('data-class-row', sp.class_name);
            const divStyle = 'background:#111114; color:var(--color-text-muted); font-size:0.75rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em;';
            // Spec column: class name + toggle icon + G/Y/R counts
            const divTdName = document.createElement('td');
            divTdName.style.cssText = divStyle + ' padding:0.3rem 0.75rem;';
            const icon = document.createElement('span');
            icon.className = 'gp-class-toggle-icon';
            divTdName.appendChild(icon);
            divTdName.appendChild(document.createTextNode(sp.class_name + ' '));
            const cc = classCountsMap[sp.class_name] || { success: 0, partial: 0, failed: 0, total: 0 };
            for (const [count, color] of [[cc.success,'#4ade80'],[cc.partial,'#fbbf24'],[cc.failed,'#f87171']]) {
                const sp2 = document.createElement('span');
                sp2.className = 'gp-sum';
                sp2.style.cssText = `color:${color}; font-weight:400; letter-spacing:0; text-transform:none;`;
                sp2.textContent = count;
                divTdName.appendChild(sp2);
            }
            const divTot = document.createElement('span');
            divTot.className = 'gp-sum gp-sum--t';
            divTot.style.cssText = 'font-weight:400; letter-spacing:0; text-transform:none;';
            divTot.textContent = `/${cc.total}`;
            divTdName.appendChild(divTot);
            divRow.appendChild(divTdName);
            // Source columns: one empty td spanning the rest
            const divTdRest = document.createElement('td');
            divTdRest.colSpan = orderedSources.length;
            divTdRest.style.cssText = divStyle;
            divRow.appendChild(divTdRest);
            divRow.addEventListener('click', () => _toggleClass(sp.class_name));
            body.appendChild(divRow);
        }

        const row = document.createElement('tr');
        row.setAttribute('data-class', sp.class_name);
        const tdSpec = document.createElement('td');
        tdSpec.className = 'gp-td-spec';
        tdSpec.textContent = sp.spec_name;
        row.appendChild(tdSpec);
        orderedSources.forEach((src, idx) => {
            const td = document.createElement('td');
            if (idx === 0 || orderedSources[idx - 1].origin !== src.origin) td.style.borderLeft = '1px solid #333';
            td.appendChild(renderCell(sp.id, src.id));
            td.addEventListener('click', () => drillDown(sp.id, src.id));
            row.appendChild(td);
        });
        body.appendChild(row);
    }

    // Column summary footer row (always visible — no data-class attribute)
    const colSumRow = document.createElement('tr');
    colSumRow.className = 'gp-col-summary-row';
    const colLabel = document.createElement('td');
    colLabel.className = 'gp-col-summary-label';
    colLabel.textContent = 'Totals ';
    colSumRow.appendChild(colLabel);

    // All specs are spec-level (no HT split)
    const allRows = _specs.map(sp => ({ specId: sp.id, htId: null }));

    let grandCounts = { success: 0, partial: 0, failed: 0, total: 0 };
    orderedSources.forEach((src, idx) => {
        const source = _sources.find(s => s.id == src.id);
        let td;
        if (source && source.origin === 'icy_veins') {
            td = document.createElement('td');
            td.textContent = '—';
            td.style.color = '#444';
        } else {
            const colStatuses = allRows.map(r => _cellStatus(r.specId, src.id, r.htId));
            const counts = _countStatuses(colStatuses);
            grandCounts.success += counts.success;
            grandCounts.partial += counts.partial;
            grandCounts.failed  += counts.failed;
            grandCounts.total   += counts.total;
            td = _makeSummaryTd(counts);
        }
        if (idx === 0 || orderedSources[idx - 1].origin !== src.origin) td.style.borderLeft = '1px solid #333';
        colSumRow.appendChild(td);
    });

    // Append grand totals to the label cell
    for (const [count, color] of [[grandCounts.success,'#4ade80'],[grandCounts.partial,'#fbbf24'],[grandCounts.failed,'#f87171']]) {
        const sp = document.createElement('span');
        sp.className = 'gp-sum';
        sp.style.cssText = `color:${color}; font-weight:400; text-transform:none;`;
        sp.textContent = count;
        colLabel.appendChild(sp);
    }
    const grandTot = document.createElement('span');
    grandTot.className = 'gp-sum gp-sum--t';
    grandTot.style.cssText = 'font-weight:400; text-transform:none;';
    grandTot.textContent = `/${grandCounts.total}`;
    colLabel.appendChild(grandTot);

    body.appendChild(colSumRow);

    _applyCollapsedState(body);
}

// ---------------------------------------------------------------------------
// Summary helpers
// ---------------------------------------------------------------------------

// Returns 'success' | 'partial' | 'failed' | 'pending' | null (IV excluded)
function _cellStatus(specId, sourceId, htId) {
    const source = _sources.find(s => s.id == sourceId);
    if (!source || source.origin === 'icy_veins') return null;
    const htKey = htId != null ? String(htId) : 'null';
    const srcCells = (_cells[specId] || {})[sourceId] || {};
    const cellData = srcCells[htKey] ?? srcCells['null'];
    return cellData?.status || null;
}

function _countStatuses(statuses) {
    let s = 0, p = 0, f = 0, t = 0;
    for (const st of statuses) {
        if (st === null) continue;   // IV or no target
        t++;
        if (st === 'success') s++;
        else if (st === 'partial') p++;
        else if (st === 'failed') f++;
    }
    return { success: s, partial: p, failed: f, total: t };
}

function _makeSummaryTd(counts, tag = 'td') {
    const el = document.createElement(tag);
    el.className = 'gp-td-summary';
    if (counts.total === 0) {
        el.textContent = '—';
        el.style.color = '#444';
        return el;
    }
    const parts = [
        [counts.success, '#4ade80'],
        [counts.partial, '#fbbf24'],
        [counts.failed,  '#f87171'],
    ];
    for (const [count, color] of parts) {
        const sp = document.createElement('span');
        sp.className = 'gp-sum';
        sp.style.color = color;
        sp.textContent = count;
        el.appendChild(sp);
    }
    const tot = document.createElement('span');
    tot.className = 'gp-sum gp-sum--t';
    tot.textContent = `/${counts.total}`;
    el.appendChild(tot);
    return el;
}

function _toggleClass(className) {
    if (_collapsedClasses.has(className)) {
        _collapsedClasses.delete(className);
    } else {
        _collapsedClasses.add(className);
    }
    _applyCollapsedState(document.getElementById('gp-matrix-body'));
}

function _applyCollapsedState(body) {
    if (!body) return;
    for (const row of body.querySelectorAll('tr[data-class]')) {
        row.style.display = _collapsedClasses.has(row.getAttribute('data-class')) ? 'none' : '';
    }
    for (const row of body.querySelectorAll('tr[data-class-row]')) {
        const icon = row.querySelector('.gp-class-toggle-icon');
        if (icon) icon.textContent = _collapsedClasses.has(row.getAttribute('data-class-row')) ? '▶ ' : '▼ ';
    }
}

function renderCell(specId, sourceId, htId) {
    // _cells keyed by spec_id → source_id → ht_key (per-HT accuracy).
    // Fall back to "null" key for sources (Wowhead, IV) that use a shared
    // per-spec target (hero_talent_id=NULL) rather than per-HT targets.
    const htKey = htId != null ? String(htId) : 'null';
    const srcCells = (_cells[specId] || {})[sourceId] || {};
    const cellData = srcCells[htKey] ?? srcCells['null'];
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

    const titleParts = [];
    if (cellData.last_fetched) {
        titleParts.push(`Last synced: ${new Date(cellData.last_fetched).toLocaleString()}`);
    }
    if (cellData.source_updated_at) {
        titleParts.push(`Source updated: ${new Date(cellData.source_updated_at).toLocaleString()}`);
    }
    if (cellData.technique) {
        titleParts.push(cellData.technique);
    }
    if (titleParts.length) wrapper.title = titleParts.join(' • ');

    return wrapper;
}

function _techIcon(technique) {
    const icons = {
        json_embed:        '[JSON]',
        json_embed_archon: '[JSON]',
        wh_gatherer:       '[WH]',
        html_parse:        '[HTML]',
        simc:              '[SimC]',
        manual:            '[Manual]',
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
    ugg:       'u.gg',
    wowhead:   'Wowhead',
    icy_veins: 'Icy Veins',
    archon:    'Archon.gg',
    method:    'Method.gg',
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

        // Hide "Overall" plan type for sources that have no overall page (u.gg, archon)
        originSel.addEventListener('change', () => {
            const planTypeSel = document.getElementById('sync-plan-type-select');
            if (!planTypeSel) return;
            const noOverall = ['ugg', 'archon'].includes(originSel.value);
            for (const opt of planTypeSel.options) {
                if (opt.value === 'overall') {
                    opt.disabled = noOverall;
                    opt.style.display = noOverall ? 'none' : '';
                }
            }
            if (noOverall && planTypeSel.value === 'overall') planTypeSel.value = 'raid';
        });
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
    if (_syncInProgress || _discoveryInProgress) { setStatus('Operation in progress — please wait.', 'error'); return; }
    const btn = document.getElementById('discover-btn');
    _setBtnRunning(btn);
    _discoveryInProgress = true;
    _updateButtonStates();
    setStatusHtml('<span class="spinner"></span> Discovering targets…', 'running');
    try {
        const r = await fetch('/api/v1/admin/bis/targets/discover', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadMatrix();
        setStatus(`Discovery complete — ${d.inserted} targets added, ${d.skipped} already existed.`, 'success');
        if (_targetsVisible) loadTargets();
    } catch (err) {
        setStatus('Discovery failed: ' + err.message, 'error');
    } finally {
        _discoveryInProgress = false;
        _updateButtonStates();
        _setBtnDone(btn);
    }
}

async function syncSource() {
    if (_syncInProgress || _discoveryInProgress) { setStatus('Operation in progress — please wait.', 'error'); return; }

    const originSel   = document.getElementById('sync-origin-select');
    const planTypeSel = document.getElementById('sync-plan-type-select');

    const origin      = originSel?.value;
    const contentType = planTypeSel?.value;

    if (!origin) { setStatus('Select a website first.', 'error'); return; }
    if (!contentType) { setStatus('Select a plan type first.', 'error'); return; }

    const src = _sources.find(s => s.origin === origin && s.content_type === contentType);
    if (!src) {
        setStatus(`No source exists for ${_ORIGIN_LABELS[origin] || origin} + ${planTypeSel.options[planTypeSel.selectedIndex].text}.`, 'error');
        return;
    }

    const btn = document.getElementById('sync-source-btn');
    _setBtnRunning(btn);
    _syncInProgress = true;
    _updateButtonStates();

    // Per-spec loop: sync one spec at a time, show live progress
    const specs = _specs.filter(sp => true); // all specs
    let totalItems = 0, totalErrors = 0, processed = 0;

    try {
        for (const sp of specs) {
            setStatusHtml(
                `<span class="spinner"></span> Syncing ${src.name} — ${sp.spec_name} (${++processed}/${specs.length})…`,
                'running'
            );
            try {
                const r = await fetch(`/api/v1/admin/bis/sync/spec/${sp.id}`, { method: 'POST' });
                const d = await r.json();
                if (d.ok) {
                    totalItems  += d.items_found || 0;
                    totalErrors += d.errors || 0;
                } else {
                    totalErrors++;
                }
            } catch (_) { totalErrors++; }
        }
    } finally {
        _syncInProgress = false;
        _updateButtonStates();
        _setBtnDone(btn);
    }

    await loadMatrix();
    const msg = `${src.name} sync complete — ${totalItems} items found, ${totalErrors} errors.`;
    setStatus(msg, totalErrors > 0 ? 'error' : 'success');
    if (_targetsVisible) loadTargets();
}

async function syncAll() {
    if (_syncInProgress || _discoveryInProgress) { setStatus('Operation in progress — please wait.', 'error'); return; }
    if (!confirm('Run full BIS sync for all sources and all specs? This may take several minutes.')) return;

    // Per-spec loop: sync all non-IV sources for each spec in sequence
    const specs = _specs;
    if (!specs.length) { setStatus('Load matrix first.', 'error'); return; }

    const btn = document.getElementById('sync-all-btn');
    _setBtnRunning(btn);
    _syncInProgress = true;
    _updateButtonStates();

    let totalItems = 0, totalErrors = 0, processed = 0;

    try {
        for (const sp of specs) {
            setStatusHtml(
                `<span class="spinner"></span> Syncing all sources — ${sp.spec_name} (${++processed}/${specs.length})…`,
                'running'
            );
            try {
                const r = await fetch(`/api/v1/admin/bis/sync/spec/${sp.id}`, { method: 'POST' });
                const d = await r.json();
                if (d.ok) {
                    totalItems  += d.items_found || 0;
                    totalErrors += d.errors || 0;
                } else {
                    totalErrors++;
                }
            } catch (_) { totalErrors++; }
        }
    } finally {
        _syncInProgress = false;
        _updateButtonStates();
        _setBtnDone(btn);
    }

    await loadMatrix();
    const msg = `Full sync complete — ${totalItems} items found, ${totalErrors} errors.`;
    setStatus(msg, totalErrors > 0 ? 'error' : 'success');
    if (_targetsVisible) loadTargets();
}

async function syncGaps() {
    if (_syncInProgress || _discoveryInProgress) { setStatus('Operation in progress — please wait.', 'error'); return; }

    // Fetch all targets and filter client-side to gap-eligible ones
    // (missing raw data or last fetched > 7 days ago)
    let gapTargets;
    setStatusHtml('<span class="spinner"></span> Gap fill — identifying missing/stale targets…', 'running');
    try {
        const r = await fetch('/api/v1/admin/bis/targets');
        const d = await r.json();
        if (!d.ok) { setStatus('Could not load targets: ' + (d.error || 'unknown error'), 'error'); return; }
        const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
        gapTargets = (d.targets || []).filter(t => {
            if (!t.last_fetched) return true;
            return new Date(t.last_fetched).getTime() < cutoff;
        });
    } catch (err) {
        setStatus('Could not load targets: ' + err.message, 'error');
        return;
    }

    if (!gapTargets.length) { setStatus('No gap targets — all targets have fresh data.', 'success'); return; }

    const btn = document.getElementById('sync-gaps-btn');
    _setBtnRunning(btn);
    _syncInProgress = true;
    _updateButtonStates();

    let totalItems = 0, totalErrors = 0, processed = 0;

    try {
        for (const t of gapTargets) {
            const label = `${t.class_name} ${t.spec_name}${t.hero_talent_name ? ' ' + t.hero_talent_name : ''} — ${t.source_name} ${t.content_type}`;
            setStatusHtml(
                `<span class="spinner"></span> Gap fill — ${label} (${++processed}/${gapTargets.length})…`,
                'running'
            );
            try {
                const r = await fetch(`/api/v1/admin/bis/sync/target/${t.id}`, { method: 'POST' });
                const d = await r.json();
                if (d.ok) {
                    totalItems  += d.items_found || 0;
                    if (d.status === 'failed') totalErrors++;
                } else {
                    totalErrors++;
                }
            } catch (_) { totalErrors++; }
            await new Promise(res => setTimeout(res, 2000));
        }
    } finally {
        _syncInProgress = false;
        _updateButtonStates();
        _setBtnDone(btn);
    }

    await loadMatrix();
    const msg = `Gap fill complete — ${processed} targets, ${totalItems} items found, ${totalErrors} errors.`;
    setStatus(msg, totalErrors > 0 ? 'error' : 'success');
    if (_targetsVisible) loadTargets();
}

async function resyncErrors() {
    if (_syncInProgress || _discoveryInProgress) { setStatus('Operation in progress — please wait.', 'error'); return; }

    const btn = document.getElementById('resync-errors-btn');

    // Fetch all targets, filter to failed ones
    let failedTargets;
    try {
        const r = await fetch('/api/v1/admin/bis/targets');
        const d = await r.json();
        if (!d.ok) { setStatus('Could not load targets: ' + (d.error || 'unknown error'), 'error'); return; }
        failedTargets = (d.targets || []).filter(t => t.status === 'failed');
    } catch (err) {
        setStatus('Could not load targets: ' + err.message, 'error');
        return;
    }

    if (!failedTargets.length) { setStatus('No failed targets to re-sync.', 'success'); return; }
    if (!confirm(`Re-sync ${failedTargets.length} failed targets?`)) return;

    _setBtnRunning(btn);
    _syncInProgress = true;
    _updateButtonStates();

    let totalItems = 0, totalErrors = 0, processed = 0;

    try {
        for (const t of failedTargets) {
            setStatusHtml(
                `<span class="spinner"></span> Re-syncing errors — target ${t.id} (${++processed}/${failedTargets.length})…`,
                'running'
            );
            try {
                const r = await fetch(`/api/v1/admin/bis/sync/target/${t.id}`, { method: 'POST' });
                const d = await r.json();
                if (d.ok) {
                    totalItems  += d.items_found || 0;
                    if (d.status === 'failed') totalErrors++;
                } else {
                    totalErrors++;
                }
            } catch (_) { totalErrors++; }
            await new Promise(res => setTimeout(res, 2000));
        }
    } finally {
        _syncInProgress = false;
        _updateButtonStates();
        _setBtnDone(btn);
    }

    await loadMatrix();
    const msg = `Re-sync errors complete — ${totalItems} items found, ${totalErrors} still failing.`;
    setStatus(msg, totalErrors > 0 ? 'error' : 'success');
    if (_targetsVisible) loadTargets();
}

// ---------------------------------------------------------------------------
// Drill-down
// ---------------------------------------------------------------------------

async function drillDown(specId, sourceId) {
    _drillSpecId = specId;
    _drillSourceId = sourceId;
    _drillHtId = null;

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
        const url = `/api/v1/admin/bis/entries?source_id=${sourceId}&spec_id=${specId}`;
        const r = await fetch(url);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        renderDrillDown(d.entries || [], specId, sourceId);

        // Look up the spec-level cell (hero_talent_id=NULL)
        const _srcCells = (_cells[specId] || {})[sourceId] || {};
        const cellData = _srcCells['null'];
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

// Canonical slot order — mirrors quality_track.SLOT_ORDER (main_hand split in migration 0155)
const SLOT_ORDER = [
    'head', 'neck', 'shoulder', 'back', 'chest', 'wrist',
    'hands', 'waist', 'legs', 'feet',
    'ring_1', 'ring_2', 'trinket_1', 'trinket_2',
    'main_hand_2h', 'main_hand_1h', 'off_hand',
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
            link.className = 'gp-item-name';
            // item_name may be empty (stub) — Wowhead power.js will rename it
            link.textContent = e.item_name || `Item #${e.blizzard_item_id}`;
            itemEl.appendChild(link);
            const idSpan = document.createElement('span');
            idSpan.className = 'gp-item-id';
            idSpan.textContent = `#${e.blizzard_item_id}`;
            itemEl.appendChild(idSpan);
        } else {
            const miss = document.createElement('span');
            miss.className = 'gp-slot-row__missing';
            miss.textContent = '— missing —';
            itemEl.appendChild(miss);
        }
        row.appendChild(itemEl);
        slotsEl.appendChild(row);
    }

    // Let Wowhead script rename/tooltip the new links
    if (window.$WowheadPower) window.$WowheadPower.refreshLinks();
}

function _slotLabel(slot) {
    const labels = {
        head: 'Head', neck: 'Neck', shoulder: 'Shoulder', back: 'Back',
        chest: 'Chest', wrist: 'Wrist', hands: 'Hands', waist: 'Waist',
        legs: 'Legs', feet: 'Feet', ring_1: 'Ring 1', ring_2: 'Ring 2',
        trinket_1: 'Trinket 1', trinket_2: 'Trinket 2',
        main_hand_2h: 'Main Hand (2H)', main_hand_1h: 'Main Hand (1H)', off_hand: 'Off Hand',
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
            await drillDown(_drillSpecId, _drillSourceId, _drillHtId);
        }
    } catch (err) {
        setStatus('Re-sync failed: ' + err.message, 'error');
    }
}

async function resyncSingleTarget(targetId, tr, btn, statusTd, itemsTd) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';

    // Clear any previous inline result
    let resultTd = tr.querySelector('.target-inline-result');
    if (!resultTd) {
        resultTd = document.createElement('td');
        resultTd.className = 'target-inline-result';
        resultTd.style.cssText = 'font-size:0.75rem; padding-left:0.5rem;';
        tr.appendChild(resultTd);
    }
    resultTd.textContent = '';

    try {
        const r = await fetch(`/api/v1/admin/bis/sync/target/${targetId}`, { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        // Update status + items cells inline
        statusTd.className = `gp-log-status-${d.status || 'pending'}`;
        statusTd.textContent = d.status || 'pending';
        itemsTd.textContent = d.items_found || 0;

        // Show result summary next to button
        const trinketPart = d.trinkets_upserted != null
            ? ` · ${d.trinkets_upserted} trinket${d.trinkets_upserted !== 1 ? 's' : ''}`
            : '';
        resultTd.style.color = 'var(--color-success, #4ade80)';
        resultTd.textContent = `${d.items_upserted} BIS${trinketPart}`;

        btn.textContent = 'Sync';
        btn.disabled = false;
    } catch (err) {
        resultTd.style.color = 'var(--color-error, #f87171)';
        resultTd.textContent = err.message;
        btn.textContent = 'Sync';
        btn.disabled = false;
    }
}

// ---------------------------------------------------------------------------
// Cross-reference
// ---------------------------------------------------------------------------

async function loadXref() {
    const specId  = document.getElementById('xref-spec-select').value;
    const content = document.getElementById('gp-xref-content');

    if (!specId) {
        content.innerHTML = '<span style="color:var(--color-text-muted);">Select a spec above to compare sources.</span>';
        return;
    }

    content.innerHTML = '<span class="spinner"></span> Loading…';

    try {
        const url = `/api/v1/admin/bis/cross-reference?spec_id=${specId}`;
        const r = await fetch(url);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        renderXref(d.by_slot || {});
    } catch (err) {
        content.innerHTML = `<span style="color:#f87171;">Error: ${err.message}</span>`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const xrefSpecSel = document.getElementById('xref-spec-select');
    if (xrefSpecSel) xrefSpecSel.addEventListener('change', loadXref);
});

function renderXref(bySlot) {
    const content = document.getElementById('gp-xref-content');

    // Check if there's any data at all (new format: slot objects with .sources)
    const hasData = SLOT_ORDER.some(slot => (bySlot[slot]?.total_with_data || 0) > 0);
    if (!hasData) {
        content.innerHTML = '<span style="color:var(--color-text-muted);">No BIS data available for this spec. Run a sync first.</span>';
        return;
    }

    const activeSources = _sources;

    const table = document.createElement('table');
    table.className = 'gp-xref-table';
    table.style.tableLayout = 'fixed';

    // Header
    const thead = document.createElement('thead');
    const hRow = document.createElement('tr');
    const thSlot = document.createElement('th');
    thSlot.textContent = 'Slot';
    thSlot.style.width = '80px';
    hRow.appendChild(thSlot);
    for (const src of activeSources) {
        const th = document.createElement('th');
        th.textContent = src.short_label || src.name;
        th.style.cssText = 'min-width:140px;';
        hRow.appendChild(th);
    }
    const thAgree = document.createElement('th');
    thAgree.textContent = 'Consensus';
    thAgree.style.cssText = 'min-width:160px;';
    hRow.appendChild(thAgree);
    thead.appendChild(hRow);
    table.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    for (const slot of SLOT_ORDER) {
        const slotData = bySlot[slot] || { sources: [], total_with_data: 0, all_agree: false, agree_count: 0, consensus_blizzard_item_id: null, consensus_item_name: '' };
        const sources = slotData.sources || [];
        const entryBySrc = {};
        for (const e of sources) entryBySrc[e.source_id] = e;

        const row = document.createElement('tr');
        if (slotData.total_with_data > 0) {
            row.className = slotData.all_agree ? 'gp-xref-row--agree' : 'gp-xref-row--partial';
        }

        const slotTd = document.createElement('td');
        slotTd.className = 'gp-xref-slot-label';
        slotTd.textContent = _slotLabel(slot);
        row.appendChild(slotTd);

        for (const src of activeSources) {
            const td = document.createElement('td');
            const e = entryBySrc[src.id];
            if (e && e.blizzard_item_id) {
                td.className = e.agrees ? 'gp-xref-cell--match' : 'gp-xref-cell--mismatch';
                const link = document.createElement('a');
                link.href = `https://www.wowhead.com/item=${e.blizzard_item_id}`;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.className = 'gp-item-name';
                link.textContent = e.item_name || `Item #${e.blizzard_item_id}`;
                td.appendChild(link);
                const idSpan = document.createElement('span');
                idSpan.className = 'gp-item-id';
                idSpan.textContent = `#${e.blizzard_item_id}`;
                td.appendChild(idSpan);
            } else {
                td.className = 'gp-xref-cell--missing';
                td.textContent = '—';
            }
            row.appendChild(td);
        }

        // Agreement / consensus cell
        const agreeTd = document.createElement('td');
        const div = document.createElement('div');
        div.className = 'gp-xref-agreement';
        if (slotData.total_with_data === 0) {
            div.className += ' gp-xref-agreement--split';
            div.textContent = '— no data';
        } else if (slotData.all_agree) {
            div.className += ' gp-xref-agreement--unanimous';
            div.textContent = `Unanimous (${slotData.agree_count}/${slotData.total_with_data})`;
        } else if (slotData.consensus_blizzard_item_id) {
            const majority = slotData.agree_count > slotData.total_with_data / 2;
            div.className += majority ? ' gp-xref-agreement--partial' : ' gp-xref-agreement--split';
            const countSpan = document.createElement('span');
            countSpan.textContent = `${slotData.agree_count}/${slotData.total_with_data} agree — `;
            div.appendChild(countSpan);
            const link = document.createElement('a');
            link.href = `https://www.wowhead.com/item=${slotData.consensus_blizzard_item_id}`;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.style.color = 'inherit';
            link.className = 'gp-item-name';
            link.textContent = slotData.consensus_item_name || `Item #${slotData.consensus_blizzard_item_id}`;
            div.appendChild(link);
        } else {
            div.className += ' gp-xref-agreement--split';
            div.textContent = 'Split';
        }
        agreeTd.appendChild(div);
        row.appendChild(agreeTd);
        tbody.appendChild(row);
    }
    table.appendChild(tbody);

    content.innerHTML = '';
    content.appendChild(table);

    // Let Wowhead script rename/tooltip the new links
    if (window.$WowheadPower) window.$WowheadPower.refreshLinks();
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
    tbody.innerHTML = '<tr><td colspan="12" style="color:var(--color-text-muted);">Loading…</td></tr>';

    try {
        const r = await fetch('/api/v1/admin/bis/targets');
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        _allTargets = d.targets || [];
        _populateTargetsSourceFilter();
        _renderTargets();
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="12" style="color:#f87171;">Error: ${err.message}</td></tr>`;
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
    const showInactive = document.getElementById('targets-filter-inactive')?.checked || false;

    const filtered = _allTargets.filter(t => {
        if (!showInactive && t.is_active === false) return false;
        if (filterOrigin && t.origin !== filterOrigin) return false;
        if (filterStatus && t.status !== filterStatus) return false;
        return true;
    });

    if (countEl) countEl.textContent = `${filtered.length} / ${_allTargets.length} targets`;

    tbody.innerHTML = '';
    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="12" style="color:var(--color-text-muted); padding:1rem;">No targets match filter.</td></tr>';
        return;
    }

    for (const t of filtered) {
        const tr = document.createElement('tr');
        tr.dataset.targetId = t.id;
        if (t.is_active === false) tr.style.opacity = '0.45';

        const ts = t.last_fetched ? new Date(t.last_fetched).toLocaleDateString() : '—';
        const nextTs = t.next_check_at ? new Date(t.next_check_at).toLocaleDateString() : '—';
        const statusClass = `gp-log-status-${t.status || 'pending'}`;
        const isIV = t.origin === 'icy_veins';

        // Active toggle cell (GL only)
        const activeTd = document.createElement('td');
        activeTd.style.cssText = 'text-align:center;';
        if (window._isGl) {
            const tog = document.createElement('button');
            tog.className = 'btn-sm ' + (t.is_active !== false ? 'btn-secondary' : 'btn-secondary');
            tog.style.cssText = 'padding:0.1rem 0.45rem; font-size:0.75rem; min-width:2.4rem;';
            tog.textContent = t.is_active !== false ? '✓' : '✗';
            tog.title = t.is_active !== false ? 'Active — click to deactivate' : 'Inactive — click to activate';
            tog.onclick = () => toggleIsActive(t.id, t.is_active !== false, tog, tr);
            activeTd.appendChild(tog);
        } else {
            activeTd.textContent = t.is_active !== false ? '✓' : '✗';
        }
        tr.appendChild(activeTd);

        // Spec cell
        const specTd = document.createElement('td');
        specTd.textContent = `${t.class_name || ''} ${t.spec_name || ''}`;
        tr.appendChild(specTd);

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

        // Next Check cell
        const nextTd = document.createElement('td');
        nextTd.style.fontSize = '0.75rem';
        nextTd.textContent = nextTs;
        tr.appendChild(nextTd);

        // Interval cell
        const intTd = document.createElement('td');
        intTd.style.fontSize = '0.75rem';
        intTd.textContent = t.check_interval_days != null ? t.check_interval_days + 'd' : '—';
        tr.appendChild(intTd);

        // URL cell
        const urlTd = document.createElement('td');
        urlTd.style.maxWidth = '280px';
        _renderTargetUrlCell(urlTd, t);
        tr.appendChild(urlTd);

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
            syncBtn.onclick = () => resyncSingleTarget(t.id, tr, syncBtn, statusTd, itemsTd);
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

// ---------------------------------------------------------------------------
// Item Sources — Loot Tables
// ---------------------------------------------------------------------------

let _trinketRatingsVisible = false;

function toggleTrinketRatings() {
    _trinketRatingsVisible = !_trinketRatingsVisible;
    document.getElementById('gp-trinket-ratings-content').style.display =
        _trinketRatingsVisible ? 'block' : 'none';
    document.getElementById('trinket-ratings-toggle-icon').textContent =
        _trinketRatingsVisible ? '▲' : '▼';
    if (_trinketRatingsVisible) loadTrinketRatings();
}

async function loadTrinketRatings() {
    const tbody = document.getElementById('gp-trinket-ratings-body');
    const countEl = document.getElementById('trinket-ratings-count');
    tbody.innerHTML = '<tr><td colspan="4" style="color:var(--color-text-muted); padding:1rem;">Loading…</td></tr>';

    try {
        const r = await fetch('/api/v1/admin/bis/trinket-ratings-status');
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed to load trinket ratings');

        const rows = d.data || [];
        if (countEl) countEl.textContent = `${rows.length} spec/source combinations`;

        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="4" style="color:var(--color-text-muted); padding:1rem;">No trinket ratings found. Run Step 4 — Sync BIS Lists to populate.</td></tr>';
            return;
        }

        tbody.innerHTML = rows.map(r => {
            const countColor = r.rating_count > 0 ? 'var(--color-success, #4ade80)' : 'var(--color-text-muted)';
            const countText = r.rating_count > 0 ? `${r.rating_count} ratings` : 'No data';
            const lastSynced = r.last_updated
                ? new Date(r.last_updated).toLocaleString()
                : 'Never';
            return `<tr>
                <td>${r.spec_name} (${r.class_name})</td>
                <td>${r.source_name}</td>
                <td style="color:${countColor}; font-weight:500;">${countText}</td>
                <td style="color:var(--color-text-muted);">${lastSynced}</td>
            </tr>`;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" style="color:var(--color-error, #f87171); padding:1rem;">Error: ${err.message}</td></tr>`;
    }
}

let _itemSourcesVisible = false;

function toggleItemSources() {
    _itemSourcesVisible = !_itemSourcesVisible;
    document.getElementById('gp-item-sources-content').style.display =
        _itemSourcesVisible ? 'block' : 'none';
    document.getElementById('item-sources-toggle-icon').textContent =
        _itemSourcesVisible ? '▲' : '▼';
    if (_itemSourcesVisible) loadItemSources();
}

async function syncItemSources() {
    const btn = document.getElementById('sync-item-sources-btn');
    _setBtnRunning(btn);
    setStatusHtml('<span class="spinner"></span> Syncing loot tables from Blizzard Journal API…', 'running');

    try {
        const r = await fetch('/api/v1/admin/bis/sync-item-sources', { method: 'POST' });
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
            const text = await r.text();
            throw new Error(`HTTP ${r.status}: server returned non-JSON response. Check app logs.\n${text.slice(0, 200)}`);
        }
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || d.detail || 'Sync failed');

        const errCount = (d.errors || []).length;
        const enriched = d.items_enriched != null ? `, ${d.items_enriched} enriched` : '';
        const catalyst = d.catalyst_tier_items ? `, ${d.catalyst_tier_items} tier (Catalyst)` : '';
        const msg = `Loot table sync complete — ${d.expansion_name || 'expansion'}: ` +
            `${d.instances_synced} instances, ${d.encounters_synced} encounters, ` +
            `${d.items_upserted} items${enriched}${catalyst}` +
            (errCount ? ` (${errCount} errors)` : '');
        setStatus(msg, errCount ? 'partial' : 'success');

        if (errCount) {
            console.warn('Item source sync errors:', d.errors);
        }

        await loadItemSources();
    } catch (err) {
        setStatus('Loot table sync failed: ' + err.message, 'error');
    } finally {
        _setBtnDone(btn);
    }
}

async function syncLegacyDungeons() {
    const btn = document.getElementById('sync-legacy-dungeons-btn');
    _setBtnRunning(btn);
    setStatusHtml('<span class="spinner"></span> Starting legacy dungeon sync…', 'running');

    try {
        const r = await fetch('/api/v1/admin/bis/sync-legacy-dungeons', { method: 'POST' });
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
            const text = await r.text();
            throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
        }
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || d.detail || 'Failed to start');

        // Sync runs in background — show a manual refresh prompt.
        setStatusHtml(
            'Legacy dungeon sync running in background (several minutes). ' +
            '<a href="#" onclick="loadItemSources();return false;" ' +
            'style="color:var(--color-accent);">Refresh Item Sources</a> when done.',
            'info'
        );
    } catch (err) {
        setStatus('Legacy dungeon sync failed: ' + err.message, 'error');
    } finally {
        _setBtnDone(btn);
    }
}

let _craftedSyncPollInterval = null;

async function syncCraftedItems() {
    const btn = document.getElementById('sync-crafted-items-btn');
    if (_craftedSyncPollInterval) return;  // already polling
    _setBtnRunning(btn);
    setStatusHtml('<span class="spinner"></span> Starting crafted item discovery…', 'running');

    try {
        const r = await fetch('/api/v1/admin/bis/sync-crafted-items', { method: 'POST' });
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
            const text = await r.text();
            throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
        }
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || d.detail || 'Failed to start');

        _startCraftedSyncPoll(btn);
    } catch (err) {
        setStatus('Crafted item sync failed: ' + err.message, 'error');
        _setBtnDone(btn);
    }
}

function _startCraftedSyncPoll(btn) {
    _craftedSyncPollInterval = setInterval(async () => {
        try {
            const r = await fetch('/api/v1/admin/bis/sync-crafted-items');
            const d = await r.json();

            if (d.running) {
                const phase = d.phase_label || 'Running';
                const checked = d.phase_2b_checked || 0;
                setStatusHtml(
                    `<span class="spinner"></span> Crafted item discovery — ${phase}` +
                    (checked > 0 ? ` (${checked} recipes searched…)` : '') + '…',
                    'running'
                );
            } else if (d.finished_at) {
                clearInterval(_craftedSyncPollInterval);
                _craftedSyncPollInterval = null;
                _setBtnDone(btn);
                const linked = (d.phase_2a_linked || 0) + (d.phase_2b_linked || 0);
                const stubbed = (d.phase_2a_stubbed || 0) + (d.phase_2b_stubbed || 0);
                const errors = d.phase_2b_errors || 0;
                const errPart = errors > 0 ? `, ${errors} errors` : '';
                if (d.phase_label === 'Error') {
                    setStatus('Crafted item sync failed — check server logs.', 'error');
                } else {
                    setStatus(
                        `Crafted item sync complete — ${linked} items linked, ${stubbed} new stubs${errPart}. Run Enrich Items (Step 2) next.`,
                        errors > 0 ? 'partial' : 'success'
                    );
                }
            }
        } catch (_) { /* ignore transient poll errors */ }
    }, 2000);
}

async function loadItemSources() {
    const tbody = document.getElementById('gp-item-sources-body');
    tbody.innerHTML = '<tr><td colspan="7" style="color:var(--color-text-muted);">Loading…</td></tr>';

    const instance  = document.getElementById('item-sources-filter-instance').value;
    const type      = document.getElementById('item-sources-filter-type').value;
    const showJunk  = document.getElementById('item-sources-show-junk')?.checked || false;

    const params = new URLSearchParams();
    if (instance)  params.set('instance_name', instance);
    if (type)      params.set('instance_type', type);
    if (showJunk)  params.set('show_junk', 'true');
    params.set('limit', '500');

    try {
        const r = await fetch('/api/v1/admin/bis/item-sources?' + params.toString());
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        // Populate instance filter dropdown on first load
        _populateInstanceFilter(d.instances || []);

        renderItemSources(d.sources || [], showJunk, d.junk_hidden_count || 0);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" style="color:#f87171;">Error: ${err.message}</td></tr>`;
    }
}

function _populateInstanceFilter(instances) {
    const sel = document.getElementById('item-sources-filter-instance');
    const cur = sel.value;
    // Only rebuild if the list has changed (avoid losing current selection)
    const existing = [...sel.options].map(o => o.value).filter(v => v);
    if (JSON.stringify(existing) === JSON.stringify(instances)) return;

    sel.innerHTML = '<option value="">All instances</option>';
    for (const inst of instances) {
        const opt = document.createElement('option');
        opt.value = inst;
        opt.textContent = inst;
        if (inst === cur) opt.selected = true;
        sel.appendChild(opt);
    }
}

const _TRACK_COLORS = { C: '#0070dd', H: '#a335ee', M: '#ff8000', V: '#1eff00' };

function renderItemSources(rows, showJunk = false, junkHiddenCount = 0) {
    const tbody = document.getElementById('gp-item-sources-body');
    const countEl = document.getElementById('item-sources-count');
    tbody.innerHTML = '';

    const junkCount = rows.filter(r => r.is_suspected_junk).length;
    let countText = `${rows.length} source${rows.length !== 1 ? 's' : ''}`;
    if (!showJunk && junkHiddenCount > 0) {
        countText += ` — ${junkHiddenCount} junk hidden`;
    } else if (showJunk && junkCount > 0) {
        countText += ` — ${junkCount} junk shown`;
    }
    if (countEl) countEl.textContent = countText;

    // Base colspan: 6 columns + optional GL delete + optional junk badge
    const colspan = window._isGl ? 7 : 6;

    if (rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${colspan}" style="color:var(--color-text-muted); padding:1rem;">No item sources found. Run "Sync Loot Tables" to populate.</td></tr>`;
        return;
    }

    for (const row of rows) {
        const tr = document.createElement('tr');
        if (row.is_suspected_junk) {
            tr.classList.add('gp-junk-row');
        }

        const TYPE_LABELS = { raid: 'Raid', world_boss: 'World Boss', dungeon: 'Dungeon' };
        const typeLabel = TYPE_LABELS[row.instance_type] || row.instance_type || '—';
        const slotLabel = row.slot_type && row.slot_type !== 'other'
            ? row.slot_type.replace(/_/g, ' ')
            : '—';

        const icon = row.icon_url
            ? `<img src="${row.icon_url}" style="width:18px;height:18px;border-radius:2px;vertical-align:middle;margin-right:4px;" loading="lazy">`
            : '';

        const junkBadge = row.is_suspected_junk
            ? ` <span style="font-size:0.7rem; color:#f87171; border:1px solid #f87171; border-radius:3px; padding:0 3px;">junk</span>`
            : '';

        let deleteCell = '';
        if (window._isGl) {
            deleteCell = `<td><button class="btn-sm btn-danger"
                style="padding:0.1rem 0.4rem; font-size:0.75rem;"
                onclick="deleteItemSource(${row.id})">✕</button></td>`;
        }

        tr.innerHTML = `
            <td>${icon}<a href="https://www.wowhead.com/item=${row.blizzard_item_id}" target="_blank" rel="noopener" style="color:inherit;">${row.item_name || `Item #${row.blizzard_item_id}`}</a> <span style="color:var(--color-text-muted);font-size:0.75rem;">#${row.blizzard_item_id}</span>${junkBadge}</td>
            <td>${slotLabel}</td>
            <td>${row.encounter_name || '—'}</td>
            <td>${row.instance_name || '—'}</td>
            <td style="color:var(--color-text-muted);">${typeLabel}</td>
            <td style="color:var(--color-text-muted); font-size:0.8rem;">${row.instance_type || '—'}</td>
            ${deleteCell}
        `;
        tbody.appendChild(tr);
    }
}

async function deleteItemSource(sourceId) {
    if (!confirm('Remove this item source entry?')) return;
    try {
        const r = await fetch(`/api/v1/admin/bis/item-sources/${sourceId}`, { method: 'DELETE' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadItemSources();
    } catch (err) {
        setStatus('Delete failed: ' + err.message, 'error');
    }
}

let _enrichPollInterval = null;

async function enrichItems() {
    const btn = document.getElementById('enrich-items-btn');

    // If already polling, don't start a second job
    if (_enrichPollInterval) return;

    _setBtnRunning(btn);
    setStatusHtml('<span class="spinner"></span> Starting item enrichment…', 'running');

    try {
        const r = await fetch('/api/v1/admin/bis/enrich-items', { method: 'POST' });
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
            const text = await r.text();
            throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
        }
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        _startEnrichPoll(btn, d.total || 0);
    } catch (err) {
        setStatus('Enrich items failed: ' + err.message, 'error');
        _setBtnDone(btn);
    }
}

function _startEnrichPoll(btn, total) {
    _enrichPollInterval = setInterval(async () => {
        try {
            const r = await fetch('/api/v1/admin/bis/enrich-items');
            const d = await r.json();
            const t = d.total || total || '?';
            const phaseLabel = d.phase_label ? ` (${d.phase_label})` : '';

            if (d.running) {
                setStatusHtml(
                    `<span class="spinner"></span> Enriching items${phaseLabel} — ${d.enriched} / ${t} done…`,
                    'running'
                );
            } else if (d.finished_at) {
                clearInterval(_enrichPollInterval);
                _enrichPollInterval = null;
                _setBtnDone(btn);
                const errPart = d.error_count > 0 ? `, ${d.error_count} errors` : '';
                setStatus(
                    `Enrich complete — ${d.enriched} recipe links built${errPart}.`,
                    d.error_count > 0 ? 'partial' : 'success'
                );
            }
        } catch (_) { /* ignore transient poll errors */ }
    }, 2000);
}

async function processTierTokens() {
    const btn = document.getElementById('process-tier-tokens-btn');
    _setBtnRunning(btn);
    setStatusHtml('<span class="spinner"></span> Processing tier tokens…', 'running');
    try {
        const r = await fetch('/api/v1/admin/bis/process-tier-tokens', { method: 'POST' });
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
            const text = await r.text();
            throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
        }
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        const msg = `Tier tokens complete — ${d.tokens_processed} tokens detected, ` +
            `${d.junk_flagged} junk rows flagged, ${d.tokens_skipped_override} overrides skipped.`;
        setStatus(msg, 'success');

        const lastRunEl = document.getElementById('tier-tokens-last-run');
        if (lastRunEl) {
            const now = new Date().toLocaleTimeString();
            lastRunEl.textContent =
                `Last run: ${now} — ${d.tokens_processed} tokens detected, ` +
                `${d.junk_flagged} junk rows flagged, ${d.tokens_skipped_override} overrides skipped.`;
        }
        await loadItemSources();
    } catch (err) {
        setStatus('Process tier tokens failed: ' + err.message, 'error');
    } finally {
        _setBtnDone(btn);
    }
}

async function flagJunkSources() {
    const btn = document.getElementById('flag-junk-btn');
    _setBtnRunning(btn);
    setStatusHtml('<span class="spinner"></span> Flagging junk sources…', 'running');
    try {
        const r = await fetch('/api/v1/admin/bis/flag-junk-sources', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        setStatus(
            `Junk flagging complete — ${d.flagged_world_boss} world boss + ${d.flagged_tier_piece} tier piece = ${d.total_flagged} total flagged.`,
            'success'
        );
        await loadItemSources();
    } catch (err) {
        setStatus('Flag junk failed: ' + err.message, 'error');
    } finally {
        _setBtnDone(btn);
    }
}

async function bulkPopulatePlans() {
    const btn = document.getElementById('bulk-populate-btn');
    const result = document.getElementById('bulk-populate-result');
    _setBtnRunning(btn);
    if (result) result.textContent = '';
    setStatusHtml('<span class="spinner"></span> Populating all plans from Wowhead Overall…', 'running');
    try {
        const r = await fetch('/api/v1/admin/bis/bulk-populate-plans', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        const msg = `Done — ${d.data.characters_processed} characters processed, ${d.data.slots_populated} slots populated.`;
        setStatus(msg, 'success');
        if (result) result.textContent = msg;
    } catch (err) {
        setStatus('Bulk populate failed: ' + err.message, 'error');
        if (result) result.textContent = 'Error: ' + err.message;
    } finally {
        _setBtnDone(btn);
    }
}

async function rebuildEnrichment() {
    const btn = document.getElementById('rebuild-enrichment-btn');
    const result = document.getElementById('rebuild-enrichment-result');
    _setBtnRunning(btn);
    if (result) result.textContent = '';
    setStatusHtml('<span class="spinner"></span> Rebuilding enrichment tables…', 'running');
    try {
        const r = await fetch('/api/v1/admin/bis/rebuild-enrichment', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        const c = d.counts;
        const msg = `Enrichment rebuild complete — ${c.items} items (${c.raid} raid, ${c.dungeon} dungeon, ${c.world_boss} world boss, ${c.tier} tier, ${c.catalyst} catalyst, ${c.crafted} crafted, ${c.unclassified} unclassified), ${c.item_sources} sources, ${c.bis_entries} BIS entries, ${c.trinket_ratings} trinket ratings.`;
        setStatus(msg, 'success');
        if (result) result.textContent = msg;
    } catch (err) {
        setStatus('Rebuild enrichment failed: ' + err.message, 'error');
        if (result) result.textContent = 'Error: ' + err.message;
    } finally {
        _setBtnDone(btn);
    }
}

// ---------------------------------------------------------------------------
// Method.gg Section Inventory
// ---------------------------------------------------------------------------

function toggleMethodSections() {
    const content = document.getElementById('gp-method-sections-content');
    const icon = document.getElementById('method-sections-toggle-icon');
    const hidden = content.style.display === 'none';
    content.style.display = hidden ? 'block' : 'none';
    if (icon) icon.textContent = hidden ? '▲' : '▼';
    if (hidden) loadMethodSections();
}

async function loadMethodSections() {
    const outliersOnly = document.getElementById('method-outliers-only')?.checked ?? true;
    const tbody = document.getElementById('method-sections-body');
    const count = document.getElementById('method-sections-count');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="8" style="color:var(--color-text-muted);padding:1rem;">Loading…</td></tr>';

    try {
        const r = await fetch(`/api/v1/admin/bis/method-sections?outliers_only=${outliersOnly}&include_gaps=true`);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        const rows = d.data;
        const sectionRows = rows.filter(r => r.row_type === 'section');
        const gapRows = rows.filter(r => r.row_type === 'gap');
        if (count) count.textContent = `${sectionRows.length} outlier section${sectionRows.length !== 1 ? 's' : ''}, ${gapRows.length} coverage gap${gapRows.length !== 1 ? 's' : ''}`;

        if (!rows.length) {
            tbody.innerHTML = `<tr><td colspan="8" style="color:var(--color-text-muted);padding:1rem;">
                ${outliersOnly ? 'No issues — all Method.gg headings auto-classified and all content types covered.' : 'No sections found — run Gap Fill first.'}
            </td></tr>`;
            return;
        }

        const CT_LABELS = { overall: 'Overall', raid: 'Raid', mythic_plus: 'M+' };
        const ALL_CTS = ['overall', 'raid', 'mythic_plus'];

        const sectionHtml = sectionRows.map(s => {
            const inferred = s.inferred_content_type
                ? `<span style="color:var(--color-text-muted);">${CT_LABELS[s.inferred_content_type] || s.inferred_content_type}</span>`
                : '<span style="color:#f87171;">unknown</span>';

            const outlierBadge = s.is_outlier
                ? `<span style="color:#fbbf24; font-size:0.75rem;">${s.outlier_reason || 'outlier'}</span>`
                : '—';

            const overrideForCTs = s.override_for || [];
            const overrideLabel = overrideForCTs.length
                ? overrideForCTs.map(ct => `<span style="color:#4ade80;font-size:0.78rem;">${CT_LABELS[ct] || ct}</span>`).join(', ')
                : '';

            const ctOptions = ALL_CTS.map(ct =>
                `<option value="${ct}" ${overrideForCTs.includes(ct) ? 'selected' : ''}>${CT_LABELS[ct]}</option>`
            ).join('');

            const selectId = `method-override-${s.spec_id}-${s.table_index}`;
            const hasOverride = overrideForCTs.length > 0;

            return `<tr data-spec-id="${s.spec_id}" data-heading="${s.section_heading.replace(/"/g, '&quot;')}">
                <td>${s.class_name}</td>
                <td>${s.spec_name}</td>
                <td style="max-width:280px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${s.section_heading}">${s.section_heading}</td>
                <td>${s.row_count}</td>
                <td>${inferred}</td>
                <td style="font-size:0.78rem;">${outlierBadge}</td>
                <td>
                    ${overrideLabel ? overrideLabel + ' ' : ''}
                    <select id="${selectId}" class="gp-select" style="font-size:0.78rem; padding:2px 4px;">
                        <option value="">— set override —</option>
                        ${ctOptions}
                    </select>
                </td>
                <td style="white-space:nowrap;">
                    <button class="btn-sm btn-secondary" style="font-size:0.75rem;"
                        onclick="saveMethodOverride(${s.spec_id}, '${s.section_heading.replace(/'/g, "\\'")}', '${selectId}')">
                        Save
                    </button>
                    ${hasOverride ? `<button class="btn-sm" style="font-size:0.75rem; background:var(--color-danger,#7f1d1d); color:#fff; margin-left:4px;"
                        onclick="clearMethodOverride(${s.spec_id}, '${overrideForCTs[0]}')">
                        Clear
                    </button>` : ''}
                </td>
            </tr>`;
        }).join('');

        const gapHtml = gapRows.length ? [
            `<tr><td colspan="8" style="padding:0.5rem 0.6rem; background:rgba(96,165,250,0.07); font-size:0.75rem; color:var(--color-text-muted); text-transform:uppercase; letter-spacing:.05em;">Coverage Gaps — content types with no matching section</td></tr>`,
            ...gapRows.map(g => {
                const headingOptions = (g.available_headings || []).map(h =>
                    `<option value="${h.replace(/"/g, '&quot;')}">${h}</option>`
                ).join('');
                const selectId = `method-gap-${g.spec_id}-${g.content_type}`;
                return `<tr style="opacity:0.85;">
                    <td>${g.class_name}</td>
                    <td>${g.spec_name}</td>
                    <td colspan="3" style="color:#60a5fa; font-size:0.82rem;">
                        No section found for <strong>${CT_LABELS[g.content_type] || g.content_type}</strong>
                    </td>
                    <td style="font-size:0.78rem; color:#60a5fa;">missing</td>
                    <td>
                        <select id="${selectId}" class="gp-select" style="font-size:0.78rem; padding:2px 4px;">
                            <option value="">— map to heading —</option>
                            ${headingOptions}
                        </select>
                    </td>
                    <td>
                        <button class="btn-sm btn-secondary" style="font-size:0.75rem;"
                            onclick="saveMethodGapOverride(${g.spec_id}, '${g.content_type}', '${selectId}')">
                            Save
                        </button>
                    </td>
                </tr>`;
            })
        ].join('') : '';

        tbody.innerHTML = (sectionHtml || '') + (gapHtml || '');
        if (!tbody.innerHTML.trim()) {
            tbody.innerHTML = '<tr><td colspan="8" style="color:var(--color-text-muted);padding:1rem;">No issues found.</td></tr>';
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" style="color:#f87171;padding:1rem;">Error: ${err.message}</td></tr>`;
    }
}

async function saveMethodGapOverride(specId, contentType, selectId) {
    const sel = document.getElementById(selectId);
    if (!sel || !sel.value) { alert('Select a heading to map to.'); return; }
    try {
        const r = await fetch('/api/v1/admin/bis/method-sections/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ spec_id: specId, content_type: contentType, section_heading: sel.value }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadMethodSections();
    } catch (err) {
        alert('Save failed: ' + err.message);
    }
}

async function saveMethodOverride(specId, heading, selectId) {
    const sel = document.getElementById(selectId);
    if (!sel || !sel.value) { alert('Select a content type first.'); return; }
    try {
        const r = await fetch('/api/v1/admin/bis/method-sections/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ spec_id: specId, content_type: sel.value, section_heading: heading }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadMethodSections();
    } catch (err) {
        alert('Save failed: ' + err.message);
    }
}

async function reparseMethodSections() {
    const btn = document.getElementById('method-reparse-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Re-parsing…'; }
    try {
        const r = await fetch('/api/v1/admin/bis/method-sections/reparse', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadMethodSections();
        const count = document.getElementById('method-sections-count');
        if (count) count.textContent += ` (re-parsed ${d.specs_processed} specs, ${d.sections_upserted} sections)`;
    } catch (err) {
        alert('Re-parse failed: ' + err.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Re-parse Sections'; }
    }
}

async function clearMethodOverride(specId, contentType) {
    if (!confirm(`Clear override for spec ${specId} / ${contentType}?`)) return;
    try {
        const r = await fetch(
            `/api/v1/admin/bis/method-sections/override?spec_id=${specId}&content_type=${contentType}`,
            { method: 'DELETE' }
        );
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadMethodSections();
    } catch (err) {
        alert('Clear failed: ' + err.message);
    }
}

// ---------------------------------------------------------------------------
// Unified Section Inventory
// ---------------------------------------------------------------------------

let _siCurrentSource = 'icy_veins';

function toggleSectionInventory() {
    const content = document.getElementById('gp-section-inventory-content');
    const icon = document.getElementById('section-inventory-toggle-icon');
    const hidden = content.style.display === 'none';
    content.style.display = hidden ? 'block' : 'none';
    if (icon) icon.textContent = hidden ? '▲' : '▼';
    if (hidden) loadSectionInventory();
}

function switchSectionTab(source, btn) {
    _siCurrentSource = source;
    ['icy_veins', 'method'].forEach(s => {
        const b = document.getElementById('si-tab-' + s.replace('_', '-'));
        if (!b) return;
        b.className = 'btn-sm btn-secondary';
        b.style.background = '';
        b.style.color = '';
    });
    btn.className = 'btn-sm';
    btn.style.background = 'var(--color-accent)';
    btn.style.color = '#000';
    const reparseBtn = document.getElementById('si-reparse-btn');
    if (reparseBtn) reparseBtn.style.display = source === 'method' ? '' : 'none';
    loadSectionInventory();
}

async function loadSectionInventory() {
    const outliersOnly = document.getElementById('si-outliers-only')?.checked ?? true;
    const tbody = document.getElementById('si-body');
    const count = document.getElementById('si-count');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="8" style="color:var(--color-text-muted);padding:1rem;">Loading…</td></tr>';

    try {
        const r = await fetch(`/api/v1/admin/bis/page-sections?source=${_siCurrentSource}&outliers_only=${outliersOnly}&include_gaps=true`);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');

        const rows = d.data;
        const sectionRows = rows.filter(r => r.row_type === 'section');
        const gapRows = rows.filter(r => r.row_type === 'gap');
        if (count) count.textContent = `${sectionRows.length} section${sectionRows.length !== 1 ? 's' : ''}, ${gapRows.length} gap${gapRows.length !== 1 ? 's' : ''}`;

        if (!rows.length) {
            tbody.innerHTML = `<tr><td colspan="8" style="color:var(--color-text-muted);padding:1rem;">
                ${outliersOnly ? 'No issues — all sections classified and all content types covered.' : 'No sections found — run a BIS sync first.'}
            </td></tr>`;
            return;
        }

        const CT_LABELS = { overall: 'Overall', raid: 'Raid', mythic_plus: 'M+' };
        const ALL_CTS = ['overall', 'raid', 'mythic_plus'];
        const isIV = _siCurrentSource === 'icy_veins';

        const sectionHtml = sectionRows.map(s => {
            const mappings = s.override_mappings || [];
            const secondaryOf = s.secondary_of_mappings || [];

            // Content type: show auto-classified value; if overridden to a different
            // type, show "auto → override"; if used as secondary, show that too.
            const autoCtLabel = s.content_type
                ? (CT_LABELS[s.content_type] || s.content_type)
                : 'unknown';
            const overrideCt = mappings.length ? (CT_LABELS[mappings[0].content_type] || mappings[0].content_type) : null;
            const secondaryCt = secondaryOf.length ? (CT_LABELS[secondaryOf[0].content_type] || secondaryOf[0].content_type) : null;

            let ctLabel;
            if (overrideCt && overrideCt !== autoCtLabel) {
                ctLabel = `<span style="color:var(--color-text-muted);">${autoCtLabel}</span> <span style="color:#4ade80;font-size:0.8rem;">→ ${overrideCt}</span>`;
            } else if (secondaryCt) {
                ctLabel = `<span style="color:var(--color-text-muted);">${autoCtLabel}</span> <span style="color:var(--color-accent);font-size:0.78rem;">↗ merged into ${secondaryCt}</span>`;
            } else if (s.content_type) {
                ctLabel = `<span style="color:var(--color-text-muted);">${autoCtLabel}</span>`;
            } else {
                ctLabel = '<span style="color:#f87171;">unknown</span>';
            }

            const trinketBadge = (isIV && s.is_trinket_section)
                ? ' <span style="font-size:0.68rem;background:#7c3aed;color:#fff;border-radius:3px;padding:1px 4px;">trinket</span>'
                : '';

            const outlierBadge = s.is_outlier
                ? `<span style="color:#fbbf24;font-size:0.75rem;">${s.outlier_reason || 'outlier'}</span>`
                : (secondaryOf.length ? `<span style="color:var(--color-accent);font-size:0.75rem;">merge secondary</span>` : '—');

            const overrideLabel = mappings.length
                ? mappings.map(m => `<span style="color:#4ade80;font-size:0.78rem;">${CT_LABELS[m.content_type] || m.content_type}</span>`).join(', ')
                : '';
            const overrideForCTs = mappings.map(m => m.content_type);

            const ctOptions = ALL_CTS.map(ct =>
                `<option value="${ct}" ${overrideForCTs.includes(ct) ? 'selected' : ''}>${CT_LABELS[ct]}</option>`
            ).join('');

            const safeKey = s.section_key.replace(/[^a-z0-9]/gi, '_');
            const selectId = `si-override-${s.spec_id}-${s.source_id}-${safeKey}`;
            const hasOverride = mappings.length > 0;
            const titleDisplay = isIV ? (s.section_title || s.section_key) : s.section_key;

            // Merge config — only shown when an override exists
            const rowId = `${s.spec_id}-${safeKey}`;
            const mergeOverride = hasOverride ? mappings[0] : null;
            const hasMerge = !!(mergeOverride?.secondary_section_key);

            let mergeRowHtml = '';
            if (hasOverride) {
                const curSecKey = mergeOverride.secondary_section_key || '';
                const curPNote = (mergeOverride.primary_note || '').replace(/"/g, '&quot;');
                const curMNote = (mergeOverride.match_note || '').replace(/"/g, '&quot;');
                const curSNote = (mergeOverride.secondary_note || '').replace(/"/g, '&quot;');
                const mergeContentType = mergeOverride.content_type;
                const mergeSectionKey = mergeOverride.section_key || s.section_key;

                const specSectionOpts = (s.spec_sections || [])
                    .filter(sec => sec.section_key !== s.section_key)
                    .map(sec => {
                        const sk = sec.section_key.replace(/"/g, '&quot;');
                        const label = (sec.section_title || sec.section_key).replace(/"/g, '&quot;');
                        const selected = sec.section_key === curSecKey ? ' selected' : '';
                        return `<option value="${sk}"${selected}>${label} (${sec.row_count})</option>`;
                    }).join('');

                mergeRowHtml = `<tr id="si-merge-${rowId}" style="display:none;">
                    <td colspan="8" style="padding:0;">
                        <div style="padding:0.7rem 1.2rem 0.8rem;background:rgba(212,168,75,0.05);border-left:3px solid var(--color-accent);border-bottom:1px solid var(--color-border);">
                            <div style="font-size:0.78rem;font-weight:600;color:var(--color-accent);margin-bottom:0.5rem;">Merge Configuration</div>
                            <div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:flex-end;">
                                <label style="font-size:0.75rem;color:var(--color-text-muted);">Secondary section
                                    <select id="si-sec-${rowId}" class="gp-select" style="display:block;margin-top:3px;min-width:180px;font-size:0.78rem;">
                                        <option value="">— none (basic override) —</option>
                                        ${specSectionOpts}
                                    </select>
                                </label>
                                <label style="font-size:0.75rem;color:var(--color-text-muted);">Primary note
                                    <input id="si-pnote-${rowId}" type="text" value="${curPNote}" placeholder="e.g. Deathbringer build"
                                        style="display:block;margin-top:3px;background:#1a1a1e;border:1px solid var(--color-border);border-radius:4px;color:var(--color-text);padding:0.28rem 0.45rem;font-size:0.78rem;width:155px;">
                                </label>
                                <label style="font-size:0.75rem;color:var(--color-text-muted);">Match note
                                    <input id="si-mnote-${rowId}" type="text" value="${curMNote}" placeholder="optional"
                                        style="display:block;margin-top:3px;background:#1a1a1e;border:1px solid var(--color-border);border-radius:4px;color:var(--color-text);padding:0.28rem 0.45rem;font-size:0.78rem;width:155px;">
                                </label>
                                <label style="font-size:0.75rem;color:var(--color-text-muted);">Secondary note
                                    <input id="si-snote-${rowId}" type="text" value="${curSNote}" placeholder="e.g. San'layn build"
                                        style="display:block;margin-top:3px;background:#1a1a1e;border:1px solid var(--color-border);border-radius:4px;color:var(--color-text);padding:0.28rem 0.45rem;font-size:0.78rem;width:155px;">
                                </label>
                                <button class="btn-sm" style="background:var(--color-accent);color:#000;font-size:0.75rem;"
                                    onclick="saveMergeConfig(${s.spec_id}, ${s.source_id}, '${mergeContentType}', '${mergeSectionKey.replace(/'/g, "\\'")}', '${rowId}')">
                                    Save Merge
                                </button>
                            </div>
                            <p style="font-size:0.72rem;color:var(--color-text-muted);margin:0.45rem 0 0;">
                                Setting a secondary section merges that section's items into this content type during Enrich &amp; Classify.
                                Items unique to the secondary build receive the secondary note; items in both receive the match note.
                            </p>
                        </div>
                    </td>
                </tr>`;
            }

            const mainRow = `<tr>
                <td>${s.class_name}</td>
                <td>${s.spec_name}</td>
                <td style="max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${s.section_title || s.section_key}">${titleDisplay}</td>
                <td>${s.row_count}</td>
                <td>${ctLabel}${trinketBadge}</td>
                <td style="font-size:0.78rem;">${outlierBadge}</td>
                <td>
                    ${overrideLabel ? overrideLabel + ' ' : ''}
                    <select id="${selectId}" class="gp-select" style="font-size:0.78rem;padding:2px 4px;">
                        <option value="">— set override —</option>
                        ${ctOptions}
                    </select>
                </td>
                <td style="white-space:nowrap;">
                    <button class="btn-sm btn-secondary" style="font-size:0.75rem;"
                        onclick="saveSectionOverride(${s.spec_id}, ${s.source_id}, '${s.section_key.replace(/'/g, "\\'")}', '${selectId}')">
                        Save
                    </button>
                    ${hasOverride ? `<button class="btn-sm btn-secondary" style="font-size:0.75rem;margin-left:4px;${hasMerge ? 'background:rgba(212,168,75,0.2);border-color:var(--color-accent);color:var(--color-accent);' : ''}" title="Configure merge"
                        onclick="toggleMergeRow('${rowId}')">
                        ${hasMerge ? 'Merge ✓' : 'Merge'}
                    </button>` : ''}
                    ${hasOverride ? `<button class="btn-sm" style="font-size:0.75rem;background:var(--color-danger,#7f1d1d);color:#fff;margin-left:4px;"
                        onclick="clearSectionOverride(${s.spec_id}, ${s.source_id}, '${mappings[0].content_type}')">
                        Clear
                    </button>` : ''}
                </td>
            </tr>`;

            return mainRow + mergeRowHtml;
        }).join('');

        const gapHtml = gapRows.length ? [
            `<tr><td colspan="8" style="padding:0.5rem 0.6rem;background:rgba(96,165,250,0.07);font-size:0.75rem;color:var(--color-text-muted);text-transform:uppercase;letter-spacing:.05em;">Coverage Gaps — content types with no matching section</td></tr>`,
            ...gapRows.map(g => {
                const sectionOptions = (g.available_sections || []).map(sec =>
                    `<option value="${sec.section_key.replace(/"/g, '&quot;')}">${sec.section_title || sec.section_key} (${sec.row_count} rows)</option>`
                ).join('');
                const selectId = `si-gap-${g.spec_id}-${g.source_id}-${g.content_type}`;
                return `<tr style="opacity:0.85;">
                    <td>${g.class_name}</td>
                    <td>${g.spec_name}</td>
                    <td colspan="2" style="color:#60a5fa;font-size:0.82rem;">
                        ${g.source_name} — no <strong>${CT_LABELS[g.content_type] || g.content_type}</strong> section
                    </td>
                    <td style="color:#60a5fa;font-size:0.82rem;">missing</td>
                    <td style="font-size:0.78rem;color:#60a5fa;">coverage gap</td>
                    <td>
                        <select id="${selectId}" class="gp-select" style="font-size:0.78rem;padding:2px 4px;">
                            <option value="">— map to section —</option>
                            ${sectionOptions}
                        </select>
                    </td>
                    <td>
                        <button class="btn-sm btn-secondary" style="font-size:0.75rem;"
                            onclick="saveGapOverride(${g.spec_id}, ${g.source_id}, '${g.content_type}', '${selectId}')">
                            Save
                        </button>
                    </td>
                </tr>`;
            })
        ].join('') : '';

        tbody.innerHTML = (sectionHtml || '') + (gapHtml || '');
        if (!tbody.innerHTML.trim()) {
            tbody.innerHTML = '<tr><td colspan="8" style="color:var(--color-text-muted);padding:1rem;">No issues found.</td></tr>';
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" style="color:#f87171;padding:1rem;">Error: ${err.message}</td></tr>`;
    }
}

async function saveSectionOverride(specId, sourceId, sectionKey, selectId) {
    const sel = document.getElementById(selectId);
    if (!sel || !sel.value) { alert('Select a content type first.'); return; }
    try {
        const r = await fetch('/api/v1/admin/bis/page-sections/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ spec_id: specId, source_id: sourceId, content_type: sel.value, section_key: sectionKey }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadSectionInventory();
    } catch (err) {
        alert('Save failed: ' + err.message);
    }
}

async function saveGapOverride(specId, sourceId, contentType, selectId) {
    const sel = document.getElementById(selectId);
    if (!sel || !sel.value) { alert('Select a section to map to.'); return; }
    try {
        const r = await fetch('/api/v1/admin/bis/page-sections/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ spec_id: specId, source_id: sourceId, content_type: contentType, section_key: sel.value }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadSectionInventory();
    } catch (err) {
        alert('Save failed: ' + err.message);
    }
}

async function clearSectionOverride(specId, sourceId, contentType) {
    if (!confirm(`Clear override for spec ${specId} / ${contentType}?`)) return;
    try {
        const r = await fetch(
            `/api/v1/admin/bis/page-sections/override?spec_id=${specId}&source_id=${sourceId}&content_type=${contentType}`,
            { method: 'DELETE' }
        );
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadSectionInventory();
    } catch (err) {
        alert('Clear failed: ' + err.message);
    }
}

function toggleMergeRow(rowId) {
    const row = document.getElementById('si-merge-' + rowId);
    if (!row) return;
    row.style.display = row.style.display === 'none' ? '' : 'none';
}

async function saveMergeConfig(specId, sourceId, contentType, sectionKey, rowId) {
    const secSel  = document.getElementById('si-sec-'   + rowId);
    const pNote   = document.getElementById('si-pnote-' + rowId);
    const mNote   = document.getElementById('si-mnote-' + rowId);
    const sNote   = document.getElementById('si-snote-' + rowId);

    const body = {
        spec_id:               specId,
        source_id:             sourceId,
        content_type:          contentType,
        section_key:           sectionKey,
        secondary_section_key: secSel?.value  || null,
        primary_note:          pNote?.value?.trim()  || null,
        match_note:            mNote?.value?.trim()  || null,
        secondary_note:        sNote?.value?.trim()  || null,
    };

    try {
        const r = await fetch('/api/v1/admin/bis/page-sections/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadSectionInventory();
    } catch (err) {
        alert('Save merge failed: ' + err.message);
    }
}

async function reparseSections() {
    const btn = document.getElementById('si-reparse-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Re-parsing…'; }
    try {
        const r = await fetch('/api/v1/admin/bis/method-sections/reparse', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        await loadSectionInventory();
        const count = document.getElementById('si-count');
        if (count) count.textContent += ` (re-parsed ${d.specs_processed} specs, ${d.sections_upserted} sections)`;
    } catch (err) {
        alert('Re-parse failed: ' + err.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Re-parse Sections'; }
    }
}

// ---------------------------------------------------------------------------
// Daily Run History  (Phase 1.7-F)
// ---------------------------------------------------------------------------

let _dailyRunsVisible = false;

function toggleDailyRuns() {
    _dailyRunsVisible = !_dailyRunsVisible;
    document.getElementById('gp-daily-runs-content').style.display = _dailyRunsVisible ? 'block' : 'none';
    document.getElementById('daily-runs-toggle-icon').textContent = _dailyRunsVisible ? '▲' : '▼';
    if (_dailyRunsVisible) loadDailyRuns();
}

async function loadDailyRuns() {
    const tbody = document.getElementById('gp-daily-runs-body');
    const countEl = document.getElementById('daily-runs-count');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" style="color:var(--color-text-muted);">Loading…</td></tr>';
    try {
        const r = await fetch('/api/v1/admin/bis/daily-runs?limit=10');
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        const runs = d.runs || [];
        if (countEl) countEl.textContent = runs.length + ' runs';
        if (runs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="color:var(--color-text-muted); padding:1rem;">No runs recorded yet.</td></tr>';
            return;
        }
        tbody.innerHTML = '';
        for (const run of runs) {
            _appendDailyRunRow(tbody, run);
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" style="color:#f87171;">Error: ${err.message}</td></tr>`;
    }
}

function _appendDailyRunRow(tbody, run) {
    const runTime = run.run_at ? new Date(run.run_at).toLocaleString() : '—';
    const duration = run.duration_seconds != null ? run.duration_seconds.toFixed(1) + 's' : '—';
    const emailSent = run.email_sent_at ? '✓' : '—';
    const triggeredBy = run.triggered_by || 'scheduled';
    const changed = run.targets_changed || 0;
    const failed = run.targets_failed || 0;
    const targetsText = `${run.targets_checked || 0} checked, ${changed} changed` + (failed ? `, ${failed} failed` : '');
    const bisBefore = run.bis_entries_before || 0;
    const bisAfter = run.bis_entries_after || 0;
    const bisText = bisBefore === bisAfter ? String(bisAfter) : `${bisBefore} → ${bisAfter}`;
    const addedCount = Array.isArray(run.delta_added) ? run.delta_added.length : 0;
    const removedCount = Array.isArray(run.delta_removed) ? run.delta_removed.length : 0;
    const deltaText = addedCount || removedCount ? `+${addedCount}/-${removedCount}` : '—';
    const patchBadge = run.patch_signal ? ' <span title="Patch signal detected" style="color:#fbbf24;">⚡</span>' : '';
    const notes = run.notes || '';

    // Main row
    const tr = document.createElement('tr');
    tr.style.cursor = (addedCount || removedCount) ? 'pointer' : '';
    tr.innerHTML = `
        <td style="font-size:0.78rem;">${runTime}${patchBadge}</td>
        <td style="font-size:0.78rem;">${triggeredBy}</td>
        <td style="font-size:0.78rem;">${targetsText}</td>
        <td style="font-size:0.78rem;">${bisText} <span style="color:var(--color-text-muted);">(${deltaText})</span></td>
        <td style="font-size:0.78rem;">${duration}</td>
        <td style="font-size:0.78rem; text-align:center;">${emailSent}</td>
        <td style="font-size:0.78rem; color:var(--color-text-muted); max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${notes}">${notes || '—'}</td>
    `;
    tbody.appendChild(tr);

    // Expandable delta detail row
    if (addedCount || removedCount) {
        const detailTr = document.createElement('tr');
        detailTr.style.display = 'none';
        detailTr.style.background = 'rgba(255,255,255,0.03)';

        let detailHtml = '<td colspan="7" style="padding:0.5rem 1rem 0.75rem; font-size:0.78rem;">';
        if (addedCount) {
            detailHtml += '<strong style="color:#4ade80;">Added (' + addedCount + '):</strong><br>';
            for (const item of run.delta_added) {
                detailHtml += `&nbsp;&nbsp;${item.spec_name || ''} / ${item.source_name || ''} / ${item.slot || ''}: ${item.item_name || item.blizzard_item_id}<br>`;
            }
        }
        if (removedCount) {
            if (addedCount) detailHtml += '<br>';
            detailHtml += '<strong style="color:#f87171;">Removed (' + removedCount + '):</strong><br>';
            for (const item of run.delta_removed) {
                detailHtml += `&nbsp;&nbsp;${item.spec_name || ''} / ${item.source_name || ''} / ${item.slot || ''}: ${item.item_name || item.blizzard_item_id}<br>`;
            }
        }
        detailHtml += '</td>';
        detailTr.innerHTML = detailHtml;
        tbody.appendChild(detailTr);

        tr.addEventListener('click', () => {
            detailTr.style.display = detailTr.style.display === 'none' ? 'table-row' : 'none';
        });
    }
}

// ---------------------------------------------------------------------------
// Patch Signal  (Phase 1.7-F)
// ---------------------------------------------------------------------------

async function loadPatchSignal() {
    try {
        const r = await fetch('/api/v1/admin/bis/patch-signal');
        const d = await r.json();
        if (!d.ok) return;
        const dot = document.getElementById('patch-signal-dot');
        const label = document.getElementById('patch-signal-label');
        if (!dot || !label) return;
        if (d.monitoring) {
            dot.style.background = '#4ade80';
            label.textContent = 'Monitoring';
            label.title = 'Post-patch mode: guide targets at 1-day interval';
        } else {
            dot.style.background = '#6b7280';
            const baseline = d.encounter_baseline != null ? ` (${d.encounter_baseline} encounters)` : '';
            label.textContent = 'Quiet' + baseline;
            label.title = d.last_probe_at ? 'Last probed: ' + new Date(d.last_probe_at).toLocaleString() : 'No probe data yet';
        }
    } catch (_) { /* non-critical */ }
}

// ---------------------------------------------------------------------------
// is_active toggle  (Phase 1.7-F)
// ---------------------------------------------------------------------------

async function toggleIsActive(targetId, currentlyActive, btn, tr) {
    const newActive = !currentlyActive;
    btn.disabled = true;
    try {
        const r = await fetch(`/api/v1/admin/bis/targets/${targetId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: newActive }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        // Update local cache
        const target = _allTargets.find(t => t.id === targetId);
        if (target) target.is_active = newActive;
        // Update row visual state
        btn.textContent = newActive ? '✓' : '✗';
        btn.title = newActive ? 'Active — click to deactivate' : 'Inactive — click to activate';
        btn.onclick = () => toggleIsActive(targetId, newActive, btn, tr);
        if (tr) tr.style.opacity = newActive ? '' : '0.45';
    } catch (err) {
        alert('Toggle failed: ' + err.message);
    } finally {
        btn.disabled = false;
    }
}

async function reactivateAll() {
    const btn = document.getElementById('reactivate-all-btn');
    if (!confirm('Re-activate all targets and reset next_check_at to NOW?')) return;
    if (btn) { btn.disabled = true; btn.textContent = 'Working…'; }
    try {
        const r = await fetch('/api/v1/admin/bis/targets/reactivate-all', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Failed');
        setStatus(`Re-activated ${d.updated} targets.`, 'success');
        await loadTargets();
    } catch (err) {
        setStatus('Re-activate failed: ' + err.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Re-activate All'; }
    }
}
