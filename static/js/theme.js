/* Manual light/dark toggle. base.html's inline <head> script already set
 * data-theme from localStorage before paint (if a choice was saved) - this
 * file only wires the click interaction and persists new choices.
 */
(function () {
  const toggle = document.querySelector(".theme-toggle");
  if (!toggle) return;

  function effectiveTheme() {
    const explicit = document.documentElement.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    toggle.setAttribute("aria-pressed", String(theme === "dark"));
    try {
      localStorage.setItem("theme", theme);
    } catch (e) {
      /* private browsing / blocked storage - toggle still works for this page view */
    }
  }

  toggle.setAttribute("aria-pressed", String(effectiveTheme() === "dark"));
  toggle.addEventListener("click", () => {
    apply(effectiveTheme() === "dark" ? "light" : "dark");
  });
})();
