/**
 * Admin Guild Quotes page (Phase 4.8)
 * Two-panel: subject list (left) + quotes/titles editor (right)
 */

const API = '/api/v1/admin';
let subjects = [];
let selectedSubject = null;
let pendingDeleteId = null;
let syncNeeded = false;

// ── Bootstrap ───────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    loadSubjects();
});

// ── Subject list ─────────────────────────────────────────────────────────────

async function loadSubjects() {
    const list = document.getElementById('subjectList');
    try {
        const res = await fetch(`${API}/quote-subjects`);
        const json = await res.json();
        subjects = json.data || [];
        renderSubjectList();
    } catch (e) {
        list.innerHTML = '<div style="color:#f87171;font-size:0.85rem;">Failed to load subjects</div>';
    }
}

function renderSubjectList() {
    const list = document.getElementById('subjectList');
    if (!subjects.length) {
        list.innerHTML = '<div style="color:var(--color-text-muted);font-size:0.85rem;padding:0.5rem 0;">No quote subjects yet. Add someone!</div>';
        return;
    }
    list.innerHTML = subjects.map(s => `
        <div class="qq-subject-row${selectedSubject?.id === s.id ? ' active' : ''}"
             data-id="${s.id}" onclick="selectSubject(${s.id})">
            <div style="flex:1;min-width:0;">
                <div class="qq-subject-row__name">${esc(s.display_name)}</div>
                <div class="qq-subject-row__slug">/${esc(s.command_slug)}
                    <span class="qq-subject-row__counts">&nbsp;${s.quote_count}q / ${s.title_count}t</span>
                </div>
            </div>
            <button class="qq-subject-row__toggle"
                    title="${s.active ? 'Active — click to deactivate' : 'Inactive — click to activate'}"
                    onclick="event.stopPropagation(); toggleActive(${s.id}, ${!s.active})">
                ${s.active ? '✅' : '⏸️'}
            </button>
            <button class="qq-subject-row__toggle qq-item__btn--danger"
                    title="Delete subject"
                    onclick="event.stopPropagation(); openDeleteModal(${s.id}, '${esc(s.display_name)}', ${s.quote_count}, ${s.title_count})">
                🗑️
            </button>
        </div>
    `).join('');
}

async function selectSubject(id) {
    selectedSubject = subjects.find(s => s.id === id) || null;
    renderSubjectList();
    if (selectedSubject) renderEditor();
}

async function toggleActive(id, newActive) {
    await apiFetch(`PATCH`, `${API}/quote-subjects/${id}`, { active: newActive });
    markSyncNeeded();
    await loadSubjects();
    if (selectedSubject?.id === id) {
        selectedSubject = subjects.find(s => s.id === id) || null;
        if (selectedSubject) renderEditor();
    }
}

// ── Editor ───────────────────────────────────────────────────────────────────

async function renderEditor() {
    const editor = document.getElementById('editor');
    editor.classList.remove('qq-editor--empty');
    const s = selectedSubject;

    // Fetch quotes + titles
    const [qRes, tRes] = await Promise.all([
        fetch(`${API}/quote-subjects/${s.id}/quotes`),
        fetch(`${API}/quote-subjects/${s.id}/titles`),
    ]);
    const quotes = (await qRes.json()).data || [];
    const titles = (await tRes.json()).data || [];

    editor.innerHTML = `
        <h3 class="qq-editor__heading">Editing: ${esc(s.display_name)}</h3>
        <div class="qq-editor__note">You are editing ${esc(s.display_name)}'s quotes. All changes are immediate.</div>

        <div class="qq-tabs">
            <button class="qq-tab active" onclick="switchTab('quotes')">Quotes (${quotes.length})</button>
            <button class="qq-tab" onclick="switchTab('titles')">Titles (${titles.length})</button>
        </div>

        <div class="qq-panel active" id="panel-quotes">
            <div class="qq-items" id="quoteList">
                ${renderQuoteItems(quotes)}
            </div>
            <div class="qq-add-form">
                <textarea id="newQuoteInput" placeholder="Enter a new quote…"></textarea>
                <button class="qq-btn qq-btn--primary" onclick="addQuote()">Add</button>
            </div>
        </div>

        <div class="qq-panel" id="panel-titles">
            <div class="qq-items" id="titleList">
                ${renderTitleItems(titles)}
            </div>
            <div class="qq-add-form">
                <input type="text" id="newTitleInput" placeholder="Enter a new title…">
                <button class="qq-btn qq-btn--primary" onclick="addTitle()">Add</button>
            </div>
        </div>
    `;
}

function renderQuoteItems(quotes) {
    if (!quotes.length) return '<div style="color:var(--color-text-muted);font-size:0.82rem;">No quotes yet.</div>';
    return quotes.map(q => `
        <div class="qq-item" id="qrow-${q.id}">
            <div class="qq-item__text" id="qtext-${q.id}">${esc(q.quote)}</div>
            <button class="qq-item__btn" title="Edit" onclick="editQuote(${q.id}, ${JSON.stringify(esc(q.quote))})">✏️</button>
            <button class="qq-item__btn qq-item__btn--danger" title="Delete" onclick="deleteQuote(${q.id})">🗑️</button>
        </div>
    `).join('');
}

