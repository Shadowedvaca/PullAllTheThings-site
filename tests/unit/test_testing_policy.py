"""Repository contracts for the canonical testing policy and profile."""

from configparser import ConfigParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ai_entry_points_are_byte_identical_and_route_testing_policy():
    agents = (ROOT / "AGENTS.md").read_bytes()
    claude = (ROOT / "CLAUDE.md").read_bytes()
    assert agents == claude

    text = agents.decode("utf-8")
    required = (
        "MEMORY.md",
        "reference/ai-context.md",
        "reference/work-management.md",
        "reference/testing-and-validation.md",
        "reference/testing-profile.md",
        "reference/development-and-release.md",
    )
    positions = [text.index(name) for name in required]
    assert positions == sorted(positions)


def test_testing_references_define_every_required_layer():
    policy = (ROOT / "reference" / "testing-and-validation.md").read_text(
        encoding="utf-8"
    )
    profile = (ROOT / "reference" / "testing-profile.md").read_text(encoding="utf-8")
    for term in (
        "Static",
        "Unit",
        "Integration",
        "Regression",
        "Coverage",
        "Automated UI/E2E",
        "Deployed smoke",
    ):
        assert term in policy
        assert term in profile
    assert "production credentials" in policy.lower()
    assert "manual human ui validation" in policy.lower()


def test_coverage_floors_match_profile_and_cannot_silently_disappear():
    config = ConfigParser()
    config.read(ROOT / ".coveragerc", encoding="utf-8")
    assert config.getint("report", "fail_under") == 37

    profile = (ROOT / "reference" / "testing-profile.md").read_text(encoding="utf-8")
    workflow = (
        ROOT / ".github" / "workflows" / "pull-request-validation.yml"
    ).read_text(encoding="utf-8")
    assert "floors are `37` overall and `72` for changed lines" in profile
    assert "--cov-fail-under=37" in workflow
    assert "--fail-under=72" in workflow
    assert "diff-cover" in workflow


def test_pr_workflow_runs_browser_suite_and_retains_failure_artifacts():
    workflow = (
        ROOT / ".github" / "workflows" / "pull-request-validation.yml"
    ).read_text(encoding="utf-8")
    assert "python -m playwright install --with-deps chromium" in workflow
    assert "python -m pytest tests/e2e" in workflow
    assert "--tracing=retain-on-failure" in workflow
    assert "actions/upload-artifact@" in workflow


def test_parent_manual_validation_requires_predeployed_exact_dev_artifact():
    work_management = (ROOT / "reference" / "work-management.md").read_text(
        encoding="utf-8"
    )
    release = (ROOT / "reference" / "development-and-release.md").read_text(
        encoding="utf-8"
    )
    testing = (ROOT / "reference" / "testing-and-validation.md").read_text(
        encoding="utf-8"
    )
    profile = (ROOT / "reference" / "testing-profile.md").read_text(encoding="utf-8")

    assert "Never ask a person to\nvalidate an undeployed branch" in work_management
    assert "before handing a checklist to the human validator" in release
    assert (
        "Evidence from a stale or\nidentity-unknown environment is invalid" in testing
    )
    assert "The human\nvalidator receives an already prepared URL" in profile
