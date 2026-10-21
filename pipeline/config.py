"""config.yaml loading (BRIEF.md Architecture: "one config file" driving
every stage - paths, site title/domain, contact, privacy years, thresholds,
overrides)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .gedcom import newest_ged
from .media import MediaThresholds
from .privacy import Thresholds


class ConfigError(ValueError):
    """config.yaml is missing a required key or has an invalid value."""


@dataclass
class Config:
    # paths
    gedcom_dir: Path | None
    gedcom_file: Path | None
    media_dir: Path
    build_dir: Path
    overrides_dir: Path

    # site
    site_title: str
    site_domain: str
    site_base_path: str
    contact_email: str

    # privacy
    home_person: str
    min_generation: int
    thresholds: Thresholds
    show_living_placeholder: bool

    # facts
    suppress_facts: list[str]

    # lint (BRIEF.md "Tree lint": "Configurable thresholds; report only.")
    lint_min_parent_age: int
    lint_max_mother_age: int
    lint_min_marriage_age: int

    # media (BRIEF.md Stage 2: "All thresholds are configurable.")
    media: MediaThresholds

    # publish (BRIEF.md Stage 5). Optional - empty until Will sets up the
    # dedicated SFTP-only deploy account server-side (see README.md) and
    # fills this in. Kept as a raw dict, not its own dataclass, since
    # nothing else in Config needs to know its shape - only
    # pipeline.publish.PublishSettings.from_config() reads it.
    publish: dict

    @property
    def data_dir(self) -> Path:
        return self.build_dir / "data"

    @property
    def reports_dir(self) -> Path:
        return self.build_dir / "reports"

    @property
    def site_dir(self) -> Path:
        return self.build_dir / "site"

    def resolve_gedcom(self) -> Path:
        """The explicit gedcom_file if config sets one, otherwise the newest
        *.ged in gedcom_dir (BRIEF.md: "Either confirm a fixed filename, or
        have config point at a folder and take the newest .ged in it")."""
        if self.gedcom_file is not None:
            if not self.gedcom_file.exists():
                raise FileNotFoundError(f"paths.gedcom_file not found: {self.gedcom_file}")
            return self.gedcom_file
        if self.gedcom_dir is None:
            raise ConfigError("config.yaml: set either paths.gedcom_file or paths.gedcom_dir")
        return newest_ged(self.gedcom_dir)

    @classmethod
    def load(cls, path: Path, *, base_dir: Path | None = None) -> "Config":
        """`base_dir` is where relative paths (build_dir, overrides_dir) are
        resolved against; defaults to config.yaml's own directory."""
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        base_dir = base_dir or path.parent

        def _req(d: dict, key: str, section: str):
            if key not in d:
                raise ConfigError(f"config.yaml: missing required key '{section}.{key}'")
            return d[key]

        paths = raw.get("paths", {})
        site = raw.get("site", {})
        privacy = raw.get("privacy", {})
        facts = raw.get("facts", {})
        lint = raw.get("lint", {})
        media = raw.get("media", {})
        publish = raw.get("publish", {})

        gedcom_file = paths.get("gedcom_file")
        gedcom_dir = paths.get("gedcom_dir")
        if not gedcom_file and not gedcom_dir:
            raise ConfigError("config.yaml: set either paths.gedcom_file or paths.gedcom_dir")

        thresholds = Thresholds(
            event_years=int(privacy.get("event_years", 100)),
            child_years=int(privacy.get("child_years", 100)),
            parent_years=int(privacy.get("parent_years", 175)),
            sibling_years=int(privacy.get("sibling_years", 130)),
            recent_years=int(privacy.get("recent_years", 100)),
            recent_child_years=int(privacy.get("recent_child_years", 85)),
        )

        return cls(
            gedcom_dir=Path(gedcom_dir) if gedcom_dir else None,
            gedcom_file=Path(gedcom_file) if gedcom_file else None,
            media_dir=Path(_req(paths, "media_dir", "paths")),
            build_dir=(base_dir / paths.get("build_dir", "build")),
            overrides_dir=(base_dir / paths.get("overrides_dir", "overrides")),
            site_title=site.get("title", "Family Tree"),
            site_domain=site.get("domain", ""),
            site_base_path=site.get("base_path", ""),
            contact_email=site.get("contact_email", ""),
            home_person=_req(privacy, "home_person", "privacy"),
            min_generation=int(privacy.get("min_generation", 2)),
            thresholds=thresholds,
            show_living_placeholder=bool(privacy.get("show_living_placeholder", True)),
            suppress_facts=list(facts.get("suppress", ["Race"])),
            lint_min_parent_age=int(lint.get("min_parent_age", 12)),
            lint_max_mother_age=int(lint.get("max_mother_age", 55)),
            lint_min_marriage_age=int(lint.get("min_marriage_age", 14)),
            media=MediaThresholds(
                thumbnail_width=int(media.get("thumbnail_width", 400)),
                display_width=int(media.get("display_width", 2048)),
                srcset_widths=tuple(sorted(set(media.get(
                    "srcset_widths", [640, 1024, 1536, 2048])))),
                photo_zoom_max_width=int(media.get("photo_zoom_max_width", 3500)),
                document_dzi_min_long_edge=int(media.get("document_dzi_min_long_edge", 4000)),
                dzi_tile_size=int(media.get("dzi_tile_size", 510)),
                pdf_render_dpi=int(media.get("pdf_render_dpi", 200)),
                publish_original_pdf=bool(media.get("publish_original_pdf", False)),
                webp_quality=int(media.get("webp_quality", 82)),
            ),
            publish=dict(publish),
        )
