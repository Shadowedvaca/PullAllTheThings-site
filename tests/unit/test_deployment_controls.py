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


def test_unpinned_action_is_rejected(tmp_path):
    for relative in (
        ".github/deployment-controls.json",
        "deploy/configure-deployment-ssh.sh",
        "deploy/run-strict-ssh.sh",
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
    with pytest.raises(DeploymentControlError, match="not pinned to a full SHA"):
        validate_deployment_controls(tmp_path)


def test_known_host_trust_is_supplied_not_scanned():
    configure = (ROOT / "deploy" / "configure-deployment-ssh.sh").read_text(
        encoding="utf-8"
    )
    runner = (ROOT / "deploy" / "run-strict-ssh.sh").read_text(encoding="utf-8")
    assert "ssh-keyscan" not in configure
    assert "StrictHostKeyChecking=yes" in runner
    assert "UserKnownHostsFile=" in runner