function renderTitleItems(titles) {
    if (!titles.length) return '<div style="color:var(--color-text-muted);font-size:0.82rem;">No titles yet.</div>';
    return titles.map(t => `
        <div class="qq-item" id="trow-${t.id}">
            <div class="qq-item__text" id="ttext-${t.id}">${esc(t.title)}</div>
            <button class="qq-item__btn" title="Edit" onclick="editTitle(${t.id}, ${JSON.stringify(esc(t.title))})">✏️</button>
            <button class="qq-item__btn qq-item__btn--danger" title="Delete" onclick="deleteTitle(${t.id})">🗑️</button>
        </div>
    `).join('');
}

function switchTab(name) {
    document.querySelectorAll('.qq-tab').forEach((el, i) => {
        el.classList.toggle('active', (i === 0 && name === 'quotes') || (i === 1 && name === 'titles'));
    });
    document.getElementById('panel-quotes').classList.toggle('active', name === 'quotes');
    document.getElementById('panel-titles').classList.toggle('active', name === 'titles');
}

// ── Quote CRUD ────────────────────────────────────────────────────────────────

async function addQuote() {
    const input = document.getElementById('newQuoteInput');
    const text = input.value.trim();
    if (!text) return;
    const res = await apiFetch('POST', `${API}/quote-subjects/${selectedSubject.id}/quotes`, { quote: text });
    if (res?.ok) {
        input.value = '';
        await renderEditor();
        await loadSubjects();  // refresh counts
    }
}

function editQuote(id, currentText) {
    const row = document.getElementById(`qrow-${id}`);
    row.innerHTML = `
        <textarea>${unesc(currentText)}</textarea>
        <button class="qq-item__btn" onclick="saveQuote(${id})">💾</button>
        <button class="qq-item__btn" onclick="renderEditor()">✕</button>
    `;
}

async function saveQuote(id) {
    const row = document.getElementById(`qrow-${id}`);
    const text = row.querySelector('textarea').value.trim();
    if (!text) return;
    const res = await apiFetch('PUT', `${API}/quotes/${id}`, { quote: text });
    if (res?.ok) await renderEditor();
}

async function deleteQuote(id) {
    if (!confirm('Delete this quote?')) return;
    const res = await apiFetch('DELETE', `${API}/quotes/${id}`);
    if (res?.ok) { await renderEditor(); await loadSubjects(); }
}

// ── Title CRUD ────────────────────────────────────────────────────────────────

async function addTitle() {
    const input = document.getElementById('newTitleInput');
    const text = input.value.trim();
    if (!text) return;
    const res = await apiFetch('POST', `${API}/quote-subjects/${selectedSubject.id}/titles`, { title: text });
    if (res?.ok) {
        input.value = '';
        await renderEditor();
        await loadSubjects();
    }
}

function editTitle(id, currentText) {
    const row = document.getElementById(`trow-${id}`);
    row.innerHTML = `
        <input type="text" value="${unesc(currentText)}">
        <button class="qq-item__btn" onclick="saveTitle(${id})">💾</button>
        <button class="qq-item__btn" onclick="renderEditor()">✕</button>
    `;
}

async function saveTitle(id) {
    const row = document.getElementById(`trow-${id}`);
    const text = row.querySelector('input').value.trim();
    if (!text) return;
    const res = await apiFetch('PUT', `${API}/titles/${id}`, { title: text });
    if (res?.ok) await renderEditor();
}

async function deleteTitle(id) {
    if (!confirm('Delete this title?')) return;
    const res = await apiFetch('DELETE', `${API}/titles/${id}`);
    if (res?.ok) { await renderEditor(); await loadSubjects(); }
}

// ── Add Subject modal ─────────────────────────────────────────────────────────

function openAddModal() {
    document.getElementById('addModal').classList.add('open');
    document.getElementById('playerSearch').value = '';
    document.getElementById('playerIdField').value = '';
    document.getElementById('displayNameField').value = '';
    document.getElementById('slugField').value = '';
    document.getElementById('slugPreview').textContent = '/';
    document.getElementById('addError').textContent = '';
    setupPlayerSearch();
    setupSlugSync();
    document.getElementById('playerSearch').focus();
}

function closeAddModal() {
    document.getElementById('addModal').classList.remove('open');
}

