"""Regression contracts for fail-closed remote deployment execution."""

from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOWS = (
    "deploy-dev.yml",
    "deploy-test.yml",
    "deploy-prod.yml",
)
REMOTE_SCRIPT = ROOT / "deploy" / "patt-remote-deploy.sh"


def test_deployment_program_is_not_streamed_on_child_process_stdin():
    """The former heredoc was consumed by Docker Buildx after checkout/build."""

    for name in WORKFLOWS:
        source = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "<<'REMOTE'" not in source
        assert '<<"REMOTE"' not in source
        assert "deploy/patt-remote-deploy.sh" in source
        assert "PATT_DEPLOYMENT_COMPLETE" in source
        assert "grep -Fqx" in source


def test_deployment_bundle_transport_is_strict_and_exact():
    for name in WORKFLOWS:
        source = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        remote = source[source.index("set -eu; export GIT_CONFIG_GLOBAL") :]
        assert "GIT_CONFIG_GLOBAL=/dev/null" in remote
        assert "GIT_CONFIG_SYSTEM=/dev/null" in remote
        assert "GIT_TERMINAL_PROMPT=0" in remote
        assert "git bundle create" in source
        assert "git bundle verify" in source
        assert "deploy/run-strict-scp.sh" in source
        assert "patt-deployment-$DEPLOY_SHA.bundle" in source
        assert (
            "git fetch --no-tags '/tmp/patt-deployment-$DEPLOY_SHA.bundle' HEAD"
            in remote
        )
        assert "$GITHUB_SERVER_URL" not in remote
        assert "git cat-file -e '$DEPLOY_SHA^{commit}'" in remote
        assert "git checkout --detach '$DEPLOY_SHA'" in remote
        assert remote.index("GIT_CONFIG_GLOBAL=/dev/null") < remote.index("git ")
        assert remote.index("GIT_CONFIG_SYSTEM=/dev/null") < remote.index("git ")
        assert remote.index("GIT_TERMINAL_PROMPT=0") < remote.index("git ")


def test_strict_bundle_copier_rejects_unbounded_destinations():
    source = (ROOT / "deploy" / "run-strict-scp.sh").read_text(encoding="utf-8")
    assert "usage:" in source
    assert "^/tmp/patt-deployment-[0-9a-f]{40}" in source
    assert "StrictHostKeyChecking=yes" in source
    assert "ServerAliveInterval=30" in source
    assert "ServerAliveCountMax=20" in source
    assert "BatchMode=yes" in source
    assert "IdentitiesOnly=yes" in source
    assert "-F /dev/null" in source


def test_remote_program_detaches_child_stdin_and_orders_every_gate():
    source = REMOTE_SCRIPT.read_text(encoding="utf-8")
    build = source.index('build "$app_service" </dev/null')
    backup = source.index("patt-predeploy-backup.sh")
    start = source.index('up -d "$app_service" </dev/null')
    health = source.index("/api/health")
    migration = source.index("alembic current --check-heads")
    marker = source.index(".deployment/active-sha.tmp")
    sentinel = source.index("PATT_DEPLOYMENT_COMPLETE")
    assert build < backup < start < health < migration < marker < sentinel
    assert "--database-env POSTGRES_DB" in source
    assert "--user-env POSTGRES_USER" in source
    assert "--database guild_db" not in source
    assert "--user guild_user" not in source


def test_completion_requires_backup_and_rollback_evidence():
    source = REMOTE_SCRIPT.read_text(encoding="utf-8")
    assert 'grep -Fq "Verified pre-deployment backup:"' in source
    assert 'grep -Fq "Rollback manifest:"' in source
    assert 'test "$(cat .deployment/active-sha)" = "$deployment_sha"' in source
