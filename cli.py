#!/usr/bin/env python
"""gedcomwright CLI (BRIEF.md: "The CLI entry point is `gedcomwright`,
e.g. `gedcomwright build`, `gedcomwright publish --live`").

`extract`, `lint`, `media` and `render` are implemented (Milestones 1, 1a,
2, 3). The other subcommands are listed so `gedcomwright --help` is honest
about what exists, but each one exits with a clear "not implemented yet"
rather than pretending to work.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.audit import run as audit_run
from pipeline.audit import write_report as write_audit_report
from pipeline.config import Config, ConfigError
from pipeline.extract import run as extract_run
from pipeline.lint import run as lint_run
from pipeline.media import run as media_run
from pipeline.publish import PublishConfigError
from pipeline.publish import run as publish_run
from pipeline.render import copy_static, render_person_page, render_site

_NOT_YET = {
    "build": "once it's worth wrapping extract+media+render+audit in one command",
}


def _cmd_extract(args: argparse.Namespace) -> int:
    try:
        config = Config.load(Path(args.config))
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        summary = extract_run(config)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"GEDCOM: {summary['gedcom_path']}")
    print(f"people: {summary['people_total']} total, "
          f"{summary['people_published']} published, "
          f"{summary['people_hidden']} hidden")
    print(f"sources used: {summary['sources_used']}")
    print(f"media used: {summary['media_used']}, withheld: {summary['media_withheld']}")
    if summary["unparseable_dates"]:
        print(f"unparseable dates: {summary['unparseable_dates']} "
              "(see build/reports/privacy.md)")
    if summary["generation_conflicts"]:
        print(f"generation conflicts: {summary['generation_conflicts']} "
              "(see build/reports/privacy.md)")
    print(f"wrote {config.data_dir}/people.json, sources.json, media_links.json")
    print(f"wrote {config.reports_dir}/privacy.md")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    try:
        config = Config.load(Path(args.config))
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        result = lint_run(config)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    for section in result.sections:
        print(f"{section.title}: {section.count}")
    print(f"total: {result.total}")
    print(f"wrote {config.reports_dir}/lint.md")
    return 0


def _cmd_media(args: argparse.Namespace) -> int:
    try:
        config = Config.load(Path(args.config))
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        summary = media_run(config, limit=args.limit)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"processed: {summary['processed']} "
          f"({summary['photos']} photos, {summary['documents']} documents, "
          f"{summary['pdf_pages']} PDF pages)")
    print(f"reused from cache: {summary['cached']}")
    if summary["unresolved"]:
        print(f"unresolved: {summary['unresolved']} (see build/reports/media.md)")
    if summary["not_published"]:
        print(f"not published (.htm/.doc/.docx): {summary['not_published']} "
              "(see build/reports/media.md)")
    print(f"wrote {config.data_dir}/media.json")
    print(f"wrote {config.reports_dir}/media.md")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    """BRIEF.md Milestone 3: "Show me one rendered person page as a design
    proposal before applying it across the site" (`--person <id>`), "then
    the full site with search" (no `--person`: every published person,
    plus the surname index, home, about and 404 pages, plus a Pagefind
    search index over all of them)."""
    try:
        config = Config.load(Path(args.config))
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.person:
        try:
            out_path = render_person_page(args.person, config)
        except KeyError:
            print(f"error: no published person with id {args.person!r} in "
                  f"{config.data_dir}/people.json", file=sys.stderr)
            return 1
        copy_static(config)
        print(f"wrote {out_path}")
        return 0

    try:
        summary = render_site(config)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"rendered {summary['written']} of {summary['total']} person pages")
    print(f"rendered {summary['surnames']} surname pages")
    print("wrote home, about and 404 pages")
    if summary["sitemap_urls"] is None:
        print("skipped sitemap.xml (site.domain is not set)")
    else:
        print(f"wrote {config.site_dir / 'sitemap.xml'} ({summary['sitemap_urls']} URLs)")
    print("built search index (build/site/pagefind/)")
    if summary["errors"]:
        print(f"errors: {len(summary['errors'])}", file=sys.stderr)
        for person_id, msg in summary["errors"][:20]:
            print(f"  {person_id}: {msg}", file=sys.stderr)
        if len(summary["errors"]) > 20:
            print(f"  ... and {len(summary['errors']) - 20} more", file=sys.stderr)
        return 1
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    """BRIEF.md Stage 4: independently re-verifies the already-built
    output; exits non-zero if any check fails. Read-only - run 'extract',
    'media' and 'render' first."""
    try:
        config = Config.load(Path(args.config))
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        result = audit_run(config)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    write_audit_report(result, config.reports_dir / "audit.md")
    s = result.summary
    print(f"people: {s['people_published']} published, "
          f"withheld by group: {s['people_withheld_by_group']}")
    print(f"media: {s['media_published']} published, {s['media_withheld']} withheld, "
          f"{len(s['missing_media_files'])} missing")
    print(f"wrote {config.reports_dir}/audit.md")
    if not result.ok:
        print(f"AUDIT FAILED: {result.total_failures} issue(s)", file=sys.stderr)
        for key, items in result.failures.items():
            for item in items[:10]:
                print(f"  [{key}] {item}", file=sys.stderr)
            if len(items) > 10:
                print(f"  [{key}] ... and {len(items) - 10} more", file=sys.stderr)
        return 1
    print("audit passed")
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    """BRIEF.md Stage 5: dry run by default (shows what would change,
    uploads nothing); `--live` pushes for real; `--delete` allows removing
    remote files no longer present locally. Backend (winscp/rsync) is
    config.yaml's `publish.backend`."""
    try:
        config = Config.load(Path(args.config))
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        result = publish_run(config, live=args.live, delete=args.delete, execute=True)
    except (PublishConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if result["warning"]:
        print(f"WARNING: {result['warning']}", file=sys.stderr)
    print(f"backend: {result['backend']}")
    print(f"command: {result['command_display']}")
    print(result["stdout"])
    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)
    if result["returncode"] != 0:
        print(f"{result['backend']} exited {result['returncode']}", file=sys.stderr)
        return 1
    if not result["live"]:
        print("(dry run - nothing was uploaded; re-run with --live to push)")
    return 0