function setupPlayerSearch() {
    const input = document.getElementById('playerSearch');
    const dropdown = document.getElementById('playerDropdown');
    let debounce;
    input.oninput = () => {
        clearTimeout(debounce);
        const q = input.value.trim();
        if (q.length < 2) { dropdown.innerHTML = ''; return; }
        debounce = setTimeout(async () => {
            const res = await fetch(`/admin/players-search?q=${encodeURIComponent(q)}`);
            const json = await res.json();
            const players = json.data || [];
            dropdown.innerHTML = players.length
                ? `<div style="
                        position:absolute;z-index:50;background:var(--color-bg-2,#141416);
                        border:1px solid var(--color-border);border-radius:4px;
                        width:100%;max-height:200px;overflow-y:auto;margin-top:2px;
                    ">${players.map(p => `
                        <div style="padding:0.4rem 0.7rem;cursor:pointer;font-size:0.85rem;"
                             onmousedown="selectPlayer(${p.id}, '${esc(p.display_name)}')"
                             onmouseover="this.style.background='var(--color-card)'"
                             onmouseout="this.style.background=''">
                            ${esc(p.display_name)}
                        </div>`).join('')}
                    </div>`
                : '';
        }, 250);
    };
}

function selectPlayer(id, name) {
    document.getElementById('playerIdField').value = id;
    document.getElementById('playerSearch').value = name;
    document.getElementById('playerDropdown').innerHTML = '';
    // Auto-fill display name + slug if empty
    if (!document.getElementById('displayNameField').value) {
        document.getElementById('displayNameField').value = name;
    }
    if (!document.getElementById('slugField').value) {
        const auto = name.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 32) || 'player';
        document.getElementById('slugField').value = auto;
        document.getElementById('slugPreview').textContent = `/${auto}`;
    }
}

function setupSlugSync() {
    const slugInput = document.getElementById('slugField');
    slugInput.oninput = () => {
        document.getElementById('slugPreview').textContent = `/${slugInput.value}`;
    };
}

async function submitAddSubject() {
    const playerId = parseInt(document.getElementById('playerIdField').value);
    const displayName = document.getElementById('displayNameField').value.trim();
    const slug = document.getElementById('slugField').value.trim().toLowerCase();
    const errEl = document.getElementById('addError');
    errEl.textContent = '';

    if (!playerId) { errEl.textContent = 'Please select a player.'; return; }
    if (!displayName) { errEl.textContent = 'Display name is required.'; return; }
    if (!slug) { errEl.textContent = 'Command slug is required.'; return; }

    const res = await apiFetch('POST', `${API}/quote-subjects`, {
        player_id: playerId,
        display_name: displayName,
        command_slug: slug,
    });
    if (res?.ok) {
        closeAddModal();
        markSyncNeeded();
        await loadSubjects();
        const newSubj = subjects.find(s => s.command_slug === slug);
        if (newSubj) selectSubject(newSubj.id);
        toast('Subject added successfully', true);
    } else {
        errEl.textContent = res?.detail || res?.error || 'Failed to create subject';
    }
}

// ── Delete modal ──────────────────────────────────────────────────────────────

function openDeleteModal(id, name, qCount, tCount) {
    pendingDeleteId = id;
    document.getElementById('deleteConfirmText').textContent =
        `This will permanently delete all ${qCount} quote(s) and ${tCount} title(s) for ${name}. This cannot be undone.`;
    document.getElementById('deleteModal').classList.add('open');
}

function closeDeleteModal() {
    document.getElementById('deleteModal').classList.remove('open');
    pendingDeleteId = null;
}

async function confirmDelete() {
    if (!pendingDeleteId) return;
    const res = await apiFetch('DELETE', `${API}/quote-subjects/${pendingDeleteId}`);
    closeDeleteModal();
    if (res?.ok) {
        if (selectedSubject?.id === pendingDeleteId) {
            selectedSubject = null;
            document.getElementById('editor').innerHTML = '<span>Select a person to edit their quotes.</span>';
            document.getElementById('editor').classList.add('qq-editor--empty');
        }
        markSyncNeeded();
        await loadSubjects();
        toast('Subject deleted', true);
    }
}

// ── Sync Bot Commands ─────────────────────────────────────────────────────────

async function syncCommands() {
    const btn = document.querySelector('[onclick="syncCommands()"]');
    btn.disabled = true;
    btn.textContent = '⟳ Syncing…';
    const res = await apiFetch('POST', `${API}/quote-subjects/sync-commands`, {});
    btn.disabled = false;
    btn.textContent = '⟳ Sync Bot';
    if (res?.ok) {
        syncNeeded = false;
        document.getElementById('syncBanner').classList.remove('visible');
        toast('Bot commands synced to Discord!', true);
    } else {
        toast(res?.error || res?.detail || 'Sync failed — check bot logs', false);
    }
}

function markSyncNeeded() {
    syncNeeded = true;
    document.getElementById('syncBanner').classList.add('visible');
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function apiFetch(method, url, body) {
    try {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (body !== undefined && method !== 'DELETE' && method !== 'GET') {
            opts.body = JSON.stringify(body);
        }
        const res = await fetch(url, opts);
        const json = await res.json();
        if (!json.ok && res.status !== 200) {
            toast(json.detail || json.error || `Error ${res.status}`, false);
        }
        return json;
    } catch (e) {
        toast('Network error', false);
        return null;
    }
}

let toastTimer;
function toast(msg, ok) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className = `qq-toast visible ${ok ? 'qq-toast--ok' : 'qq-toast--err'}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('visible'), 3500);
}

function esc(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function unesc(s) {
    return String(s)
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'");
}
