/**
 * admin-forms.js — Admin page helpers
 *
 * - Confirmation dialogs for destructive actions
 * - Duration preset buttons
 * - Character row expand/collapse
 * - Inline Discord ID editing
 */
(function () {
    'use strict';

    // -----------------------------------------------------------------------
    // Confirmation dialogs for destructive form actions
    // -----------------------------------------------------------------------
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('[data-confirm]');
        if (!btn) return;
        const msg = btn.dataset.confirm || 'Are you sure?';
        if (!window.confirm(msg)) {
            e.preventDefault();
            e.stopPropagation();
        }
    });

    // Submit forms with data-confirm on the form itself
    document.addEventListener('submit', function (e) {
        const form = e.target;
        if (!form.dataset.confirm) return;
        const msg = form.dataset.confirm;
        if (!window.confirm(msg)) {
            e.preventDefault();
        }
    });

    // -----------------------------------------------------------------------
    // Duration preset buttons
    // -----------------------------------------------------------------------
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('.preset-btn[data-hours]');
        if (!btn) return;
        const hours = btn.dataset.hours;
        const target = document.getElementById(btn.dataset.target || 'duration_hours');
        if (target) {
            target.value = hours;
            // Highlight active preset
            const siblings = btn.closest('.duration-presets').querySelectorAll('.preset-btn');
            siblings.forEach(s => s.style.color = '');
            btn.style.color = 'var(--color-accent)';
        }
    });

    // -----------------------------------------------------------------------
    // Character row expand/collapse in roster
    // -----------------------------------------------------------------------
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('[data-toggle-chars]');
        if (!btn) return;
        const memberId = btn.dataset.toggleChars;
        const row = document.getElementById(`chars-${memberId}`);
        if (!row) return;
        row.classList.toggle('open');
        btn.textContent = row.classList.contains('open') ? '▲ Hide' : '▼ Characters';
    });

    // -----------------------------------------------------------------------
    // Inline Discord ID editing in roster
    // -----------------------------------------------------------------------
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('[data-edit-discord]');
        if (!btn) return;
        const memberId = btn.dataset.editDiscord;
        const display = document.getElementById(`discord-display-${memberId}`);
        const editor = document.getElementById(`discord-editor-${memberId}`);
        if (!display || !editor) return;
        display.style.display = 'none';
        editor.style.display = 'flex';
        editor.querySelector('input')?.focus();
    });

    document.addEventListener('click', function (e) {
        const btn = e.target.closest('[data-cancel-discord]');
        if (!btn) return;
        const memberId = btn.dataset.cancelDiscord;
        const display = document.getElementById(`discord-display-${memberId}`);
        const editor = document.getElementById(`discord-editor-${memberId}`);
        if (!display || !editor) return;
        display.style.display = '';
        editor.style.display = 'none';
    });

})();
