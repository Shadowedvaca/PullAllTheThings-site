"""Validate repository-side GitHub and strict SSH deployment contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path


CONTROL_PATH = Path(".github/deployment-controls.json")
DEPLOYMENTS = {
    "deploy-dev.yml": "development",
    "deploy-test.yml": "test",
    "deploy-prod.yml": "production",
}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class DeploymentControlError(ValueError):
    """Raised when a repository deployment control is incomplete or unsafe."""


def validate_deployment_controls(repository_root: Path) -> dict:
    root = repository_root.resolve()
    controls = json.loads((root / CONTROL_PATH).read_text(encoding="utf-8"))
    errors: list[str] = []

    if controls.get("schema_version") != 1:
        errors.append("deployment control schema_version must equal 1")
    actions = controls.get("actions", {})
    if actions != {
        "enabled": True,
        "allowed_actions": "all",
        "sha_pinning_required": True,
    }:
        errors.append("Actions must remain enabled with full-SHA pinning required")

    main = controls.get("main_branch", {})
    expected_main = {
        "require_pull_request": True,
        "required_approving_review_count": 0,
        "required_status_checks": ["Quality, migrations, tests, and build"],
        "strict_status_checks": True,
        "enforce_admins": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": True,
    }
    if main != expected_main:
        errors.append(
            "main protection must match the owner-approved no-extra-review gate"
        )

    environments = controls.get("environments", {})
    if set(environments) != set(DEPLOYMENTS.values()):
        errors.append(
            "deployment environments must be development, test, and production"
        )
    if environments.get("development", {}).get("deployment_policy") != "all":
        errors.append("development must allow explicitly dispatched branch commits")
    if environments.get("test", {}).get("deployment_policy") != "protected_branches":
        errors.append("test must accept only protected branches")
    production = environments.get("production", {})
    if production.get("deployment_branch_policies") != [
        {"type": "tag", "name": "prod-v*"}
    ]:
        errors.append("production must accept only prod-v* tags")
    for name, environment in environments.items():
        if environment.get("required_reviewers") != 0:
            errors.append(f"{name} must not invent an extra deployment approval")
        if environment.get("wait_timer_minutes") != 0:
            errors.append(f"{name} must not add an unselected wait timer")

    if controls.get("environment_secret_names") != [
        "DEPLOY_HOST",
        "DEPLOY_KNOWN_HOSTS",
        "DEPLOY_SSH_KEY",
    ]:
        errors.append("environment secret-name contract is incomplete")
    if controls.get("environment_variable_names") != ["DEPLOY_USER"]:
        errors.append("environment variable-name contract is incomplete")

    workflows = root / ".github" / "workflows"
    for path in workflows.glob("*.yml"):
        source = path.read_text(encoding="utf-8")
        for target in re.findall(r"^\s*uses:\s*([^\s#]+)", source, re.MULTILINE):
            if target.startswith("./"):
                continue
            _, separator, revision = target.rpartition("@")
            if not separator or not FULL_SHA.fullmatch(revision):
                errors.append(
                    f"{path.name} has an action not pinned to a full SHA: {target}"
                )

    forbidden = ("appleboy/ssh-action", "DEV_HOST", "TEST_HOST", "PROD_HOST")
    for workflow_name, environment in DEPLOYMENTS.items():
        source = (workflows / workflow_name).read_text(encoding="utf-8")
        if f"environment: {environment}" not in source:
            errors.append(f"{workflow_name} must use the {environment} environment")
        for token in forbidden:
            if token in source:
                errors.append(
                    f"{workflow_name} retains forbidden shared SSH token {token}"
                )
        if "<<'REMOTE'" in source or '<<"REMOTE"' in source:
            errors.append(
                f"{workflow_name} must not stream deployment source over SSH stdin"
            )
        for token in (
            "secrets.DEPLOY_HOST",
            "secrets.DEPLOY_KNOWN_HOSTS",
            "secrets.DEPLOY_SSH_KEY",
            "vars.DEPLOY_USER",
            "deploy/configure-deployment-ssh.sh",
            "deploy/run-strict-ssh.sh",
            "deploy/patt-remote-deploy.sh",
            "PATT_DEPLOYMENT_COMPLETE",
            "GIT_CONFIG_GLOBAL=/dev/null",
            "GIT_CONFIG_SYSTEM=/dev/null",
            "GIT_TERMINAL_PROMPT=0",
            "deploy/run-strict-scp.sh",
            "git bundle create",
            "git bundle verify",
            "patt-deployment-$DEPLOY_SHA.bundle",
        ):
            if token not in source:
                errors.append(f"{workflow_name} is missing {token}")

    compose_environments = {
        "docker-compose.dev.yml": "APP_ENV: development",
        "docker-compose.test.yml": "APP_ENV: test",
        "docker-compose.guild.yml": "APP_ENV: production",
    }
    for compose_name, expected_environment in compose_environments.items():
        source = (root / compose_name).read_text(encoding="utf-8")
        if expected_environment not in source:
            errors.append(f"{compose_name} is missing {expected_environment}")

    development_source = (workflows / "deploy-dev.yml").read_text(encoding="utf-8")
    for token in ("DEPLOY_REF", "git check-ref-format --branch"):
        if token not in development_source:
            errors.append(f"deploy-dev.yml is missing {token}")

    configure = (root / "deploy" / "configure-deployment-ssh.sh").read_text(
        encoding="utf-8"
    )
    runner = (root / "deploy" / "run-strict-ssh.sh").read_text(encoding="utf-8")
    copier = (root / "deploy" / "run-strict-scp.sh").read_text(encoding="utf-8")
    if "ssh-keyscan" in configure:
        errors.append(
            "known-host trust must not be learned from the deployment connection"
        )
    for source_name, transport in (("SSH runner", runner), ("SCP runner", copier)):
        for token in (
            "StrictHostKeyChecking=yes",
            "UserKnownHostsFile=",
            "BatchMode=yes",
            "IdentitiesOnly=yes",
            "-F /dev/null",
        ):
            if token not in transport:
                errors.append(f"strict {source_name} is missing {token}")

    if errors:
        raise DeploymentControlError("; ".join(errors))
    return controls


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    validate_deployment_controls(root)
    print("Repository deployment controls valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
