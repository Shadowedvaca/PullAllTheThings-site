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
        _updateButtonStates();
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
        const label = _ORIGIN_LABELS[origin] || origin;
        th.textContent = origin === 'icy_veins' ? label + ' — Coming Soon' : label;
        if (origin === 'icy_veins') {
            th.title = 'Auto-extraction not yet implemented — see reference/PHASE_Z_ICY_VEINS_SCRAPE-idea-only.md';
            th.style.cssText = 'text-align:center; border-left:1px solid #333; color:var(--color-text-muted);';
        } else {
            th.style.cssText = 'text-align:center; border-left:1px solid #333;';
        }
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

    // Pre-compute G/Y/R counts per class (across all specs, HTs, non-IV sources)
    const classCountsMap = {};
    for (const sp of _specs) {
        const hts = _htBySpec[sp.id] || [];
        const rows = hts.length > 0
            ? hts.map(ht => ({ specId: sp.id, htId: ht.id }))
            : [{ specId: sp.id, htId: null }];
        const statuses = rows.flatMap(r => orderedSources.map(src => _cellStatus(r.specId, src.id, r.htId)));
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
            // Spec column: class name + toggle icon
            const divTdName = document.createElement('td');
            divTdName.style.cssText = divStyle + ' padding:0.3rem 0.75rem;';
            const icon = document.createElement('span');
            icon.className = 'gp-class-toggle-icon';
            divTdName.appendChild(icon);
            divTdName.appendChild(document.createTextNode(sp.class_name));
            divRow.appendChild(divTdName);
            // Hero Talent column: G/Y/R counts
            const cc = classCountsMap[sp.class_name] || { success: 0, partial: 0, failed: 0, total: 0 };
            const divTdCounts = document.createElement('td');
            divTdCounts.style.cssText = divStyle + ' padding:0.3rem 0.5rem; font-weight:400; letter-spacing:0; text-transform:none;';
            for (const [count, color] of [[cc.success,'#4ade80'],[cc.partial,'#fbbf24'],[cc.failed,'#f87171']]) {
                const sp2 = document.createElement('span');
                sp2.className = 'gp-sum';
                sp2.style.color = color;
                sp2.textContent = count;
                divTdCounts.appendChild(sp2);
            }
            const divTot = document.createElement('span');
            divTot.className = 'gp-sum gp-sum--t';
            divTot.textContent = `/${cc.total}`;
            divTdCounts.appendChild(divTot);
            divRow.appendChild(divTdCounts);
            // Source columns: one empty td spanning the rest
            const divTdRest = document.createElement('td');
            divTdRest.colSpan = orderedSources.length;
            divTdRest.style.cssText = divStyle;
            divRow.appendChild(divTdRest);
            divRow.addEventListener('click', () => _toggleClass(sp.class_name));
            body.appendChild(divRow);
        }

        const htOptions = (_htBySpec[sp.id] || []);

        if (htOptions.length === 0) {
            const row = document.createElement('tr');
            row.setAttribute('data-class', sp.class_name);
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
                if (idx === 0 || orderedSources[idx - 1].origin !== src.origin) td.style.borderLeft = '1px solid #333';
                td.appendChild(renderCell(sp.id, src.id));
                td.addEventListener('click', () => drillDown(sp.id, src.id));
                row.appendChild(td);
            });
            body.appendChild(row);
        } else {
            htOptions.forEach((ht, idx) => {
                const row = document.createElement('tr');
                row.setAttribute('data-class', sp.class_name);
                if (idx === 0) {
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
                    if (srcIdx === 0 || orderedSources[srcIdx - 1].origin !== src.origin) td.style.borderLeft = '1px solid #333';
                    td.appendChild(renderCell(sp.id, src.id, ht.id));
                    td.addEventListener('click', () => drillDown(sp.id, src.id, ht.id));
                    row.appendChild(td);
                });
                body.appendChild(row);
            });
        }
    }

    // Column summary footer row (always visible — no data-class attribute)
    const colSumRow = document.createElement('tr');
    colSumRow.className = 'gp-col-summary-row';
    // Spec column: label text
    const colLabel = document.createElement('td');
    colLabel.className = 'gp-col-summary-label';
    colLabel.textContent = 'Column totals';
    colSumRow.appendChild(colLabel);
    // Hero Talent column: grand total counts (filled in after iterating sources)
    const grandTd = document.createElement('td');
    grandTd.className = 'gp-col-summary-label';
    grandTd.style.textTransform = 'none';
    grandTd.style.fontWeight = '400';
    colSumRow.appendChild(grandTd);

    // Collect all spec/HT combinations for column counting
    const allRows = [];
    for (const sp of _specs) {
        const hts = _htBySpec[sp.id] || [];
        if (hts.length === 0) {
            allRows.push({ specId: sp.id, htId: null });
        } else {
            for (const ht of hts) allRows.push({ specId: sp.id, htId: ht.id });
        }
    }

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

    // Populate grand total into the HT column td
    for (const [count, color] of [[grandCounts.success,'#4ade80'],[grandCounts.partial,'#fbbf24'],[grandCounts.failed,'#f87171']]) {
        const sp = document.createElement('span');
        sp.className = 'gp-sum';
        sp.style.color = color;
        sp.textContent = count;
        grandTd.appendChild(sp);
    }
    const grandTot = document.createElement('span');
    grandTot.className = 'gp-sum gp-sum--t';
    grandTot.textContent = `/${grandCounts.total}`;
    grandTd.appendChild(grandTot);

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
    // Icy Veins extraction is stubbed — show Coming Soon placeholder regardless of target status
    const source = _sources.find(s => s.id == sourceId);
    if (source && source.origin === 'icy_veins') {
        const wrapper = document.createElement('span');
        wrapper.className = 'gp-cell gp-cell--empty';
        wrapper.title = 'Icy Veins — auto-extraction coming in a future release';
        wrapper.textContent = '—';
        return wrapper;
    }

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
    archon:    'u.gg',
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
            if (origin === 'icy_veins') {
                opt.textContent = (_ORIGIN_LABELS[origin] || origin) + ' — Coming Soon';
                opt.disabled = true;
                opt.style.color = 'var(--color-text-muted)';
            } else {
                opt.textContent = _ORIGIN_LABELS[origin] || origin;
            }
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
    if (_syncInProgress || _discoveryInProgress) { setStatus('Operation in progress — please wait.', 'error'); return; }
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
                    totalItems  += d.items_upserted || 0;
                    totalErrors += d.errors || 0;
                } else {
                    totalErrors++;
                }
            } catch (_) { totalErrors++; }
        }
    } finally {
        _syncInProgress = false;
        _updateButtonStates();
    }

    await loadMatrix();
    const msg = `${src.name} sync complete — ${totalItems} items upserted, ${totalErrors} errors.`;
    setStatus(msg, totalErrors > 0 ? 'error' : 'success');
    if (_targetsVisible) loadTargets();
}

