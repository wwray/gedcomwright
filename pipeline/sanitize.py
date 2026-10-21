"""HTML allowlist sanitizer (BRIEF.md, Stage 1: "HTML in source notes:
sanitize to an allowlist (p, i, em, b, strong, a[href], br, ul, ol, li)").

Pure stdlib (html.parser) - no new dependency for a nine-tag allowlist.
Disallowed tags are dropped but their text content is kept, since an unknown
wrapper tag shouldn't delete the text inside it; <script>/<style> are the
exception - their content is dropped too, defensively, even though none
appear in the real data (confirmed in Phase 0: only i, p, a, li, br, ul).
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset({"p", "i", "em", "b", "strong", "a", "br", "ul", "ol", "li"})
_VOID_TAGS = frozenset({"br"})
_RAW_TEXT_TAGS = frozenset({"script", "style"})  # dropped, content and all
_SAFE_HREF_PREFIXES = ("http://", "https://", "mailto:")


def _safe_href(href: str) -> str | None:
    href = (href or "").strip()
    if href.lower().startswith(_SAFE_HREF_PREFIXES):
        return href
    return None


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._open_stack: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _RAW_TEXT_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth or tag not in ALLOWED_TAGS:
            return
        if tag == "a":
            href = _safe_href(dict(attrs).get("href", ""))
            self.out.append(f'<a href="{escape(href, quote=True)}">' if href else "<a>")
        else:
            self.out.append(f"<{tag}>")
        if tag not in _VOID_TAGS:
            self._open_stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID_TAGS and not self._skip_depth:
            self.out.append(f"<{tag}>")
        elif tag in ALLOWED_TAGS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _RAW_TEXT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth or tag not in ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        # Close any unbalanced tags opened after this one too, so output is
        # always properly nested even if the source wasn't.
        while self._open_stack:
            t = self._open_stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self._skip_depth:
            self.out.append(escape(data))

    def close(self):
        super().close()
        while self._open_stack:
            self.out.append(f"</{self._open_stack.pop()}>")


def sanitize_html(raw: str | None) -> str:
    """Strip everything except the allowlisted tags/attributes."""
    if not raw:
        return ""
    parser = _Sanitizer()
    parser.feed(raw)
    parser.close()
    return "".join(parser.out)
