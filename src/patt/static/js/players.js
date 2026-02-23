// Player Manager — 3-column drag-and-drop editor
// Discord Users | Players | Characters
'use strict';

let discordUsers = [];
let players = [];
let allChars = [];

let dragType = null;   // 'discord' | 'char'
let dragId = null;     // discord_id (string) or char id (number)

// ── Drill state ────────────────────────────────────────────────────────────
// When active, overrides search filters to show only related items.
// drill.memberIds / drill.discordIds / drill.charIds are Sets of allowed IDs.
let drill = null;  // null = off, or { label, memberIds, discordIds, charIds }

function drillOn(type, id) {
    let memberIds = new Set();
    let discordIds = new Set();
    let charIds = new Set();
    let label = '';

    if (type === 'discord') {
        const u = discordUsers.find(u => u.id === id);
        if (!u) return;
        label = u.display_name || u.username;
        discordIds.add(id);
        // Find linked player(s)
        players.filter(p => p.discord_id === id).forEach(p => {
            memberIds.add(p.id);
            allChars.filter(c => c.member_id === p.id).forEach(c => charIds.add(c.id));
        });
    } else if (type === 'player') {
        const p = players.find(p => p.id === id);
        if (!p) return;
        label = p.display_name || p.discord_username;
        memberIds.add(id);
        if (p.discord_id) discordIds.add(p.discord_id);
        allChars.filter(c => c.member_id === id).forEach(c => charIds.add(c.id));
    } else if (type === 'char') {
        const c = allChars.find(c => c.id === id);
        if (!c) return;
        label = c.name;
        charIds.add(id);
        if (c.member_id) {
            memberIds.add(c.member_id);
            // All chars for same player
            allChars.filter(ch => ch.member_id === c.member_id).forEach(ch => charIds.add(ch.id));
            const p = players.find(p => p.id === c.member_id);
            if (p && p.discord_id) discordIds.add(p.discord_id);
        }
    }

    drill = { label, memberIds, discordIds, charIds };
    render();
}

function clearDrill() {
    drill = null;
    render();
}

// ── Bootstrap ──────────────────────────────────────────────────────────────

async function loadData() {
    try {
        const res = await fetch('/admin/players-data');
        const data = await res.json();
        if (!data.ok) { showStatus('Failed to load data: ' + (data.error || '?'), 'error'); return; }
        discordUsers = data.data.discord_users || [];
        players      = data.data.players       || [];
        allChars     = data.data.characters    || [];
        render();
    } catch (e) {
        showStatus('Network error loading data', 'error');
    }
}

// ── Render all three columns ───────────────────────────────────────────────

function render() {
    // Drill banner
    const banner = document.getElementById('pm-drill-banner');
    if (banner) {
        if (drill) {
            banner.style.display = 'flex';
            banner.querySelector('.pm-drill-label').textContent = `Filtered: ${drill.label}`;
        } else {
            banner.style.display = 'none';
        }
    }
    renderDiscord();
    renderPlayers();
    renderChars();
    // re-attach unlink zone events (rendered into player-list div)
    attachUnlinkZones();
}

// ── Col 1: Discord Users ───────────────────────────────────────────────────

function renderDiscord() {
    const search  = (document.getElementById('discord-search').value || '').toLowerCase();
    const unlinkedOnly = document.getElementById('discord-unlinked-only').checked;

    const filtered = discordUsers.filter(u => {
        if (drill) return drill.discordIds.has(u.id);
        if (unlinkedOnly && u.linked) return false;
        if (search && !u.display_name.toLowerCase().includes(search) &&
                      !u.username.toLowerCase().includes(search)) return false;
        return true;
    });

    document.getElementById('discord-count').textContent =
        `(${discordUsers.length} total, ${discordUsers.filter(u => !u.linked).length} unlinked)`;

    const list = document.getElementById('discord-list');
    list.innerHTML = filtered.map(u => `
        <div class="pm-discord-row ${u.linked ? 'pm-linked' : 'pm-unlinked-row'} ${drill && drill.discordIds.has(u.id) ? 'pm-drill-active' : ''}"
             draggable="true"
             data-discord-id="${escAttr(u.id)}"
             data-username="${escAttr(u.username)}"
             ondragstart="handleDiscordDragStart(event, '${escAttr(u.id)}', '${escAttr(u.username)}')">
            <button class="pm-drill-btn" onclick="drillOn('discord','${escAttr(u.id)}')" title="Focus on ${escAttr(u.display_name)}">◎</button>
            <span class="pm-discord-icon">💬</span>
            <div class="pm-discord-info">
                <span class="pm-discord-name">${escHtml(u.display_name)}</span>
                <span class="pm-discord-handle text-muted">@${escHtml(u.username)}</span>
            </div>
            <div class="pm-discord-badges">
                ${u.rank_name ? `<span class="pm-discord-rank">${escHtml(u.rank_name)}</span>` : ''}
                ${u.linked
                    ? '<span class="pm-badge pm-badge--ok">linked</span>'
                    : '<span class="pm-badge pm-badge--warn">unlinked</span>'}
            </div>
        </div>
    `).join('');
}

