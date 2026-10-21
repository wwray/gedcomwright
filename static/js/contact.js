/* Assembles the real mailto: href from data attributes (BRIEF.md: "a
 * mailto link with a lightly obfuscated email (assembled by JS, with a
 * no-JS fallback)"). The no-JS fallback is the <noscript> text sitting
 * right next to each .contact-link in the templates - this script just
 * upgrades the link for everyone else. Classic script, not a module, so
 * it has no dependency on how/whether gallery.js's module graph loads.
 */
(function () {
  function wire(el) {
    var user = el.dataset.user;
    var domain = el.dataset.domain;
    if (!user || !domain) return;
    var href = "mailto:" + user + "@" + domain;
    if (el.dataset.subject) {
      href += "?subject=" + encodeURIComponent(el.dataset.subject);
    }
    el.setAttribute("href", href);
  }
  document.querySelectorAll(".contact-link[data-user]").forEach(wire);
})();
