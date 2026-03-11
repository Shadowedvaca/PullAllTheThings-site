/**
 * vote-interaction.js — Ranked-choice voting UI
 *
 * Handles card selection, pick summary bar, form submission.
 * Works with the vote/campaign.html template.
 */
(function () {
    'use strict';

    const MAX_PICKS = parseInt(document.getElementById('vote-grid')?.dataset.maxPicks || '3', 10);

    // picks: array of {entryId, name} in order (index 0 = 1st choice)
    let picks = [];

    const grid = document.getElementById('vote-grid');
    const submitBtn = document.getElementById('vote-submit-btn');
    const picksForm = document.getElementById('vote-form');
    const voteError = document.getElementById('vote-error');

    if (!grid) return; // not on vote page

    // -----------------------------------------------------------------------
    // Click handler for entry cards
    // -----------------------------------------------------------------------
    grid.addEventListener('click', function (e) {
        const card = e.target.closest('.entry-card[data-entry-id]');
        if (!card) return;

        const entryId = parseInt(card.dataset.entryId, 10);
        const entryName = card.dataset.entryName;

        const existingIdx = picks.findIndex(p => p.entryId === entryId);

        if (existingIdx !== -1) {
            // Deselect: remove and shift others down
            picks.splice(existingIdx, 1);
        } else {
            // Select: only if we haven't hit the max
            if (picks.length >= MAX_PICKS) return;
            picks.push({ entryId, name: entryName });
        }

        renderPicks();
    });

    // -----------------------------------------------------------------------
    // Render state
    // -----------------------------------------------------------------------
    function renderPicks() {
        // Update card badges
        const cards = grid.querySelectorAll('.entry-card[data-entry-id]');
        cards.forEach(card => {
            const entryId = parseInt(card.dataset.entryId, 10);
            const pickIdx = picks.findIndex(p => p.entryId === entryId);
            const badge = card.querySelector('.pick-badge');

            // Remove all selection classes
            card.classList.remove('selected-1', 'selected-2', 'selected-3');
            if (badge) badge.classList.remove('visible', 'pick-badge--1', 'pick-badge--2', 'pick-badge--3');

            if (pickIdx !== -1) {
                const rank = pickIdx + 1;
                card.classList.add(`selected-${rank}`);
                if (badge) {
                    badge.textContent = rank;
                    badge.classList.add('visible', `pick-badge--${rank}`);
                }
            }
        });

        // Update picks bar slots
        for (let i = 1; i <= MAX_PICKS; i++) {
            const slot = document.getElementById(`pick-slot-${i}`);
            if (!slot) continue;
            const nameEl = slot.querySelector('.pick-slot__name');
            const emptyEl = slot.querySelector('.pick-slot__empty');
            const pick = picks[i - 1];
            if (pick) {
                if (nameEl) { nameEl.textContent = pick.name; nameEl.style.display = ''; }
                if (emptyEl) emptyEl.style.display = 'none';
            } else {
                if (nameEl) nameEl.style.display = 'none';
                if (emptyEl) emptyEl.style.display = '';
            }
        }

        // Enable/disable submit button
        if (submitBtn) {
            submitBtn.disabled = picks.length !== MAX_PICKS;
        }
    }

    // -----------------------------------------------------------------------
    // Form submission (POST form with hidden inputs)
    // -----------------------------------------------------------------------
    if (submitBtn && picksForm) {
        submitBtn.addEventListener('click', function (e) {
            e.preventDefault();
            if (picks.length !== MAX_PICKS) return;

            // Build hidden inputs for each pick
            const hiddenContainer = document.getElementById('vote-hidden-inputs');
            if (hiddenContainer) hiddenContainer.innerHTML = '';

            picks.forEach((pick, idx) => {
                const rankInput = document.createElement('input');
                rankInput.type = 'hidden';
                rankInput.name = `pick_entry_${idx}`;
                rankInput.value = pick.entryId;
                if (hiddenContainer) hiddenContainer.appendChild(rankInput);

                const rankNumInput = document.createElement('input');
                rankNumInput.type = 'hidden';
                rankNumInput.name = `pick_rank_${idx}`;
                rankNumInput.value = idx + 1;
                if (hiddenContainer) hiddenContainer.appendChild(rankNumInput);
            });

            picksForm.submit();
        });
    }

    // Initial render
    renderPicks();
})();