// ── Col 2: Players ─────────────────────────────────────────────────────────

function renderPlayers() {
    const search = (document.getElementById('player-search').value || '').toLowerCase();

    const filtered = players.filter(p => {
        if (drill) return drill.memberIds.has(p.id);
        if (!search) return true;
        return (p.display_name || '').toLowerCase().includes(search) ||
               (p.discord_username || '').toLowerCase().includes(search);
    });

    document.getElementById('player-count').textContent = `(${players.length})`;

    const list = document.getElementById('player-list');
    const unzoneHtml = `
        <div class="pm-unassign-zone" id="pm-discord-unlink">
            <span>🚫 Drop Discord user here to unlink</span>
        </div>`;

    list.innerHTML = filtered.map(p => {
        const charCount = allChars.filter(c => c.member_id === p.id).length;
        const discordUser = p.discord_id ? discordUsers.find(u => u.id === p.discord_id) : null;
        const discordLabel = discordUser
            ? `<span class="pm-player-discord">💬 @${escHtml(discordUser.username)}</span>`
            : `<span class="pm-player-discord pm-missing">No Discord</span>`;
        const regBadge = p.registered
            ? '<span class="pm-badge pm-badge--ok">Reg</span>' : '';

        // Display name: player-set > discord server name > main char name
        const effectiveName = p.display_name
            || (discordUser ? discordUser.display_name : null)
            || p.main_char_name
            || p.discord_username;
        const nameIsSet = !!p.display_name;

        // Role: main char game role > discord rank > nothing
        const effectiveRole = p.main_char_role || (discordUser ? discordUser.rank_name : null) || '';
        const roleIcon = effectiveRole === 'tank' ? '🛡️' : effectiveRole === 'healer' ? '💚'
                       : (effectiveRole === 'dps' || effectiveRole === 'melee_dps' || effectiveRole === 'ranged_dps') ? '⚔️' : '';

        return `
        <div class="pm-player-card ${drill && drill.memberIds.has(p.id) ? 'pm-drill-active' : ''}"
             data-member-id="${p.id}"
             ondragover="event.preventDefault();this.classList.add('pm-drag-over')"
             ondragleave="this.classList.remove('pm-drag-over')"
             ondrop="handlePlayerDrop(event, ${p.id})">
            <div class="pm-player-header">
                <button class="pm-drill-btn" onclick="drillOn('player',${p.id})" title="Focus on ${escAttr(effectiveName)}">◎</button>
                ${roleIcon ? `<span class="pm-player-role-icon">${roleIcon}</span>` : ''}
                <span class="pm-player-name ${nameIsSet ? '' : 'pm-name-derived'}"
                      title="${nameIsSet ? 'Custom display name' : 'Derived — click pencil to set'}"
                >${escHtml(effectiveName)}</span>
                <button class="pm-edit-name-btn" onclick="startEditName(event,${p.id},'${escAttr(p.display_name || '')}')"
                        title="Edit display name">✎</button>
                <span class="pm-player-rank">${escHtml(p.rank_name)}</span>
                ${regBadge}
                <button class="pm-delete-player-btn" onclick="deletePlayer(event,${p.id},'${escAttr(effectiveName)}')"
                        title="Delete player">🗑</button>
            </div>
            <div id="pm-name-editor-${p.id}" class="pm-name-editor" style="display:none;">
                <input class="pm-name-input form-control" id="pm-name-input-${p.id}"
                       placeholder="${escAttr(effectiveName)}" value="${escAttr(p.display_name || '')}"
                       style="font-size:0.82rem;">
                <button class="btn btn-primary btn-sm" onclick="saveDisplayName(event,${p.id})">Save</button>
                <button class="btn btn-secondary btn-sm" onclick="cancelEditName(${p.id})">✕</button>
            </div>
            <div class="pm-player-meta">
                ${discordLabel}
                <span class="pm-char-count">${charCount} char${charCount !== 1 ? 's' : ''}</span>
            </div>
        </div>`;
    }).join('') + unzoneHtml;
}

