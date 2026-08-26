"""Unit and contract tests for the bounded database-recovery controls."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "rehearse_database_recovery.py"
BACKUP_SCRIPT = ROOT / "deploy" / "patt-predeploy-backup.sh"
PR_WORKFLOW = ROOT / ".github" / "workflows" / "pull-request-validation.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("database_recovery", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_restore_database_must_use_bounded_namespace():
    module = _load_module()
    module.validate_restore_database("guild_migration_db", "patt_recovery_ci")
    with pytest.raises(ValueError, match="bounded"):
        module.validate_restore_database("guild_migration_db", "guild_prod")


def test_restore_database_cannot_be_source():
    module = _load_module()
    with pytest.raises(ValueError, match="differ"):
        module.validate_restore_database("patt_recovery_ci", "patt_recovery_ci")


def test_fingerprint_is_stable_across_input_order():
    module = _load_module()
    first = module.stable_fingerprint_payload(
        heads=["0182"],
        tables=["patt.recovery_rehearsal_probe", "common.users"],
        probe_rows=[(2, "omega"), (1, "alpha")],
    )
    second = module.stable_fingerprint_payload(
        heads=["0182"],
        tables=["common.users", "patt.recovery_rehearsal_probe"],
        probe_rows=[(1, "alpha"), (2, "omega")],
    )
    assert first == second


def test_pg_connection_keeps_password_out_of_command_arguments():
    module = _load_module()
    connection = module._connection(  # noqa: SLF001
        "postgresql+asyncpg://patt:synthetic-password@127.0.0.1:5433/source"
    )
    assert "synthetic-password" not in " ".join(connection.command_args())
    assert connection.environment()["PGPASSWORD"] == "synthetic-password"


def test_predeploy_backup_is_atomic_verified_and_non_destructive():
    source = BACKUP_SCRIPT.read_text(encoding="utf-8")
    assert "--format=custom" in source
    assert "pg_restore --list" in source
    assert "sha256sum" in source
    assert 'mv -- "$archive_tmp" "$archive"' in source
    assert "restore_authority=explicit_required" in source
    assert "automatic_database_downgrade=false" in source
    assert "Refusing to overwrite existing backup evidence" in source
    assert "pg_restore --dbname" not in source
    assert "DROP DATABASE" not in source


@pytest.mark.parametrize(
    "workflow_name",
    [
        "deploy-dev.yml",
        "deploy-test.yml",
        "deploy-prod.yml",
    ],
)
def test_deployment_requires_verified_backup_before_container_start(
    workflow_name: str,
):
    workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text(
        encoding="utf-8"
    )
    remote = (ROOT / "deploy" / "patt-remote-deploy.sh").read_text(encoding="utf-8")
    assert "deploy/patt-remote-deploy.sh" in workflow
    assert "PATT_DEPLOYMENT_COMPLETE" in workflow
    assert remote.index("patt-predeploy-backup.sh") < remote.index(" up -d ")
    assert ".deployment/active-sha" in remote
    assert remote.index("alembic current --check-heads") < remote.index(
        ".deployment/active-sha.tmp"
    )


def test_pr_workflow_requires_restore_and_fresh_container_identity():
    source = PR_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/rehearse_database_recovery.py" in source
    assert "docker-compose.recovery.yml" in source
    assert 'data["data"]["environment"] == "recovery"' in source
    assert "alembic current --check-heads" in source
    assert "--database patt_recovery" in source
    assert "--backup-dir artifacts/predeploy" in source
    assert "artifacts/predeploy" in source
