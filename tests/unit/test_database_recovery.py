"""Unit and contract tests for the bounded database-recovery controls."""

from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "rehearse_database_recovery.py"
BACKUP_SCRIPT = ROOT / "deploy" / "patt-predeploy-backup.sh"
PR_WORKFLOW = ROOT / ".github" / "workflows" / "pull-request-validation.yml"
LEGACY_IDENTITY_SCRIPT = ROOT / "scripts" / "reproduce_legacy_database_identity.sh"


def _bash_path(path: Path) -> str:
    """Return a path Bash can consume when Windows Python runs from WSL UNC."""
    value = path.as_posix()
    if value.lower().startswith("//wsl.localhost/"):
        parts = value.split("/", 4)
        if len(parts) == 5:
            return f"/{parts[4]}"
    return value


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


def test_predeploy_backup_uses_composed_application_database_identity(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker_log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_bytes(
        b"""#!/bin/sh
printf '%s\\n' "$*" >> "$PATT_DOCKER_LOG"
case "$*" in
  *" config --format json") printf '%s' '{"services":{"app-prod":{"environment":{"DATABASE_URL":"postgresql+asyncpg://legacy_owner:synthetic-password@db-prod:5432/legacy_db"}}}}' ;;
  *" pg_dump --username legacy_owner --dbname legacy_db "*) printf alembic_version ;;
  *" pg_restore --list") cat >/dev/null; printf 'TABLE patt alembic_version\\n' ;;
  *" psql --username legacy_owner --dbname legacy_db "*) printf '0182\\n' ;;
  *) exit 9 ;;
esac
""",
    )
    docker.chmod(0o755)
    sha = "a" * 40
    environment = os.environ.copy()
    bash = shutil.which("bash") or "bash"
    script_args = [
        _bash_path(BACKUP_SCRIPT),
        "--compose-file",
        _bash_path(compose),
        "--db-service",
        "db-prod",
        "--database-url-service",
        "app-prod",
        "--database-url-env",
        "DATABASE_URL",
        "--backup-dir",
        _bash_path(tmp_path / "backups"),
        "--previous-sha",
        sha,
        "--deployment-sha",
        sha,
    ]

    shell_command = (
        f"chmod 700 {shlex.quote(_bash_path(docker))}; "
        f"export PATH={shlex.quote(_bash_path(fake_bin))}:\"$PATH\"; "
        f"export PATT_DOCKER_LOG={shlex.quote(_bash_path(docker_log))}; "
        "exec bash " + " ".join(shlex.quote(value) for value in script_args)
    )
    command = [bash, "-c", shell_command]
    if BACKUP_SCRIPT.as_posix().lower().startswith("//wsl.localhost/"):
        wsl = shutil.which("wsl")
        assert wsl is not None
        command = [wsl, "--", "bash", "-c", shell_command]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    commands = docker_log.read_text(encoding="utf-8")
    assert "config --format json" in commands
    assert "printenv POSTGRES_DB" not in commands
    assert "printenv POSTGRES_USER" not in commands
    assert "synthetic-password" not in commands
    assert "synthetic-password" not in result.stdout
    assert "pg_dump --username legacy_owner --dbname legacy_db" in commands
    assert "psql --username legacy_owner --dbname legacy_db" in commands
    assert "Verified pre-deployment backup:" in result.stdout
    assert "Rollback manifest:" in result.stdout


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
    assert ".deployment/pending-previous-sha" in workflow
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
    assert "scripts/reproduce_legacy_database_identity.sh" in source
    assert "artifacts/predeploy" in source


def test_legacy_identity_regression_uses_real_compose_backup_wrapper():
    source = LEGACY_IDENTITY_SCRIPT.read_text(encoding="utf-8")
    workflow = PR_WORKFLOW.read_text(encoding="utf-8")
    assert "patt-predeploy-backup.sh" in source
    assert "--database guild_db" in source
    assert "--user guild_user" in source
    assert "SELECT count(*) FROM pg_roles WHERE rolname = 'guild_user'" in source
    assert "SELECT count(*) FROM pg_database WHERE datname = 'guild_db'" in source
    assert "hard_coded_status" in source
    assert "RECOVERY_POSTGRES_DB=guild_db" in source
    assert "RECOVERY_POSTGRES_USER=guild_user" in source
    assert '--database-url-env DATABASE_URL' in source
    assert '--database-url-service "$app_service"' in source
    assert "database=patt_recovery" in source
    assert "alembic_revision=0182" in source
    assert workflow.index("docker-compose.recovery.yml up -d") < workflow.rindex(
        "scripts/reproduce_legacy_database_identity.sh"
    )