// ── Col 3: Characters ──────────────────────────────────────────────────────

function renderChars() {
    const search = (document.getElementById('char-search').value || '').toLowerCase();
    const unlinkedOnly = document.getElementById('char-unlinked-only').checked;

    const filtered = allChars.filter(c => {
        if (drill) return drill.charIds.has(c.id);
        if (unlinkedOnly && c.member_id) return false;
        if (search &&
            !c.name.toLowerCase().includes(search) &&
            !(c.class        || '').toLowerCase().includes(search) &&
            !(c.spec         || '').toLowerCase().includes(search) &&
            !(c.guild_note   || '').toLowerCase().includes(search) &&
            !(c.officer_note || '').toLowerCase().includes(search)) return false;
        return true;
    });

    const unlinkedCount = allChars.filter(c => !c.member_id).length;
    document.getElementById('char-count').textContent =
        `(${allChars.length} total, ${unlinkedCount} unlinked)`;

    const list = document.getElementById('char-list');
    const unzoneHtml = `
        <div class="pm-unassign-zone" id="pm-char-unlink">
            <span>🚫 Drop here to unlink character</span>
        </div>`;

    list.innerHTML = filtered.map(c => {
        const roleIcon  = c.role === 'tank' ? '🛡️' : c.role === 'healer' ? '💚' : '⚔️';
        const roleClass = c.role === 'tank' ? 'tank' : c.role === 'healer' ? 'healer' : 'dps';
        const isMain    = c.main_alt === 'main';
        const owner     = c.member_id ? players.find(p => p.id === c.member_id) : null;
        const ownerLabel = owner
            ? `<span class="pm-char-owner">→ ${escHtml(owner.display_name || owner.discord_username)}</span>`
            : `<span class="pm-char-unlinked">Unlinked</span>`;
        const notInScanBadge = c.in_wow_scan ? ''
            : `<span class="pm-badge pm-badge--warn pm-not-in-scan" title="Not found in Blizzard API scan — name may have changed">? API</span>
               <button class="pm-delete-btn" onclick="deleteChar(event,${c.id},'${escAttr(c.name)}')" title="Delete this character">✕</button>`;
        const noteHtml = (c.guild_note || c.officer_note)
            ? `<div class="pm-char-notes">
                ${c.guild_note    ? `<span class="pm-char-note pm-char-note--guild" title="Guild note">${escHtml(c.guild_note)}</span>` : ''}
                ${c.officer_note  ? `<span class="pm-char-note pm-char-note--officer" title="Officer note">${escHtml(c.officer_note)}</span>` : ''}
               </div>`
            : '';

        return `
        <div class="pm-char-row ${drill && drill.charIds.has(c.id) ? 'pm-drill-active' : ''}">
            <div class="pm-char-chip-wrap">
                <div class="pm-char-chip pm-char-chip--${roleClass}"
                     draggable="true"
                     data-char-id="${c.id}"
                     ondragstart="handleCharDragStart(event, ${c.id})"
                     ondragend="this.classList.remove('pm-dragging')">
                    <button class="pm-drill-btn" onclick="drillOn('char',${c.id})" title="Focus on ${escAttr(c.name)}">◎</button>
                    <span class="pm-role-icon">${roleIcon}</span>
                    <span class="pm-char-name">${escHtml(c.name)}</span>
                    <span class="pm-char-realm text-muted">${escHtml(c.realm)}</span>
                    <span class="pm-char-spec">${escHtml(c.spec || c.class || '')}</span>
                    ${c.guild_rank_name ? `<span class="pm-char-guild-rank">${escHtml(c.guild_rank_name)}</span>` : ''}
                    ${isMain ? '<span class="pm-main-badge">M</span>' : '<span class="pm-alt-badge">A</span>'}
                    ${notInScanBadge}
                    <button class="pm-toggle-btn" onclick="toggleMain(event,${c.id})"
                            title="Toggle Main/Alt">${isMain ? 'Alt?' : 'Main?'}</button>
                </div>
                <div class="pm-char-row2">${ownerLabel}${noteHtml}</div>
            </div>
        </div>`;
    }).join('') + unzoneHtml;
}

function attachUnlinkZones() {
    const dz = document.getElementById('pm-discord-unlink');
    if (dz) {
        dz.ondragover = e => { e.preventDefault(); dz.classList.add('pm-drag-over'); };
        dz.ondragleave = () => dz.classList.remove('pm-drag-over');
        dz.ondrop = handleDiscordUnlinkDrop;
    }
    const cz = document.getElementById('pm-char-unlink');
    if (cz) {
        cz.ondragover = e => { e.preventDefault(); cz.classList.add('pm-drag-over'); };
        cz.ondragleave = () => cz.classList.remove('pm-drag-over');
        cz.ondrop = handleCharUnlinkDrop;
    }
}

