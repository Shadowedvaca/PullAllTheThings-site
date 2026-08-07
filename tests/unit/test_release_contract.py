"""Version, runtime identity, and release-foundation contract tests."""

import json
from pathlib import Path

import pytest

from guild_portal.version import APP_VERSION
from scripts.validate_production_readiness import (
    ProductionReadinessError,
    validate_production_readiness,
)
from scripts.validate_release import ReleaseValidationError, validate_release
from scripts.validate_test_provenance import (
    ProvenanceValidationError,
    validate_test_provenance,
)


REQUIRED_NOTES = """# Pull All The Things {version}

## Highlights

- A concrete outcome.

## Fixes/Changes

- A concrete change.

## Validation

- Automated checks passed.

## Deployment/Migrations

- No migration is required.

## Rollback

- Redeploy the prior tag.

## Known Limitations

- One limitation remains.
"""


def _repository(tmp_path: Path, version: str = "1.2.3") -> Path:
    releases = tmp_path / "docs" / "releases"
    releases.mkdir(parents=True)
    (tmp_path / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    template = REQUIRED_NOTES.format(version="X.Y.Z").replace(
        "A concrete outcome.",
        "Summarize the most important user-visible or operational outcomes.",
    )
    (releases / "TEMPLATE.md").write_text(template, encoding="utf-8")
    (releases / "UNRELEASED.md").write_text(
        REQUIRED_NOTES.format(version="Unreleased"), encoding="utf-8"
    )
    (releases / f"{version}.md").write_text(
        REQUIRED_NOTES.format(version=version), encoding="utf-8"
    )
    return tmp_path


def test_repository_release_contract_is_valid():
    root = Path(__file__).resolve().parents[2]
    release = validate_release(root)
    assert release.version == APP_VERSION
    assert APP_VERSION == (root / "VERSION").read_text(encoding="utf-8").strip()
    assert release.tag == f"prod-v{APP_VERSION}"


def test_matching_tag_is_valid(tmp_path):
    assert validate_release(_repository(tmp_path), "prod-v1.2.3").tag == "prod-v1.2.3"


@pytest.mark.parametrize(
    ("version", "tag"),
    (("1.2.3", "v1.2.3"), ("1.2.3", "prod-v1.2.4"), ("01.2.3", "prod-v01.2.3")),
)
def test_invalid_version_or_tag_fails(tmp_path, version, tag):
    with pytest.raises(ReleaseValidationError):
        validate_release(_repository(tmp_path, version), tag)


@pytest.mark.parametrize(
    "unsafe",
    (
        "TODO",
        "postgresql://user:unsafe@example.invalid/db",
        "client_secret=unsafe",
        "-----BEGIN PRIVATE KEY-----",
    ),
)
def test_unsafe_release_note_fails(tmp_path, unsafe):
    root = _repository(tmp_path)
    notes = root / "docs" / "releases" / "1.2.3.md"
    notes.write_text(
        notes.read_text(encoding="utf-8").replace("A concrete outcome.", unsafe),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseValidationError):
        validate_release(root)


def test_fastapi_metadata_uses_authoritative_version(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused"
    )
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-secret-key-long-enough")
    from guild_portal.app import create_app

    assert create_app().version == APP_VERSION


def _production_readiness_repository(tmp_path: Path, data: dict) -> Path:
    readiness = tmp_path / ".github" / "production-readiness.json"
    readiness.parent.mkdir(parents=True)
    readiness.write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def _production_readiness_data(*, enabled: bool = False) -> dict:
    return {
        "schema_version": 1,
        "production_enabled": enabled,
        "required_foundation_issues": [54, 55],
        "required_controls": {
            "migration_backup_rollback": "blocked",
            "ssh_trust_credential_isolation": "blocked",
            "github_environment_branch_protection": "blocked",
            "exact_sha_test_provenance": "implemented_unverified",
        },
    }


def test_repository_production_readiness_is_valid_and_blocked():
    root = Path(__file__).resolve().parents[2]
    readiness = validate_production_readiness(root, configuration_only=True)
    assert readiness.enabled is False
    with pytest.raises(ProductionReadinessError, match="repository-blocked"):
        validate_production_readiness(root)


def test_production_cannot_be_enabled_with_unverified_controls(tmp_path):
    data = _production_readiness_data(enabled=True)
    root = _production_readiness_repository(tmp_path, data)
    with pytest.raises(ProductionReadinessError, match="controls are unverified"):
        validate_production_readiness(root, configuration_only=True)


def test_production_requires_all_controls_explicitly_verified(tmp_path):
    data = _production_readiness_data(enabled=True)
    data["required_controls"] = {
        name: "implemented_and_verified" for name in data["required_controls"]
    }
    root = _production_readiness_repository(tmp_path, data)
    assert validate_production_readiness(root).enabled is True


def _test_workflow_runs(sha: str) -> dict:
    return {
        "workflow_runs": [
            {
                "id": 12345,
                "head_sha": sha,
                "head_branch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.example/actions/runs/12345",
            }
        ]
    }


def test_exact_sha_successful_test_run_is_accepted():
    sha = "a" * 40
    evidence = validate_test_provenance(_test_workflow_runs(sha), sha)
    assert evidence.run_id == 12345


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("head_sha", "b" * 40),
        ("head_branch", "feature/not-main"),
        ("event", "workflow_dispatch"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
    ),
)
def test_test_provenance_rejects_nonmatching_run(field, value):
    sha = "a" * 40
    runs = _test_workflow_runs(sha)
    runs["workflow_runs"][0][field] = value
    with pytest.raises(ProvenanceValidationError, match="No successful Deploy to Test"):
        validate_test_provenance(runs, sha)
