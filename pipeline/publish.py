"""Stage 5: publish (BRIEF.md). Syncs build/site/ to the remote docroot
over SFTP so only changed files go up.

Two backends, selected by `publish.backend` in config.yaml:

- **winscp** (default, matches BRIEF.md's native-Windows recommendation):
  WinSCP scripting (`winscp.com`, `synchronize remote`). Works against the
  brief's recommended dedicated SFTP-only, chrooted deploy account - the
  sync protocol never needs a remote shell. Flags checked against WinSCP's
  own docs (winscp.net/eng/docs/scriptcommand_synchronize and
  .../scriptcommand_open), not assumed from memory, 6 Oct 2026: `-preview`
  is the documented dry-run switch ("preview changes only, do not
  synchronize"); `-privatekey=`/`-hostkey=` are the documented `open`
  switches for unattended SSH-key auth.

- **rsync** (opt-in, Will's choice 6 Oct 2026, for anyone who'd rather not
  install WinSCP, and as a step toward a more cross-platform publish path
  than a Windows-only GUI tool's scripting interface): plain `rsync` over
  SSH. **Needs a real shell account, not the chrooted SFTP-only one** -
  the rsync protocol runs `rsync --server ...` as a remote command over
  the SSH session, which an SFTP-only `ForceCommand internal-sftp`
  account can never do (this is exactly BRIEF.md's own reason for ruling
  rsync out as the primary backend: "Plain rsync needs a shell on the
  server, which the SFTP-only chroot account won't have"). Broader blast
  radius than the chrooted account by design - see docs/deploy-setup.md
  and the loud warning `run()` attaches to every result when this backend
  is selected. Not currently installed on this machine (confirmed 6 Oct
  2026 - `rsync` isn't on PATH, isn't bundled with Windows or with plain
  Git for Windows); needs a separate install (Git for Windows' optional
  components, WSL, or a standalone build) before this backend can be used.

Dry run by default either way (BRIEF.md: "`--live` is needed to push.
Remote deletion only with an explicit flag"). This is the product's own
designed safety behaviour for Will's future day-to-day use - distinct from
CLAUDE.md's stronger rule that governs this coding session specifically:
Claude Code never invokes this (not even the dry run - it still opens a
real connection to compute the diff) against the real VPS without showing
Will the exact command and getting a yes first.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

BACKENDS = ("winscp", "rsync")
REQUIRED_KEYS_COMMON = ("host", "username", "remote_path", "private_key_path")

RSYNC_WARNING = (
    "rsync backend selected: this deploy account needs a REAL shell on the "
    "server (rsync runs 'rsync --server' remotely), not the SFTP-only "
    "chrooted account BRIEF.md recommends. That's a materially larger blast "
    "radius on a server that also hosts other live sites - a wrong "
    "remote_path combined with --delete can reach further than /gene. See "
    "docs/deploy-setup.md's rsync section before using this for real."
)


class PublishConfigError(ValueError):
    """config.yaml's publish: section is missing or incomplete."""


@dataclass
class PublishSettings:
    backend: str
    host: str
    port: int
    username: str
    remote_path: str
    private_key_path: str
    host_key: str  # winscp backend only; empty for rsync (uses known_hosts)

    @classmethod
    def from_config(cls, config: "Config") -> "PublishSettings":
        p = config.publish or {}
        backend = p.get("backend", "winscp")
        if backend not in BACKENDS:
            raise PublishConfigError(
                f"config.yaml's publish.backend must be one of {BACKENDS}, got {backend!r}")

        required = list(REQUIRED_KEYS_COMMON)
        if backend == "winscp":
            required.append("host_key")
        missing = [k for k in required if not p.get(k)]
        if missing:
            raise PublishConfigError(
                "config.yaml's publish: section is missing/empty for: "
                f"{', '.join(missing)}. Needed for backend {backend!r}: "
                f"{', '.join(required)} - see docs/deploy-setup.md.")
        return cls(
            backend=backend, host=p["host"], port=int(p.get("port", 22)),
            username=p["username"], remote_path=p["remote_path"],
            private_key_path=p["private_key_path"], host_key=p.get("host_key", ""),
        )