// ── Drag: Discord users ────────────────────────────────────────────────────

function handleDiscordDragStart(event, discordId, username) {
    dragType = 'discord';
    dragId   = discordId;
    event.dataTransfer.effectAllowed = 'move';
    event.currentTarget.classList.add('pm-dragging');
    document.addEventListener('dragend', clearDrag, { once: true });
}

async function handleDiscordUnlinkDrop(event) {
    event.preventDefault();
    document.getElementById('pm-discord-unlink').classList.remove('pm-drag-over');
    if (dragType !== 'discord') return;
    // Find which player has this discord_id and unlink them
    const p = players.find(pl => pl.discord_id === dragId);
    if (!p) { clearDrag(); return; }
    await linkDiscord(p.id, null, null);
}

// ── Drag: Characters ───────────────────────────────────────────────────────

function handleCharDragStart(event, charId) {
    dragType = 'char';
    dragId   = charId;
    event.dataTransfer.effectAllowed = 'move';
    event.currentTarget.classList.add('pm-dragging');
    document.addEventListener('dragend', clearDrag, { once: true });
}

async function handleCharUnlinkDrop(event) {
    event.preventDefault();
    document.getElementById('pm-char-unlink').classList.remove('pm-drag-over');
    if (dragType !== 'char') return;
    await assignChar(dragId, null);
}

// ── Drop on Player ─────────────────────────────────────────────────────────

async function handlePlayerDrop(event, memberId) {
    event.preventDefault();
    event.currentTarget.classList.remove('pm-drag-over');

    if (dragType === 'discord') {
        const username = event.currentTarget
            .closest ? discordUsers.find(u => u.id === dragId)?.username : '';
        await linkDiscord(memberId, dragId, username || '');
    } else if (dragType === 'char') {
        const char = allChars.find(c => c.id === dragId);
        if (char && char.member_id === memberId) { clearDrag(); return; }
        await assignChar(dragId, memberId);
    }
}

// ── API calls ──────────────────────────────────────────────────────────────

async function linkDiscord(memberId, discordId, discordUsername) {
    clearDrag();
    try {
        const res = await fetch(`/admin/players/${memberId}/link-discord`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ discord_id: discordId, discord_username: discordUsername }),
        });
        const data = await res.json();
        if (data.ok) {
            const p = players.find(pl => pl.id === memberId);
            if (p) p.discord_id = discordId;
            // Update linked flag on discord user
            discordUsers.forEach(u => {
                if (u.id === discordId) u.linked = true;
                // If this player previously had a different discord, unlink that
            });
            render();
            showStatus(discordId ? 'Discord account linked' : 'Discord account unlinked', 'success');
        } else {
            showStatus('Error: ' + (data.error || '?'), 'error');
        }
    } catch (e) {
        showStatus('Network error linking Discord', 'error');
    }
}

async function assignChar(charId, memberId) {
    clearDrag();
    try {
        const res = await fetch(`/admin/characters/${charId}/assign`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ member_id: memberId }),
        });
        const data = await res.json();
        if (data.ok) {
            const c = allChars.find(ch => ch.id === charId);
            if (c) c.member_id = memberId;
            render();
            const dest = memberId ? `assigned to ${data.data.member_name}` : 'unlinked';
            showStatus(`${data.data.char_name} ${dest}`, 'success');
        } else {
            showStatus('Error: ' + (data.error || '?'), 'error');
        }
    } catch (e) {
        showStatus('Network error assigning character', 'error');
    }
}

// ── Player display name editing ────────────────────────────────────────────

function startEditName(event, memberId, current) {
    event.stopPropagation();
    const editor = document.getElementById(`pm-name-editor-${memberId}`);
    const input  = document.getElementById(`pm-name-input-${memberId}`);
    if (editor) {
        editor.style.display = 'flex';
        input.focus();
        input.select();
    }
}
function cancelEditName(memberId) {
    const editor = document.getElementById(`pm-name-editor-${memberId}`);
    if (editor) editor.style.display = 'none';
}
async function saveDisplayName(event, memberId) {
    event.stopPropagation();
    const input = document.getElementById(`pm-name-input-${memberId}`);
    const name = (input ? input.value : '').trim();
    try {
        const res = await fetch(`/admin/players/${memberId}/display-name`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_name: name }),
        });
        const data = await res.json();
        if (data.ok) {
            const p = players.find(pl => pl.id === memberId);
            if (p) p.display_name = data.data.display_name || null;
            render();
            showStatus(name ? `Name set to "${name}"` : 'Name cleared (using fallback)', 'success');
        } else {
            showStatus('Error: ' + (data.error || '?'), 'error');
        }
    } catch (e) {
        showStatus('Network error saving name', 'error');
    }
}

