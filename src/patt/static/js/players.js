// Player Manager — drag-and-drop character assignment editor
'use strict';

let allMembers = [];
let allChars = [];
let draggedCharId = null;

// ── Bootstrap ──────────────────────────────────────────────────────────────

async function loadData() {
    try {
        const res = await fetch('/api/v1/admin/players-data');
        const data = await res.json();
        if (!data.ok) { showStatus('Failed to load player data', 'error'); return; }
        allMembers = data.data.members;
        allChars   = data.data.characters;
        render();
    } catch (e) {
        showStatus('Network error loading data', 'error');
    }
}

// ── Render ─────────────────────────────────────────────────────────────────

function render() {
    renderPlayers();
    renderChars();
    // Re-attach unassign zone listeners (DOM persists, but re-bind for safety)
    const uz = document.getElementById('pm-unassign');
    uz.ondragover = e => { e.preventDefault(); uz.classList.add('pm-drag-over'); };
    uz.ondragleave = () => uz.classList.remove('pm-drag-over');
    uz.ondrop = handleUnassignDrop;
}

function renderPlayers() {
    const list = document.getElementById('player-list');
    document.getElementById('player-count').textContent = `(${allMembers.length})`;

    list.innerHTML = allMembers.map(m => {
        const chars = allChars.filter(c => c.member_id === m.id);
        const discordBadge = m.discord_id
            ? `<span class="pm-discord-id text-mono">${m.discord_id}</span>`
            : `<span class="pm-badge pm-badge--warn">No Discord ID</span>`;
        const regBadge = m.registered
            ? `<span class="pm-badge pm-badge--ok">Registered</span>`
            : '';

        return `
        <div class="pm-player-card"
             data-member-id="${m.id}"
             ondragover="event.preventDefault();this.classList.add('pm-drag-over')"
             ondragleave="this.classList.remove('pm-drag-over')"
             ondrop="handleDrop(event,${m.id})">
            <div class="pm-player-header">
                <div class="pm-player-name">${escHtml(m.display_name || m.discord_username)}</div>
                <div class="pm-player-meta">
                    @${escHtml(m.discord_username)}
                    &nbsp;•&nbsp;<span class="pm-rank">${escHtml(m.rank_name)}</span>
                </div>
                <div class="pm-player-badges">${discordBadge}${regBadge}</div>
            </div>
            <div class="pm-player-chars" id="pchars-${m.id}">
                ${chars.length
                    ? chars.map(c => charChipHtml(c, true)).join('')
                    : '<div class="pm-drop-hint">Drop characters here</div>'}
            </div>
        </div>`;
    }).join('');
}

function renderChars() {
    const list   = document.getElementById('char-list');
    const search = (document.getElementById('char-search').value || '').toLowerCase();
    const filtered = allChars.filter(c =>
        !search ||
        c.name.toLowerCase().includes(search) ||
        (c.spec  || '').toLowerCase().includes(search) ||
        (c.class || '').toLowerCase().includes(search)
    );
    document.getElementById('char-count').textContent = `(${allChars.length})`;

    list.innerHTML = filtered.map(c => {
        const owner = c.member_id ? allMembers.find(m => m.id === c.member_id) : null;
        const ownerLabel = owner
            ? `<span class="pm-char-owner">→ ${escHtml(owner.display_name || owner.discord_username)}</span>`
            : `<span class="pm-char-unlinked">Unlinked</span>`;
        return `<div class="pm-char-row">${charChipHtml(c, false)}${ownerLabel}</div>`;
    }).join('');
}