def build_winscp_command(settings: PublishSettings, local_dir: Path, *,
                          live: bool, delete: bool) -> list[str]:
    """`option batch abort` makes any confirmation prompt this script
    doesn't explicitly handle abort the run rather than silently
    proceeding; `option confirm off` is what lets ordinary
    overwrite-on-sync proceed without a prompt."""
    open_cmd = (f'open sftp://{settings.username}@{settings.host}:{settings.port}/ '
                f'-privatekey="{settings.private_key_path}" '
                f'-hostkey="{settings.host_key}"')
    sync_flags = ["-criteria=either"]
    if delete:
        sync_flags.append("-delete")
    if not live:
        sync_flags.append("-preview")
    sync_cmd = (f'synchronize remote {" ".join(sync_flags)} '
                f'"{local_dir}" "{settings.remote_path}"')
    return [
        "winscp.com", "/ini=nul", "/command",
        open_cmd,
        "option batch abort",
        "option confirm off",
        sync_cmd,
        "exit",
    ]


def build_rsync_command(settings: PublishSettings, local_dir: Path, *,
                         live: bool, delete: bool) -> list[str]:
    """Trailing slash on the source is deliberate rsync convention: it
    copies the *contents* of build/site/ into remote_path, not a nested
    build/site/ directory inside it. `-o BatchMode=yes` makes ssh fail
    instead of prompting if the key doesn't work; `StrictHostKeyChecking=yes`
    requires the host key to already be in known_hosts (accept it once,
    interactively, before the first real run - see docs/deploy-setup.md)
    rather than WinSCP's own `-hostkey=` fingerprint mechanism."""
    ssh_cmd = (f'ssh -i "{settings.private_key_path}" -p {settings.port} '
               f'-o StrictHostKeyChecking=yes -o BatchMode=yes')
    cmd = ["rsync", "-az", "--human-readable", "-e", ssh_cmd]
    if delete:
        cmd.append("--delete-after")
    if not live:
        cmd.append("--dry-run")
    local = str(local_dir).rstrip("/\\") + "/"
    remote = f'{settings.username}@{settings.host}:{settings.remote_path.rstrip("/")}/'
    cmd += [local, remote]
    return cmd


def _display(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def run(config: "Config", *, live: bool = False, delete: bool = False,
        execute: bool = False) -> dict:
    """Builds the sync command for the current build/site/, using whichever
    backend config.yaml's `publish.backend` selects (default "winscp").
    `execute` defaults to False: this call only ever constructs and returns
    the command for the caller to show the user - it does not touch the
    network unless the caller explicitly passes `execute=True`, which
    cli.py only does after the command has been printed. Nothing in this
    module decides on its own to connect to the VPS."""
    settings = PublishSettings.from_config(config)
    local_dir = config.site_dir
    if not local_dir.exists():
        raise FileNotFoundError(f"{local_dir} not found - run 'gedcomwright render' first")

    if settings.backend == "winscp":
        cmd = build_winscp_command(settings, local_dir, live=live, delete=delete)
        tool = "winscp.com"
    else:
        cmd = build_rsync_command(settings, local_dir, live=live, delete=delete)
        tool = "rsync"

    tool_path = shutil.which(tool)
    result = {
        "backend": settings.backend,
        "command": cmd,
        "command_display": _display(cmd),
        "live": live,
        "delete": delete,
        "tool_found": tool_path is not None,
        "tool_path": tool_path,
        "executed": False,
        "warning": RSYNC_WARNING if settings.backend == "rsync" else None,
    }
    if execute:
        if tool_path is None:
            raise FileNotFoundError(f"{tool} not found on PATH - install it first")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        result.update(executed=True, returncode=proc.returncode,
                      stdout=proc.stdout, stderr=proc.stderr)
    return result
