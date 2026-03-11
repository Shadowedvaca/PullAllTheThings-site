const pw = document.getElementById('password');
const pw2 = document.getElementById('password2');
const msg = document.getElementById('pw-match-msg');
function checkMatch() {
    if (!pw2.value) { msg.textContent = ''; return; }
    if (pw.value === pw2.value) {
        msg.textContent = '✓ Passwords match';
        msg.style.color = 'var(--color-success)';
    } else {
        msg.textContent = '✗ Passwords do not match';
        msg.style.color = 'var(--color-danger)';
    }
}
pw.addEventListener('input', checkMatch);
pw2.addEventListener('input', checkMatch);