function charChipHtml(c, inPlayer) {
    const roleIcon  = c.role === 'tank' ? '🛡️' : c.role === 'healer' ? '💚' : '⚔️';
    const roleClass = c.role === 'tank' ? 'tank' : c.role === 'healer' ? 'healer' : 'dps';
    const isMain    = c.main_alt === 'main';
    const mainBadge = isMain
        ? `<span class="pm-main-badge" title="Main">M</span>`
        : `<span class="pm-alt-badge" title="Alt">A</span>`;
    const toggleBtn = `<button class="pm-toggle-btn" onclick="toggleMain(event,${c.id})" title="Toggle Main / Alt">${isMain ? 'Alt?' : 'Main?'}</button>`;

    return `
    <div class="pm-char-chip pm-char-chip--${roleClass}"
         draggable="true"
         data-char-id="${c.id}"
         ondragstart="handleDragStart(event,${c.id})"
         ondragend="this.classList.remove('pm-dragging')">
        <span class="pm-role-icon">${roleIcon}</span>
        <span class="pm-char-name">${escHtml(c.name)}</span>
        <span class="pm-char-realm text-muted">${escHtml(c.realm)}</span>
        <span class="pm-char-spec">${escHtml(c.spec || c.class || '')}</span>
        ${mainBadge}
        ${toggleBtn}
    </div>`;
}

// ── Drag & Drop ────────────────────────────────────────────────────────────

function handleDragStart(event, charId) {
    draggedCharId = charId;
    event.dataTransfer.effectAllowed = 'move';
    event.currentTarget.classList.add('pm-dragging');
}

document.addEventListener('dragend', () => {
    document.querySelectorAll('.pm-drag-over').forEach(el => el.classList.remove('pm-drag-over'));
});

async function handleDrop(event, memberId) {
    event.preventDefault();
    event.currentTarget.classList.remove('pm-drag-over');
    if (draggedCharId === null) return;

    const charId = draggedCharId;
    draggedCharId = null;

    const char = allChars.find(c => c.id === charId);
    if (char && char.member_id === memberId) return; // no-op, already assigned

    await assignChar(charId, memberId);
}

async function handleUnassignDrop(event) {
    event.preventDefault();
    if (draggedCharId === null) return;
    const charId = draggedCharId;
    draggedCharId = null;
    await assignChar(charId, null);
}

async function assignChar(charId, memberId) {
    try {
        const res = await fetch(`/api/v1/admin/characters/${charId}/assign`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ member_id: memberId }),
        });
        const data = await res.json();
        if (data.ok) {
            const char = allChars.find(c => c.id === charId);
            if (char) char.member_id = memberId;
            render();
            const dest = memberId ? `assigned to ${data.data.member_name}` : 'unlinked';
            showStatus(`${data.data.char_name} ${dest}`, 'success');
        } else {
            showStatus(`Error: ${data.error}`, 'error');
        }
    } catch (e) {
        showStatus('Network error saving assignment', 'error');
    }
}

// ── Toggle Main/Alt ────────────────────────────────────────────────────────

async function toggleMain(event, charId) {
    event.stopPropagation();
    const char = allChars.find(c => c.id === charId);
    if (!char) return;
    const newVal = char.main_alt === 'main' ? 'alt' : 'main';

    try {
        const res = await fetch(`/api/v1/admin/characters/${charId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ main_alt: newVal }),
        });
        const data = await res.json();
        if (data.ok) {
            char.main_alt = newVal;
            render();
            showStatus(`${char.name} is now ${newVal === 'main' ? 'the Main' : 'an Alt'}`, 'success');
        } else {
            showStatus(`Error: ${data.error}`, 'error');
        }
    } catch (e) {
        showStatus('Network error toggling main/alt', 'error');
    }
}

// ── UI Helpers ─────────────────────────────────────────────────────────────

function showStatus(msg, type) {
    const el = document.getElementById('pm-status');
    el.textContent = msg;
    el.className = `flash-bar flash-bar--${type === 'error' ? 'error' : 'success'}`;
    el.style.display = 'block';
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.style.display = 'none'; }, 3500);
}

function escHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ── Init ───────────────────────────────────────────────────────────────────

document.getElementById('char-search').addEventListener('input', () => renderChars());
loadData();
