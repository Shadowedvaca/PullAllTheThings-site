(() => {
    "use strict";

    const canvas = document.getElementById("sw-wheel");
    const ctx = canvas.getContext("2d");
    const spinButton = document.getElementById("sw-spin");
    const openFilter = document.getElementById("sw-filter-open");
    const newFilter = document.getElementById("sw-filter-new");
    const resultBox = document.getElementById("sw-result");
    const message = document.getElementById("sw-wheel-message");
    const dialog = document.getElementById("sw-assign-dialog");

    let state = null;
    let selectedSlot = "main";
    let spinning = false;
    let currentRotation = 0;
    let pendingAssignment = null;

    const roleShort = {
        "Tank": "TANK",
        "Healer": "HEAL",
        "Melee DPS": "MELEE",
        "Ranged DPS": "RANGED",
    };

    function eligibleSpecs() {
        if (!state) return [];
        const openRoles = new Set(Object.keys(state.open_role_needs));
        const represented = new Set(state.represented_spec_ids);
        return state.specs.filter((spec) =>
            (!openFilter.checked || openRoles.has(spec.role))
            && (!newFilter.checked || !represented.has(spec.id))
        );
    }

    function shade(hex, amount) {
        const safe = /^#[0-9a-f]{6}$/i.test(hex || "") ? hex.slice(1) : "666666";
        const value = parseInt(safe, 16);
        const channel = (shift) => Math.max(0, Math.min(255, ((value >> shift) & 255) + amount));
        return `rgb(${channel(16)}, ${channel(8)}, ${channel(0)})`;
    }

    function drawWheel(specs = eligibleSpecs(), rotation = currentRotation) {
        const width = canvas.width;
        const center = width / 2;
        const radius = center - 12;
        ctx.clearRect(0, 0, width, width);

        if (!specs.length) {
            ctx.beginPath();
            ctx.arc(center, center, radius, 0, Math.PI * 2);
            ctx.fillStyle = "#1a1a1d";
            ctx.fill();
            ctx.strokeStyle = "#d4a84b";
            ctx.lineWidth = 8;
            ctx.stroke();
            ctx.fillStyle = "#888";
            ctx.font = "600 24px 'Source Sans 3'";
            ctx.textAlign = "center";
            ctx.fillText("No matching specializations", center, center - 20);
            return;
        }

        const arc = (Math.PI * 2) / specs.length;
        specs.forEach((spec, index) => {
            const start = -Math.PI / 2 + rotation + index * arc;
            const end = start + arc;
            ctx.beginPath();
            ctx.moveTo(center, center);
            ctx.arc(center, center, radius, start, end);
            ctx.closePath();
            ctx.fillStyle = shade(spec.color_hex, index % 2 ? -20 : 4);
            ctx.fill();
            ctx.strokeStyle = "rgba(10,10,11,.72)";
            ctx.lineWidth = 2;
            ctx.stroke();

            ctx.save();
            ctx.translate(center, center);
            ctx.rotate(start + arc / 2);
            ctx.textAlign = "right";
            ctx.textBaseline = "middle";
            ctx.fillStyle = "#fff";
            ctx.shadowColor = "#000";
            ctx.shadowBlur = 3;
            ctx.font = `700 ${specs.length > 30 ? 12 : 14}px 'Source Sans 3'`;
            const label = `${spec.class_name} · ${spec.name}`;
            ctx.fillText(label, radius - 22, -5, radius * 0.67);
            ctx.fillStyle = "rgba(255,255,255,.78)";
            ctx.font = "700 9px 'Source Sans 3'";
            ctx.fillText(roleShort[spec.role] || spec.role, radius - 22, 9);
            ctx.restore();
        });

        ctx.beginPath();
        ctx.arc(center, center, radius, 0, Math.PI * 2);
        ctx.strokeStyle = "#d4a84b";
        ctx.lineWidth = 9;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(center, center, radius - 10, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(244,212,134,.55)";
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(center, center, 78, 0, Math.PI * 2);
        ctx.fillStyle = "#171719";
        ctx.fill();
    }

    function formatDate(value) {
        if (!value) return "";
        return new Intl.DateTimeFormat(undefined, {
            month: "short",
            day: "numeric",
            year: "numeric",
        }).format(new Date(value));
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function safeColor(value) {
        return /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#d4a84b";
    }

    function specLine(spec) {
        return `${spec.class_name} — ${spec.name}`;
    }

    function renderHistory() {
        const bySlot = Object.fromEntries(state.history.map((item) => [item.slot, item]));
        document.getElementById("sw-history").innerHTML = ["main", "offspec"].map((slot) => {
            const item = bySlot[slot];
            const label = slot === "main" ? "Main" : "Off-spec";
            if (!item) {
                return `<section class="sw-history-slot">
                    <div class="sw-history-slot__head"><span>${label}</span><span>0 rolls</span></div>
                    <p class="sw-history-empty">No roll yet this season.</p>
                </section>`;
            }
            const firstLine = escapeHtml(specLine(item.first));
            const latestLine = escapeHtml(specLine(item.latest));
            return `<section class="sw-history-slot">
                <div class="sw-history-slot__head"><span>${label}</span><span>${item.roll_count} ${item.roll_count === 1 ? "roll" : "rolls"}</span></div>
                <div class="sw-history-roll">
                    <span class="sw-history-roll__label">First</span>
                    <div><strong style="color:${safeColor(item.first.color_hex)}">${firstLine}</strong><small>${escapeHtml(item.first.role)} · ${formatDate(item.first_rolled_at)}</small></div>
                </div>
                <div class="sw-history-roll">
                    <span class="sw-history-roll__label">Latest</span>
                    <div><strong style="color:${safeColor(item.latest.color_hex)}">${latestLine}</strong><small>${escapeHtml(item.latest.role)} · ${formatDate(item.latest_rolled_at)}</small></div>
                </div>
            </section>`;
        }).join("");
        document.getElementById("sw-total-rolls").textContent = state.season_roll_count;
    }

    function renderFilters() {
        const needs = Object.entries(state.open_role_needs);
        document.getElementById("sw-open-summary").textContent = needs.length
            ? needs.map(([role, count]) => `${role} +${count}`).join(" · ")
            : "The roster currently meets every role target.";
        const eligible = eligibleSpecs();
        document.getElementById("sw-pool-count").textContent = eligible.length;
        spinButton.disabled = spinning || eligible.length === 0;
        message.textContent = eligible.length
            ? ""
            : "No specializations match both selected filters.";
        drawWheel(eligible, 0);
        currentRotation = 0;
    }

    async function loadState(showLoading = true) {
        if (showLoading) {
            document.getElementById("sw-loading").hidden = false;
            document.getElementById("sw-app").hidden = true;
        }
        const response = await fetch("/api/v1/spec-wheel");
        const payload = await response.json();
        if (!response.ok) throw new Error(errorMessage(payload));
        state = payload.data;
        document.getElementById("sw-season").textContent = state.season.name;
        renderHistory();
        renderFilters();
        document.getElementById("sw-loading").hidden = true;
        document.getElementById("sw-app").hidden = false;
    }

    function errorMessage(payload) {
        const detail = payload && payload.detail;
        if (typeof detail === "string") return detail;
        if (detail && typeof detail.message === "string") return detail.message;
        return "Something went wrong. Please try again.";
    }

    function hasSlotHistory(slot) {
        return state.history.some((item) => item.slot === slot);
    }

    function replacementConfirmed() {
        const label = selectedSlot === "main" ? "main" : "off-spec";
        return window.confirm(
            `You already rolled for your ${label} this season.\n\n`
            + "Spin again and replace your latest roll? Your first roll will remain saved."
        );
    }

    function animateTo(specs, selected) {
        return new Promise((resolve) => {
            const index = specs.findIndex((spec) => spec.id === selected.id);
            const arc = (Math.PI * 2) / specs.length;
            const target = -(index + 0.5) * arc;
            const start = currentRotation;
            const targetRotation = target + Math.PI * 2 * 7;
            const duration = 5200;
            const started = performance.now();

            function frame(now) {
                const progress = Math.min(1, (now - started) / duration);
                const eased = 1 - Math.pow(1 - progress, 4);
                currentRotation = start + (targetRotation - start) * eased;
                drawWheel(specs, currentRotation);
                if (progress < 1) {
                    requestAnimationFrame(frame);
                } else {
                    currentRotation = target % (Math.PI * 2);
                    drawWheel(specs, currentRotation);
                    resolve();
                }
            }
            requestAnimationFrame(frame);
        });
    }

    async function spinWheel(forceReplace = false) {
        if (spinning) return;
        let replace = forceReplace;
        if (hasSlotHistory(selectedSlot) && !replace) {
            if (!replacementConfirmed()) return;
            replace = true;
        }

        spinning = true;
        spinButton.disabled = true;
        resultBox.hidden = true;
        message.textContent = "The wheel is choosing…";

        try {
            const response = await fetch("/api/v1/spec-wheel/spin", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    slot: selectedSlot,
                    only_open_roles: openFilter.checked,
                    only_unrepresented: newFilter.checked,
                    replace,
                }),
            });
            const payload = await response.json();
            if (!response.ok) {
                if (response.status === 409
                    && payload.detail?.code === "replacement_required"
                    && !replace
                    && replacementConfirmed()) {
                    spinning = false;
                    return spinWheel(true);
                }
                throw new Error(errorMessage(payload));
            }

            const data = payload.data;
            await animateTo(data.eligible_specs, data.result);
            const label = data.slot === "main" ? "Main result" : "Off-spec result";
            document.getElementById("sw-result-slot").textContent = label;
            document.getElementById("sw-result-name").textContent = specLine(data.result);
            document.getElementById("sw-result-role").textContent = data.result.role;
            resultBox.style.setProperty("--result-color", data.result.color_hex || "#d4a84b");
            resultBox.hidden = false;
            await loadState(false);
            message.textContent = "Your result is saved.";
            if (data.characters.length) {
                openAssignmentDialog(data);
            }
        } catch (error) {
            message.textContent = error.message;
        } finally {
            spinning = false;
            spinButton.disabled = eligibleSpecs().length === 0;
        }
    }

    function characterLabel(character) {
        const level = character.level == null ? "?" : character.level;
        return `${character.character_name}-${character.realm} lvl ${level}`;
    }

    function openAssignmentDialog(data) {
        pendingAssignment = data;
        const slotLabel = data.slot === "main" ? "main" : "off-spec";
        document.getElementById("sw-assign-title").textContent = `Set a ${data.result.class_name} as your ${slotLabel}?`;
        document.getElementById("sw-assign-copy").textContent =
            `This also sets ${data.result.name} as the selected ${slotLabel} specialization.`;
        const select = document.getElementById("sw-character-select");
        select.innerHTML = "";
        data.characters.forEach((character) => {
            const option = document.createElement("option");
            option.value = character.id;
            option.textContent = characterLabel(character);
            select.appendChild(option);
        });
        document.getElementById("sw-assign-error").hidden = true;
        dialog.showModal();
    }

    async function assignCharacter() {
        if (!pendingAssignment) return;
        const button = document.getElementById("sw-assign-confirm");
        const errorBox = document.getElementById("sw-assign-error");
        button.disabled = true;
        try {
            const response = await fetch("/api/v1/spec-wheel/assign-character", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    slot: pendingAssignment.slot,
                    character_id: Number(document.getElementById("sw-character-select").value),
                }),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(errorMessage(payload));
            dialog.close();
            message.textContent = `${characterLabel(payload.data)} is now set as your ${pendingAssignment.slot === "main" ? "main" : "off-spec"} character.`;
            pendingAssignment = null;
        } catch (error) {
            errorBox.textContent = error.message;
            errorBox.hidden = false;
        } finally {
            button.disabled = false;
        }
    }

    document.querySelectorAll(".sw-slot").forEach((button) => {
        button.addEventListener("click", () => {
            selectedSlot = button.dataset.slot;
            document.querySelectorAll(".sw-slot").forEach((item) => {
                item.classList.toggle("active", item === button);
            });
        });
    });
    openFilter.addEventListener("change", renderFilters);
    newFilter.addEventListener("change", renderFilters);
    spinButton.addEventListener("click", () => spinWheel());
    document.getElementById("sw-assign-confirm").addEventListener("click", assignCharacter);

    loadState().catch((error) => {
        document.getElementById("sw-loading").hidden = true;
        const errorBox = document.getElementById("sw-error");
        errorBox.textContent = error.message;
        errorBox.hidden = false;
    });
})();
