"""GEDCOM 5.5.1 parser (BRIEF.md, Stage 1: "a small purpose-built one is
fine", used only because it handles FTM's actual output correctly).

Handles CONC/CONT line continuation, cross-references, over-long lines (FTM
routinely writes lines past the 255-character limit; this parser never
enforces it), and the encoding declared in the header.

Only UTF-8 (with or without a BOM) is supported, because that is what every
export seen so far declares. A file declaring a different CHAR value raises
UnsupportedEncoding rather than silently mis-decoding it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_LINE_RE = re.compile(r"^(\d+)[ ]+(?:(@[^@]*@)[ ]+)?([A-Za-z0-9_]+)(?:[ ](.*))?$")


class GedcomParseError(ValueError):
    """Reserved for callers; the parser itself tolerates unparseable lines
    (see Gedcom.bad_lines) rather than raising, since one bad line shouldn't
    abort inspection of the rest of a 50,000-line file."""


class UnsupportedEncoding(ValueError):
    """The file declares a CHAR encoding this parser doesn't support."""


@dataclass
class Node:
    """One line of the GEDCOM record tree, with CONC/CONT already merged into
    `value` and child tags attached under `children`."""

    level: int
    xref: str | None
    tag: str
    value: str
    lineno: int
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)

    def first(self, *tags: str) -> "Node | None":
        """The first child whose tag is one of `tags`, or None."""
        for c in self.children:
            if c.tag in tags:
                return c
        return None

    def all(self, *tags: str) -> list["Node"]:
        """Every child whose tag is one of `tags`, in document order."""
        return [c for c in self.children if c.tag in tags]

    def val(self, *tags: str) -> str | None:
        """The value of the first child whose tag is one of `tags`, or None."""
        n = self.first(*tags)
        return n.value if n is not None else None

    def path(self) -> str:
        """Dotted tag path from the record root, e.g. 'INDI.BIRT.DATE'."""
        parts: list[str] = []
        node: Node | None = self
        while node is not None:
            parts.append(node.tag)
            node = node.parent
        return ".".join(reversed(parts))

    def walk(self):
        """Depth-first iterator over every descendant (not including self)."""
        for c in self.children:
            yield c
            yield from c.walk()

    def __repr__(self) -> str:
        xref = f"{self.xref} " if self.xref else ""
        return f"<Node {self.level} {xref}{self.tag} {self.value[:40]!r}>"


class Gedcom:
    """A parsed GEDCOM file: `records` (the top-level, 0-level nodes) and
    `by_xref` (lookup from xref, e.g. '@I1@', to its record node))."""

    def __init__(self, path: Path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        text, encoding, has_bom = _decode(raw)
        self._parse_text(text, encoding, has_bom)

    @classmethod
    def from_text(cls, text: str) -> "Gedcom":
        """Parse already-decoded GEDCOM text directly, with no file on disk -
        for tests that want a small, tightly-scoped fixture inline rather
        than a whole file under tests/fixtures/."""
        self = cls.__new__(cls)
        self.path = None
        self._parse_text(text, "UTF-8", False)
        return self

    def _parse_text(self, text: str, encoding: str, has_bom: bool) -> None:
        self.encoding, self.has_bom = encoding, has_bom
        self.crlf_count = text.count("\r\n")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        # Only the line terminator is stripped. Trailing spaces are kept,
        # because a CONC continuation can carry a significant leading or
        # trailing space that joins two words across the line break.
        self.lines = [ln[:-1] if ln.endswith("\r") else ln for ln in lines]
        self.bare_lf_count = len(self.lines) - self.crlf_count

        self.long_lines: list[tuple[int, int]] = []
        self.bad_lines: list[tuple[int, str]] = []
        root = Node(level=-1, xref=None, tag="", value="", lineno=0)
        stack: list[Node] = [root]

        for i, ln in enumerate(self.lines, start=1):
            if not ln.strip():
                continue
            if len(ln) > 255:
                self.long_lines.append((i, len(ln)))
            m = _LINE_RE.match(ln)
            if not m:
                self.bad_lines.append((i, ln))
                continue
            level = int(m.group(1))
            node = Node(level=level, xref=m.group(2), tag=m.group(3),
                        value=m.group(4) or "", lineno=i)
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            node.parent = stack[-1] if stack[-1] is not root else None
            stack[-1].children.append(node)
            stack.append(node)

        self._merge_continuations(root)
        self.records: list[Node] = root.children
        self.by_xref: dict[str, Node] = {r.xref: r for r in self.records if r.xref}

    @staticmethod
    def _merge_continuations(node: Node) -> None:
        """Fold CONC (no line break) and CONT (line break) children into the
        value of the preceding sibling, recursively and bottom-up, so a
        caller never sees a CONC/CONT tag in the final tree."""
        for c in node.children:
            Gedcom._merge_continuations(c)
        kept: list[Node] = []
        for c in node.children:
            if c.tag == "CONC" and kept:
                kept[-1].value += c.value
            elif c.tag == "CONT" and kept:
                kept[-1].value += "\n" + c.value
            elif c.tag in ("CONC", "CONT"):
                # Nothing precedes it to extend (malformed input) - fold into
                # this node's own value rather than silently dropping it.
                node.value += ("" if c.tag == "CONC" else "\n") + c.value
            else:
                kept.append(c)
        node.children = kept

    def of_type(self, tag: str) -> list[Node]:
        """All top-level records with this tag (e.g. 'INDI', 'FAM', 'SOUR')."""
        return [r for r in self.records if r.tag == tag]

    def walk_all(self):
        """Depth-first iterator over every record and all its descendants."""
        for r in self.records:
            yield r
            yield from r.walk()


def _decode(raw: bytes) -> tuple[str, str, bool]:
    """Decode GEDCOM bytes per the header's CHAR tag.

    Returns (text, encoding_name, had_bom). Only UTF-8 is supported; any
    other declared charset raises UnsupportedEncoding rather than guessing,
    since mis-decoding would silently corrupt non-ASCII names and places.
    """
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    if has_bom:
        return raw.decode("utf-8-sig"), "UTF-8", True

    # No BOM: peek at the header with a superset encoding that never raises,
    # to find the declared CHAR value before committing to a real decode.
    head = raw[:4000].decode("latin-1", errors="replace")
    m = re.search(r"^1 CHAR[ \t]*(\S*)", head, re.MULTILINE)
    declared = m.group(1).strip().upper() if m else ""
    if declared in ("", "UTF-8", "UTF8"):
        return raw.decode("utf-8"), "UTF-8", False
    raise UnsupportedEncoding(
        f"GEDCOM declares CHAR {declared!r}; only UTF-8 is supported "
        "(this pipeline is built against FTM's UTF-8 export only)."
    )


def newest_ged(folder: Path) -> Path:
    """The most recently modified *.ged file in `folder` (case-insensitive
    extension). Raises FileNotFoundError if there isn't one."""
    folder = Path(folder)
    candidates = sorted(
        (p for p in folder.glob("*") if p.suffix.lower() == ".ged"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"no .ged file in {folder}")
    return candidates[0]
