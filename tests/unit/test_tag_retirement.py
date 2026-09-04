"""Regression tests for failed Production attempt-tag retirement controls."""

from __future__ import annotations

import pytest

from scripts.validate_failed_tag_retirement import (
    DEPLOY_JOB,
    TagRetirementValidationError,
    validate_failed_tag_retirement,
)


TAG = "prod-v0.24.3"
TAG_OBJECT = "e" * 40
COMMIT = "a" * 40
RUN_ID = 33806491061


def _evidence() -> dict:
    return {
        "tag_ref": {
            "ref": f"refs/tags/{TAG}",
            "object": {"type": "tag", "sha": TAG_OBJECT},
        },
        "tag_object": {
            "sha": TAG_OBJECT,
            "tag": TAG,
            "object": {"type": "commit", "sha": COMMIT},
        },
        "releases": [],
        "workflow_runs": [
            {
                "id": RUN_ID,
                "head_branch": TAG,
                "head_sha": COMMIT,
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
            }
        ],
        "jobs_by_run": {
            RUN_ID: {
                "jobs": [
                    {"name": DEPLOY_JOB, "conclusion": "failure"},
                    {
                        "name": "Publish verified production release",
                        "conclusion": "skipped",
                    },
                ]
            }
        },
    }


def _validate(evidence: dict):
    return validate_failed_tag_retirement(
        tag=TAG,
        expected_tag_object=TAG_OBJECT,
        expected_commit=COMMIT,
        expected_failed_run_id=RUN_ID,
        **evidence,
    )


def test_failed_unpublished_attempt_is_eligible_for_manual_retirement():
    result = _validate(_evidence())
    assert result.tag_object_sha == TAG_OBJECT
    assert result.commit_sha == COMMIT
    assert result.failed_run_ids == (RUN_ID,)


def test_published_release_permanently_blocks_tag_retirement():
    evidence = _evidence()
    evidence["releases"] = [{"tag_name": TAG, "draft": False}]
    with pytest.raises(TagRetirementValidationError, match="Release exists"):
        _validate(evidence)


def test_successful_production_completion_permanently_blocks_tag_retirement():
    evidence = _evidence()
    evidence["workflow_runs"][0]["conclusion"] = "failure"
    evidence["jobs_by_run"][RUN_ID]["jobs"][0]["conclusion"] = "success"
    with pytest.raises(TagRetirementValidationError, match="completed deployment"):
        _validate(evidence)


@pytest.mark.parametrize("conclusion", [None, "queued", "in_progress", "skipped"])
def test_nonterminal_or_unexecuted_deploy_job_fails_closed(conclusion):
    evidence = _evidence()
    evidence["jobs_by_run"][RUN_ID]["jobs"][0]["conclusion"] = conclusion
    with pytest.raises(TagRetirementValidationError, match="non-terminal"):
        _validate(evidence)


def test_changed_remote_tag_object_fails_closed():
    evidence = _evidence()
    evidence["tag_ref"]["object"]["sha"] = "b" * 40
    with pytest.raises(TagRetirementValidationError, match="object changed"):
        _validate(evidence)


def test_missing_expected_failed_run_fails_closed():
    evidence = _evidence()
    evidence["workflow_runs"][0]["id"] = RUN_ID + 1
    evidence["jobs_by_run"] = {RUN_ID + 1: evidence["jobs_by_run"].pop(RUN_ID)}
    with pytest.raises(TagRetirementValidationError, match="expected failed"):
        _validate(evidence)


def test_prior_run_for_same_tag_on_another_commit_fails_closed():
    evidence = _evidence()
    evidence["workflow_runs"].append(
        {
            "id": RUN_ID - 1,
            "head_branch": TAG,
            "head_sha": "b" * 40,
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
        }
    )
    with pytest.raises(TagRetirementValidationError, match="another commit"):
        _validate(evidence)