async function syncAll() {
    if (_syncInProgress || _discoveryInProgress) { setStatus('Operation in progress — please wait.', 'error'); return; }
    if (!confirm('Run full BIS sync for all sources and all specs? This may take several minutes.')) return;

    // Per-spec loop: sync all non-IV sources for each spec in sequence
    const specs = _specs;
    if (!specs.length) { setStatus('Load matrix first.', 'error'); return; }

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
                    totalItems  += d.items_upserted || 0;
                    totalErrors += d.errors || 0;
                } else {
                    totalErrors++;
                }
            } catch (_) { totalErrors++; }
        }
    } finally {
        _syncInProgress = false;
        _updateButtonStates();
    }

    await loadMatrix();
    const msg = `Full sync complete — ${totalItems} items upserted, ${totalErrors} errors.`;
    setStatus(msg, totalErrors > 0 ? 'error' : 'success');
    if (_targetsVisible) loadTargets();
}

async function resyncErrors() {
    if (_syncInProgress || _discoveryInProgress) { setStatus('Operation in progress — please wait.', 'error'); return; }

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
                    totalItems  += d.items_upserted || 0;
                    if (d.status === 'failed') totalErrors++;
                } else {
                    totalErrors++;
                }
            } catch (_) { totalErrors++; }
        }
    } finally {
        _syncInProgress = false;
        _updateButtonStates();
    }

    await loadMatrix();
    const msg = `Re-sync errors complete — ${totalItems} items upserted, ${totalErrors} still failing.`;
    setStatus(msg, totalErrors > 0 ? 'error' : 'success');
    if (_targetsVisible) loadTargets();
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

    const htInfo = _htBySpec[specId]?.find(h => h.id == htId);
    const htLabel = htInfo ? ` — ${htInfo.name}` : (htId ? ` — HT ${htId}` : ' — All builds');
    title.textContent = `BIS Entries — ${srcInfo?.name || sourceId} | ${specInfo?.class_name} ${specInfo?.spec_name}${htLabel}`;
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

        // Actions row — look up the HT-specific cell; fall back to "null" for shared targets
        const _htKey = htId != null ? String(htId) : 'null';
        const _srcCells = (_cells[specId] || {})[sourceId] || {};
        const cellData = _srcCells[_htKey] ?? _srcCells['null'];
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
            await drillDown(_drillSpecId, _drillSourceId, _drillHtId);
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

    // Check if there's any data at all (new format: slot objects with .sources)
    const hasData = SLOT_ORDER.some(slot => (bySlot[slot]?.total_with_data || 0) > 0);
    if (!hasData) {
        content.innerHTML = '<span style="color:var(--color-text-muted);">No BIS data available for this spec. Run a sync first.</span>';
        return;
    }

    // Filter to non-IV sources only (IV is always Coming Soon)
    const activeSources = _sources.filter(s => s.origin !== 'icy_veins');

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
            if (isIV) {
                syncBtn.textContent = 'Coming Soon';
                syncBtn.disabled = true;
                syncBtn.title = 'Icy Veins extraction not yet implemented';
            } else {
                syncBtn.textContent = 'Sync';
                syncBtn.onclick = () => resyncTarget(t.id);
            }
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
    if (btn) btn.disabled = true;
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
        if (btn) btn.disabled = false;
    }
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

        renderItemSources(d.sources || [], showJunk);
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

function renderItemSources(rows, showJunk = false) {
    const tbody = document.getElementById('gp-item-sources-body');
    const countEl = document.getElementById('item-sources-count');
    tbody.innerHTML = '';

    const junkCount = rows.filter(r => r.is_suspected_junk).length;
    let countText = `${rows.length} item${rows.length !== 1 ? 's' : ''}`;
    if (showJunk && junkCount > 0) countText += ` (${junkCount} junk)`;
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
            tr.style.opacity = '0.5';
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

async function flagJunkSources() {
    const btn = document.getElementById('flag-junk-btn');
    if (btn) btn.disabled = true;
    setStatus('Flagging junk sources…', 'info');
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
        if (btn) btn.disabled = false;
    }
}
