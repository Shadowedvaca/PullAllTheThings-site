(() => {
    "use strict";

    const toggle = document.querySelector(".site-menu__toggle");
    const panel = document.getElementById("site-menu-panel");
    if (!toggle || !panel) return;

    function setOpen(open) {
        panel.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
        toggle.setAttribute("aria-label", open ? "Close site menu" : "Open site menu");
        toggle.classList.toggle("is-open", open);
    }

    toggle.addEventListener("click", (event) => {
        event.stopPropagation();
        setOpen(panel.hidden);
    });

    panel.addEventListener("click", (event) => event.stopPropagation());
    document.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setOpen(false);
            toggle.focus();
        }
    });
})();