// ── Delete Player ──────────────────────────────────────────────────────────

async function deletePlayer(event, memberId, name) {
    event.stopPropagation();
    if (!confirm(`Delete player "${name}"?\n\nThis unlinks their characters but does not delete the characters themselves.`)) return;
    try {
        const res = await fetch(`/admin/players/${memberId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.ok) {
            players = players.filter(p => p.id !== memberId);
            // Unlink any chars that belonged to this player
            allChars.forEach(c => { if (c.member_id === memberId) c.member_id = null; });
            render();
            showStatus(`"${name}" deleted`, 'success');
        } else {
            showStatus('Error: ' + (data.error || '?'), 'error');
        }
    } catch (e) {
        showStatus('Network error deleting player', 'error');
    }
}

// ── Delete Character ───────────────────────────────────────────────────────

async function deleteChar(event, charId, charName) {
    event.stopPropagation();
    if (!confirm(`Delete "${charName}"? This cannot be undone.`)) return;
    try {
        const res = await fetch(`/admin/characters/${charId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.ok) {
            allChars = allChars.filter(c => c.id !== charId);
            render();
            showStatus(`"${charName}" deleted`, 'success');
        } else {
            showStatus('Error: ' + (data.error || '?'), 'error');
        }
    } catch (e) {
        showStatus('Network error deleting character', 'error');
    }
}

// ── Toggle Main/Alt ────────────────────────────────────────────────────────

async function toggleMain(event, charId) {
    event.stopPropagation();
    const c = allChars.find(ch => ch.id === charId);
    if (!c) return;
    const newVal = c.main_alt === 'main' ? 'alt' : 'main';
    try {
        const res = await fetch(`/admin/characters/${charId}/main-alt`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ main_alt: newVal }),
        });
        const data = await res.json();
        if (data.ok) {
            c.main_alt = newVal;
            render();
            showStatus(`${c.name} → ${newVal}`, 'success');
        } else {
            showStatus('Error: ' + (data.error || '?'), 'error');
        }
    } catch (e) {
        showStatus('Network error', 'error');
    }
}

// ── Create Player ──────────────────────────────────────────────────────────

function showNewPlayerForm() {
    document.getElementById('new-player-form').style.display = 'block';
    document.getElementById('new-player-name').focus();
}
function hideNewPlayerForm() {
    document.getElementById('new-player-form').style.display = 'none';
    document.getElementById('new-player-name').value = '';
}
async function createPlayer() {
    const name = document.getElementById('new-player-name').value.trim();
    if (!name) return;
    try {
        const res = await fetch('/admin/players/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_name: name }),
        });
        const data = await res.json();
        if (data.ok) {
            players.push(data.data);
            players.sort((a, b) => (a.display_name || a.discord_username).localeCompare(b.display_name || b.discord_username));
            hideNewPlayerForm();
            render();
            showStatus(`Player "${name}" created`, 'success');
        } else {
            showStatus('Error: ' + (data.error || '?'), 'error');
        }
    } catch (e) {
        showStatus('Network error creating player', 'error');
    }
}

// ── Helpers ────────────────────────────────────────────────────────────────

function clearDrag() {
    dragType = null;
    dragId   = null;
    document.querySelectorAll('.pm-dragging').forEach(el => el.classList.remove('pm-dragging'));
    document.querySelectorAll('.pm-drag-over').forEach(el => el.classList.remove('pm-drag-over'));
}

function showStatus(msg, type) {
    const el = document.getElementById('pm-status');
    el.textContent = msg;
    el.className = `flash-bar flash-bar--${type === 'error' ? 'error' : 'success'}`;
    el.style.display = 'block';
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.style.display = 'none'; }, 3500);
}

function escHtml(s) {
    if (s == null) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) { return escHtml(s); }

// ── Search / filter wiring ─────────────────────────────────────────────────

document.getElementById('discord-search').addEventListener('input', renderDiscord);
document.getElementById('discord-unlinked-only').addEventListener('change', renderDiscord);
document.getElementById('player-search').addEventListener('input', renderPlayers);
document.getElementById('char-search').addEventListener('input', renderChars);
document.getElementById('char-unlinked-only').addEventListener('change', renderChars);

loadData();
