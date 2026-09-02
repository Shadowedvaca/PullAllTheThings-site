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


def test_remote_git_operations_ignore_host_configuration_and_cannot_prompt():
    for name in WORKFLOWS:
        source = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        remote = source[source.index("set -eu; export GIT_CONFIG_GLOBAL") :]
        assert "GIT_CONFIG_GLOBAL=/dev/null" in remote
        assert "GIT_CONFIG_SYSTEM=/dev/null" in remote
        assert "GIT_TERMINAL_PROMPT=0" in remote
        assert "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY.git" in remote
        assert "mktemp -d /tmp/patt-fetch.XXXXXX" in remote
        assert "init --bare" in remote
        assert remote.index("GIT_CONFIG_GLOBAL=/dev/null") < remote.index("git ")
        assert remote.index("GIT_CONFIG_SYSTEM=/dev/null") < remote.index("git ")
        assert remote.index("GIT_TERMINAL_PROMPT=0") < remote.index("git ")

    development = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text(
        encoding="utf-8"
    )
    assert "git check-ref-format --branch" in development
    assert "refs/heads/$DEPLOY_REF:refs/heads/patt-deploy" in development


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


def test_completion_requires_backup_and_rollback_evidence():
    source = REMOTE_SCRIPT.read_text(encoding="utf-8")
    assert 'grep -Fq "Verified pre-deployment backup:"' in source
    assert 'grep -Fq "Rollback manifest:"' in source
    assert 'test "$(cat .deployment/active-sha)" = "$deployment_sha"' in source
