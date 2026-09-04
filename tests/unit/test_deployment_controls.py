"""Regression tests for GitHub and strict SSH deployment controls."""

from pathlib import Path

import pytest

from scripts.validate_deployment_controls import (
    DeploymentControlError,
    validate_deployment_controls,
)


ROOT = Path(__file__).parents[2]


def test_repository_deployment_controls_are_valid():
    controls = validate_deployment_controls(ROOT)
    assert controls["main_branch"]["required_approving_review_count"] == 0
    assert controls["environments"]["production"]["deployment_branch_policies"] == [
        {"type": "tag", "name": "prod-v*"}
    ]
    assert controls["production_tag_lifecycle"] == {
        "successful_deployment_or_release": "permanently_immutable",
        "failed_unpublished_attempt": "manual_evidence_gated_retirement",
        "retirement_validator": "scripts/validate_failed_tag_retirement.py",
        "automatic_deletion_or_recreation": False,
    }


def test_compose_files_pin_their_deployment_environment_identity():
    expected = {
        "docker-compose.dev.yml": "APP_ENV: development",
        "docker-compose.test.yml": "APP_ENV: test",
        "docker-compose.guild.yml": "APP_ENV: production",
    }
    for name, identity in expected.items():
        source = (ROOT / name).read_text(encoding="utf-8")
        assert source.count(identity) == 1



def test_unpinned_action_is_rejected(tmp_path):
    for relative in (
        ".github/deployment-controls.json",
        "docker-compose.dev.yml",
        "docker-compose.test.yml",
        "docker-compose.guild.yml",
        "deploy/configure-deployment-ssh.sh",
        "deploy/run-strict-ssh.sh",
        "deploy/run-strict-scp.sh",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            (ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8"
        )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for workflow_name, environment in {
        "deploy-dev.yml": "development",
        "deploy-test.yml": "test",
        "deploy-prod.yml": "production",
    }.items():
        source = (ROOT / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8"
        )
        if workflow_name == "deploy-dev.yml":
            source += "\n      uses: example/unsafe@v1\n"
        (workflows / workflow_name).write_text(source, encoding="utf-8")
    (workflows / "pull-request-validation.yml").write_text(
        (ROOT / ".github" / "workflows" / "pull-request-validation.yml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    with pytest.raises(DeploymentControlError, match="not pinned to a full SHA"):
        validate_deployment_controls(tmp_path)


def test_known_host_trust_is_supplied_not_scanned():
    configure = (ROOT / "deploy" / "configure-deployment-ssh.sh").read_text(
        encoding="utf-8"
    )
    runner = (ROOT / "deploy" / "run-strict-ssh.sh").read_text(encoding="utf-8")
    copier = (ROOT / "deploy" / "run-strict-scp.sh").read_text(encoding="utf-8")
    assert "ssh-keyscan" not in configure
    assert "StrictHostKeyChecking=yes" in runner
    assert "ServerAliveInterval=30" in runner
    assert "ServerAliveCountMax=20" in runner
    assert "UserKnownHostsFile=" in runner
    assert "StrictHostKeyChecking=yes" in copier
    assert "UserKnownHostsFile=" in copier
