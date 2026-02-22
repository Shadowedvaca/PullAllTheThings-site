/**
 * countdown.js — Campaign countdown timer
 *
 * Reads data-end-ts (Unix timestamp in seconds) from .countdown elements
 * and updates them every second.
 */
(function () {
    'use strict';

    function formatDuration(seconds) {
        if (seconds <= 0) return null; // expired
        const d = Math.floor(seconds / 86400);
        const h = Math.floor((seconds % 86400) / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);

        const parts = [];
        if (d > 0) parts.push(`${d}d`);
        if (h > 0 || d > 0) parts.push(`${h}h`);
        if (m > 0 || h > 0 || d > 0) parts.push(`${m}m`);
        parts.push(`${s}s`);
        return parts.join(' ');
    }

    function tick() {
        const countdowns = document.querySelectorAll('.countdown[data-end-ts]');
        const now = Math.floor(Date.now() / 1000);

        countdowns.forEach(el => {
            const endTs = parseInt(el.dataset.endTs, 10);
            const remaining = endTs - now;
            const display = formatDuration(remaining);

            if (display === null) {
                el.classList.add('countdown--ended');
                el.textContent = 'Voting has ended';
                // Reload after a short delay so the page state updates
                setTimeout(() => { window.location.reload(); }, 3000);
            } else {
                el.textContent = display;
            }
        });
    }

    // Run immediately, then every second
    tick();
    setInterval(tick, 1000);
})();
