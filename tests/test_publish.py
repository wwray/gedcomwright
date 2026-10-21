"""Tests for pipeline.publish (BRIEF.md Stage 5). Only command
construction and config validation are tested - nothing here ever invokes
subprocess or touches a network; `run()`'s `execute` stays False in every
test."""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.publish import (
    PublishConfigError, PublishSettings, build_rsync_command, build_winscp_command, run,
)


class _FakeConfig:
    def __init__(self, publish: dict, site_dir: Path):
        self.publish = publish
        self.site_dir = site_dir


FULL_SETTINGS = {
    "host": "example.com",
    "username": "gwdeploy",
    "remote_path": "/var/www/html/gene",
    "private_key_path": r"C:\Users\you\.ssh\gwdeploy.ppk",
    "host_key": "ssh-ed25519 255 abcdef...",
}


def test_from_config_requires_all_keys():
    config = _FakeConfig(publish={"host": "example.com"}, site_dir=Path("."))
    with pytest.raises(PublishConfigError) as exc:
        PublishSettings.from_config(config)
    msg = str(exc.value)
    assert "username" in msg and "remote_path" in msg and "host_key" in msg


def test_from_config_empty_publish_section_raises():
    config = _FakeConfig(publish={}, site_dir=Path("."))
    with pytest.raises(PublishConfigError):
        PublishSettings.from_config(config)


def test_from_config_accepts_full_settings_and_defaults_port():
    config = _FakeConfig(publish=FULL_SETTINGS, site_dir=Path("."))
    settings = PublishSettings.from_config(config)
    assert settings.host == "example.com"
    assert settings.port == 22


def test_from_config_honours_explicit_port():
    config = _FakeConfig(publish={**FULL_SETTINGS, "port": 2222}, site_dir=Path("."))
    assert PublishSettings.from_config(config).port == 2222


def test_build_command_dry_run_includes_preview_not_delete():
    settings = PublishSettings.from_config(_FakeConfig(publish=FULL_SETTINGS, site_dir=Path(".")))
    cmd = build_winscp_command(settings, Path("build/site"), live=False, delete=False)
    sync_line = next(c for c in cmd if c.startswith("synchronize"))
    assert "-preview" in sync_line
    assert "-delete" not in sync_line


def test_build_command_live_omits_preview():
    settings = PublishSettings.from_config(_FakeConfig(publish=FULL_SETTINGS, site_dir=Path(".")))
    cmd = build_winscp_command(settings, Path("build/site"), live=True, delete=False)
    sync_line = next(c for c in cmd if c.startswith("synchronize"))
    assert "-preview" not in sync_line


def test_build_command_delete_flag_only_with_explicit_request():
    settings = PublishSettings.from_config(_FakeConfig(publish=FULL_SETTINGS, site_dir=Path(".")))
    cmd = build_winscp_command(settings, Path("build/site"), live=True, delete=True)
    sync_line = next(c for c in cmd if c.startswith("synchronize"))
    assert "-delete" in sync_line


def test_build_command_uses_privatekey_and_hostkey_switches():
    settings = PublishSettings.from_config(_FakeConfig(publish=FULL_SETTINGS, site_dir=Path(".")))
    cmd = build_winscp_command(settings, Path("build/site"), live=False, delete=False)
    open_line = next(c for c in cmd if c.startswith("open"))
    assert "-privatekey=" in open_line
    assert "-hostkey=" in open_line
    assert "gwdeploy@example.com:22" in open_line


def test_run_never_executes_without_explicit_opt_in(tmp_path):
    site_dir = tmp_path / "build" / "site"
    site_dir.mkdir(parents=True)
    config = _FakeConfig(publish=FULL_SETTINGS, site_dir=site_dir)
    result = run(config, live=False, delete=False)  # execute defaults False
    assert result["executed"] is False
    assert "returncode" not in result


def test_run_raises_if_site_dir_missing(tmp_path):
    config = _FakeConfig(publish=FULL_SETTINGS, site_dir=tmp_path / "nonexistent")
    with pytest.raises(FileNotFoundError):
        run(config)