def _cmd_not_yet(name: str):
    def _run(args: argparse.Namespace) -> int:
        print(f"'{name}' isn't implemented yet (coming at {_NOT_YET[name]}).",
              file=sys.stderr)
        return 2
    return _run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gedcomwright")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="GEDCOM -> normalized JSON + privacy report")
    p_extract.set_defaults(func=_cmd_extract)

    p_lint = sub.add_parser("lint", help="read-only tree-quality report (build/reports/lint.md)")
    p_lint.set_defaults(func=_cmd_lint)

    p_media = sub.add_parser("media", help="generate derivatives + build/data/media.json")
    p_media.add_argument("--limit", type=int, default=None,
                          help="process only the first N media items (testing only)")
    p_media.set_defaults(func=_cmd_media)

    p_render = sub.add_parser("render", help="design-proposal stage: renders one person page")
    p_render.add_argument("--person", default=None, help="person id, e.g. I1045")
    p_render.set_defaults(func=_cmd_render)

    p_audit = sub.add_parser("audit", help="verify build/site/ independently; non-zero exit on failure")
    p_audit.set_defaults(func=_cmd_audit)

    p_publish = sub.add_parser("publish", help="sync build/site/ to the VPS over SFTP; dry run by default")
    p_publish.add_argument("--live", action="store_true", help="actually push (default: dry run / preview)")
    p_publish.add_argument("--delete", action="store_true",
                            help="also remove remote files no longer present locally")
    p_publish.set_defaults(func=_cmd_publish)

    for name in _NOT_YET:
        p = sub.add_parser(name, help=f"not implemented yet ({_NOT_YET[name]})")
        p.set_defaults(func=_cmd_not_yet(name))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
