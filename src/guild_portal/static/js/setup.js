/* Setup wizard shared utilities */

/**
 * Show an error message in the step-error element.
 */
function showError(msg) {
    const el = document.getElementById('step-error');
    if (!el) { console.error('showError:', msg); return; }
    el.textContent = msg;
    el.style.display = 'block';
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/**
 * Hide the step error message.
 */
function hideError() {
    const el = document.getElementById('step-error');
    if (el) { el.style.display = 'none'; el.textContent = ''; }
}

/**
 * Set a verify status indicator.
 * @param {string} id - element id
 * @param {'loading'|'success'|'error'} state
 * @param {string} msg
 */
function setVerifyStatus(id, state, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = 'setup-verify-status is-' + state;
    el.textContent = msg;
}

/**
 * POST JSON to a setup API endpoint. On success, navigate to nextUrl.
 * On failure, show the error in #step-error.
 */
async function apiPost(url, body, nextUrl) {
    hideError();
    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.ok) {
            if (nextUrl) window.location.href = nextUrl;
            return data;
        } else {
            showError(data.detail || data.error || 'An error occurred. Please try again.');
        }
    } catch (e) {
        showError('Network error: ' + e.message);
    }
    return null;
}
