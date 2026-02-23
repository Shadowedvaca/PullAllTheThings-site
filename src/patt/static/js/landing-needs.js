// Recruiting Needs — live from platform API
const RAID_TARGETS = { tank: 2, healer: 4, melee: 7, ranged: 7 };
const TANK_SPECS   = ['Protection', 'Guardian', 'Brewmaster', 'Blood', 'Vengeance'];
const HEALER_SPECS = ['Holy', 'Discipline', 'Restoration', 'Mistweaver', 'Preservation'];
const MELEE_SPECS  = [
    'Fury', 'Arms',
    'Retribution',
    'Feral', 'Guardian',
    'Windwalker', 'Brewmaster',
    'Assassination', 'Outlaw', 'Subtlety',
    'Enhancement',
    'Unholy', 'Frost', 'Blood',
    'Havoc', 'Vengeance',
    'Survival'
];

async function loadRecruitingNeeds() {
    try {
        const res = await fetch('/api/v1/guild/roster-data');
        const data = await res.json();
        if (!data.success || !data.characters) { showDefaultNeeds(); return; }

        const counts = { tank: 0, healer: 0, melee: 0, ranged: 0 };
        data.characters.forEach(char => {
            const isMain = char.mainAlt === 'Main';
            if (!isMain) return;
            const spec = char.spec || '';
            const role = (char.role || '').toLowerCase();
            if (role === 'tank' || TANK_SPECS.includes(spec))          counts.tank++;
            else if (role === 'healer' || HEALER_SPECS.includes(spec)) counts.healer++;
            else if (role === 'melee' || MELEE_SPECS.includes(spec))   counts.melee++;
            else                                                         counts.ranged++;
        });

        const total = Object.values(counts).reduce((a, b) => a + b, 0);
        ['tank', 'healer', 'melee', 'ranged'].forEach(role => {
            const target  = RAID_TARGETS[role];
            const current = counts[role];
            const needed  = Math.max(0, target - current);
            const countEl = document.getElementById(`need-${role}-count`);
            const itemEl  = document.getElementById(`need-${role}`);
            if (!countEl) return;
            if (needed === 0) {
                countEl.textContent = `✓ Full (${current}/${target})`;
                itemEl && itemEl.classList.add('fulfilled');
            } else {
                countEl.textContent = `${needed} needed (${current}/${target})`;
                itemEl && itemEl.classList.remove('fulfilled');
                if (needed >= 3 && itemEl) itemEl.classList.add('high-need');
            }
        });
        const noteEl = document.getElementById('needsNote');
        if (noteEl) noteEl.textContent = `Current roster: ${total}/20 • Updated live`;
    } catch (e) {
        showDefaultNeeds();
    }
}

function showDefaultNeeds() {
    const defaults = { tank: '1 needed', healer: '1–2 needed', melee: '3–4 needed', ranged: '3–4 needed' };
    Object.entries(defaults).forEach(([role, text]) => {
        const el = document.getElementById(`need-${role}-count`);
        if (el) el.textContent = text;
    });
}

loadRecruitingNeeds();
