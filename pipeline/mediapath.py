"""Resolve FTM's absolute media paths onto the configured media folder
(BRIEF.md Stage 2: "Resolve FTM's absolute paths to the configured media
folder (prefix rewrite, case-insensitive). ... Handle filenames with
leading spaces, & and double spaces.").

Shared by pipeline.lint and pipeline.media so there's one implementation of
this, not two slightly-different ones.
"""
from __future__ import annotations

from pathlib import Path, PureWindowsPath


def resolve_media_path(media_dir: Path, raw: str) -> Path | None:
    """FTM stores an absolute Windows path; rewrite it onto `media_dir` by
    finding that folder's own name within the stored path, so this works
    regardless of whose machine the GEDCOM was exported on (a different
    username, drive letter, etc.) - only the media folder's own name has to
    match. Leading spaces, '&' and double spaces in path components are
    ordinary characters to pathlib, so they need no special handling once
    the path is split into parts; comparison is case-insensitive because
    Windows filesystems are.
    """
    raw_parts = PureWindowsPath(raw).parts
    folder_name = PureWindowsPath(str(media_dir)).parts[-1].lower()
    lowered = [p.lower() for p in raw_parts]
    if folder_name not in lowered:
        return None
    idx = lowered.index(folder_name)
    return Path(media_dir, *raw_parts[idx + 1:])
