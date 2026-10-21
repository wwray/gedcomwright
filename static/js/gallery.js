/* Media viewers (BRIEF.md Stage 3 "Image viewer"):
 * - Photos: a PhotoSwipe v5 lightbox (zoom, pan, swipe/arrow nav, Esc).
 * - Documents: OpenSeadragon in a native <dialog>, with zoom + fullscreen.
 * Both show the caption/date; a URL-hash deep link is PhotoSwipe's own
 * built-in behaviour (enabled by default) for photos. Document deep-linking
 * isn't wired yet - a design-proposal gap, not forgotten; see README.md.
 */
import PhotoSwipeLightbox from "../vendor/photoswipe/photoswipe-lightbox.esm.min.js";

const lightbox = new PhotoSwipeLightbox({
  // Scoped to the whole article, not just #media-gallery, so the hero
  // portrait (outside the gallery grid) opens in the same lightbox
  // sequence as everything else.
  gallery: ".person",
  children: "a.gallery-photo",
  pswpModule: () => import("../vendor/photoswipe/photoswipe.esm.min.js"),
  // PhotoSwipe's default wheel behaviour is to pan (ctrl+wheel zooms); Will
  // asked for plain wheel-to-zoom to match the document viewer below, which
  // already does this via OpenSeadragon's own default
  // (gestureSettingsMouse.scrollToZoom, confirmed in its vendored source -
  // true unless explicitly turned off, which gallery.js doesn't do).
  wheelToZoom: true,
});

// Core PhotoSwipe v5 has no built-in caption UI; this is the documented
// way to add one without pulling in the separate caption plugin.
lightbox.on("uiRegister", function () {
  lightbox.pswp.ui.registerElement({
    name: "caption",
    order: 9,
    isButton: false,
    appendTo: "root",
    onInit: (el) => {
      lightbox.pswp.on("change", () => {
        const trigger = lightbox.pswp.currSlide?.data?.element;
        el.textContent = trigger?.dataset.caption || "";
      });
    },
  });
});

lightbox.init();

// --- documents: OpenSeadragon inside a native <dialog> ---
const dialog = document.getElementById("document-viewer");
const osdContainer = document.getElementById("osd-container");
let osdViewer = null;

if (dialog && osdContainer && window.OpenSeadragon) {
  document.querySelectorAll(".gallery-document").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const pages = JSON.parse(trigger.dataset.pages || "[]");
      if (!pages.length) return;
      // A multi-page PDF's pages were previously processed but
      // unreachable in the UI (only page 1 ever showed, 6 Oct 2026) -
      // OpenSeadragon's own sequenceMode gives built-in prev/next controls
      // for an array of tileSources, so no custom paging UI is needed.
      const tileSources = pages.map((p) =>
        p.zoom.kind === "dzi" ? p.zoom.path : { type: "image", url: p.zoom.path }
      );

      dialog.querySelector(".viewer-caption").textContent = trigger.dataset.caption || "";
      dialog.showModal();

      if (osdViewer) {
        osdViewer.destroy();
        osdViewer = null;
      }
      osdViewer = window.OpenSeadragon({
        element: osdContainer,
        prefixUrl: dialog.dataset.iconsPrefix,
        tileSources,
        sequenceMode: tileSources.length > 1,
        showSequenceControl: tileSources.length > 1,
        showNavigationControl: true,
        gestureSettingsMouse: { clickToZoom: false },
      });
    });
  });

  dialog.querySelector(".viewer-close").addEventListener("click", () => dialog.close());

  dialog.addEventListener("close", () => {
    if (osdViewer) {
      osdViewer.destroy();
      osdViewer = null;
    }
  });

  // click on the backdrop (outside the dialog's own box) closes it
  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    const insideDialog =
      rect.top <= event.clientY && event.clientY <= rect.top + rect.height &&
      rect.left <= event.clientX && event.clientX <= rect.left + rect.width;
    if (!insideDialog) dialog.close();
  });
}
