"""Unit coverage for bounded, fail-closed backup retention."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "deploy" / "patt-prune-backups.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("patt_backup_retention", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pair(directory: Path, sequence: int, *, sha_digit: str = "a") -> Path:
    module = _load_module()
    sha = sha_digit * 40
    archive = directory / f"patt_db_20260921T1200{sequence:02d}Z_{sha}.dump"
    archive.write_bytes(f"archive-{sequence}".encode())
    manifest = archive.with_name(f"{archive.name}.manifest")
    manifest.write_text(
        "\n".join(
            (
                "schema_version=1",
                f"deployment_sha={sha}",
                f"archive={archive}",
                f"archive_sha256={'b' * 64}",
                "restore_authority=explicit_required",
                "automatic_database_downgrade=false",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    assert module.ARCHIVE_PATTERN.fullmatch(archive.name)
    return archive


def test_plan_retains_newest_complete_pairs_and_expires_only_older_pairs(tmp_path):
    module = _load_module()
    archives = [_pair(tmp_path, sequence) for sequence in range(5)]

    retained, expired = module.plan_retention(
        tmp_path, retain=3, verified_archive=archives[-1]
    )

    assert [pair.archive for pair in retained] == list(reversed(archives[-3:]))
    assert [pair.archive for pair in expired] == list(reversed(archives[:2]))


@pytest.mark.parametrize("orphan_kind", ["archive", "manifest", "temporary"])
def test_plan_fails_closed_on_incomplete_or_unexpected_evidence(tmp_path, orphan_kind):
    module = _load_module()
    newest = _pair(tmp_path, 2)
    orphan = tmp_path / f"patt_db_20260921T120001Z_{'c' * 40}.dump"
    if orphan_kind == "archive":
        orphan.write_bytes(b"orphan")
    elif orphan_kind == "manifest":
        orphan.with_name(f"{orphan.name}.manifest").write_text(
            "orphan=true\n", encoding="utf-8"
        )
    else:
        orphan.with_name(f"{orphan.name}.tmp").write_bytes(b"partial")

    with pytest.raises(module.RetentionError):
        module.plan_retention(tmp_path, retain=1, verified_archive=newest)

    assert newest.exists()
    assert newest.with_name(f"{newest.name}.manifest").exists()


def test_dry_run_reports_exact_paths_without_deleting(tmp_path, capsys):
    module = _load_module()
    older = _pair(tmp_path, 1)
    newest = _pair(tmp_path, 2)
    retained, expired = module.plan_retention(
        tmp_path, retain=1, verified_archive=newest
    )

    module.apply_retention(
        expired, environment="development", retained_count=len(retained), dry_run=True
    )

    output = capsys.readouterr().out
    assert f"archive={older}" in output
    assert f"manifest={older}.manifest" in output
    assert "dry_run=true" in output
    assert older.exists()
    assert older.with_name(f"{older.name}.manifest").exists()


def test_apply_deletes_only_expired_complete_pairs(tmp_path):
    module = _load_module()
    older = _pair(tmp_path, 1)
    newest = _pair(tmp_path, 2)
    retained, expired = module.plan_retention(
        tmp_path, retain=1, verified_archive=newest
    )

    module.apply_retention(
        expired, environment="development", retained_count=len(retained), dry_run=False
    )

    assert not older.exists()
    assert not older.with_name(f"{older.name}.manifest").exists()
    assert newest.exists()
    assert newest.with_name(f"{newest.name}.manifest").exists()


def test_remote_deployment_prunes_only_after_success_gates_and_never_production():
    source = (ROOT / "deploy" / "patt-remote-deploy.sh").read_text(encoding="utf-8")
    migration = source.index("alembic current --check-heads")
    marker = source.index(".deployment/active-sha.tmp")
    retention = source.index("patt-prune-backups.py")
    completion = source.index("PATT_DEPLOYMENT_COMPLETE")

    assert migration < marker < retention < completion
    assert "retention_count=3" in source
    assert "retention_count=7" in source
    assert "retention_count=0" in source
    assert "if ((retention_count > 0))" in source


def test_retention_cli_refuses_production_before_inspecting_backups():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--environment",
            "production",
            "--verified-archive",
            "/opt/backups/patt-db/production/not-used.dump",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Production backup retention is never automatic" in result.stderr