def test_run_raises_if_publish_not_configured(tmp_path):
    site_dir = tmp_path / "build" / "site"
    site_dir.mkdir(parents=True)
    config = _FakeConfig(publish={}, site_dir=site_dir)
    with pytest.raises(PublishConfigError):
        run(config)


def test_run_defaults_to_winscp_backend_with_no_warning(tmp_path):
    site_dir = tmp_path / "build" / "site"
    site_dir.mkdir(parents=True)
    config = _FakeConfig(publish=FULL_SETTINGS, site_dir=site_dir)
    result = run(config)
    assert result["backend"] == "winscp"
    assert result["warning"] is None


# --- rsync backend (Will's choice, 6 Oct 2026: opt-in, with a warning -
# needs a real shell account, not the chrooted SFTP-only one) -------------

RSYNC_SETTINGS = {
    "backend": "rsync",
    "host": "example.com",
    "username": "someuser",
    "remote_path": "/var/www/html/gene",
    "private_key_path": r"C:\Users\you\.ssh\id_ed25519",
}


def test_rsync_backend_does_not_require_host_key():
    config = _FakeConfig(publish=RSYNC_SETTINGS, site_dir=Path("."))
    settings = PublishSettings.from_config(config)
    assert settings.backend == "rsync"
    assert settings.host_key == ""


def test_rsync_backend_still_requires_the_common_keys():
    config = _FakeConfig(publish={"backend": "rsync", "host": "example.com"}, site_dir=Path("."))
    with pytest.raises(PublishConfigError) as exc:
        PublishSettings.from_config(config)
    msg = str(exc.value)
    assert "username" in msg and "remote_path" in msg and "private_key_path" in msg
    assert "host_key" not in msg


def test_unknown_backend_rejected():
    config = _FakeConfig(publish={**RSYNC_SETTINGS, "backend": "ftp"}, site_dir=Path("."))
    with pytest.raises(PublishConfigError):
        PublishSettings.from_config(config)


def test_build_rsync_command_dry_run_by_default():
    settings = PublishSettings.from_config(_FakeConfig(publish=RSYNC_SETTINGS, site_dir=Path(".")))
    cmd = build_rsync_command(settings, Path("build/site"), live=False, delete=False)
    assert cmd[0] == "rsync"
    assert "--dry-run" in cmd
    assert "--delete-after" not in cmd


def test_build_rsync_command_live_omits_dry_run():
    settings = PublishSettings.from_config(_FakeConfig(publish=RSYNC_SETTINGS, site_dir=Path(".")))
    cmd = build_rsync_command(settings, Path("build/site"), live=True, delete=False)
    assert "--dry-run" not in cmd


def test_build_rsync_command_delete_only_with_explicit_request():
    settings = PublishSettings.from_config(_FakeConfig(publish=RSYNC_SETTINGS, site_dir=Path(".")))
    cmd = build_rsync_command(settings, Path("build/site"), live=True, delete=True)
    assert "--delete-after" in cmd


def test_build_rsync_command_source_has_trailing_slash_for_contents_not_nesting():
    settings = PublishSettings.from_config(_FakeConfig(publish=RSYNC_SETTINGS, site_dir=Path(".")))
    cmd = build_rsync_command(settings, Path("build/site"), live=True, delete=False)
    source = cmd[-2]
    assert source.endswith("/")


def test_build_rsync_command_uses_ssh_private_key_switch():
    settings = PublishSettings.from_config(_FakeConfig(publish=RSYNC_SETTINGS, site_dir=Path(".")))
    cmd = build_rsync_command(settings, Path("build/site"), live=True, delete=False)
    ssh_arg = cmd[cmd.index("-e") + 1]
    assert "-i" in ssh_arg and RSYNC_SETTINGS["private_key_path"] in ssh_arg


def test_run_rsync_backend_attaches_a_warning(tmp_path):
    site_dir = tmp_path / "build" / "site"
    site_dir.mkdir(parents=True)
    config = _FakeConfig(publish=RSYNC_SETTINGS, site_dir=site_dir)
    result = run(config)
    assert result["backend"] == "rsync"
    assert "shell" in result["warning"]
    assert "chroot" in result["warning"] or "SFTP-only" in result["warning"]
