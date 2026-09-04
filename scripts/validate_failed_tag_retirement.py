"""Prove that a failed, unpublished Production attempt tag may be retired.

This command is deliberately read-only. It validates GitHub evidence and never
deletes, creates, or moves a tag.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re
import subprocess
from urllib.parse import quote


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TAG_PATTERN = re.compile(
    r"^prod-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
DEPLOY_JOB = "Deploy exact approved production tag"


class TagRetirementValidationError(ValueError):
    """Raised when GitHub evidence does not permit attempt-tag retirement."""


@dataclass(frozen=True)
class TagRetirementEvidence:
    tag: str
    tag_object_sha: str
    commit_sha: str
    failed_run_ids: tuple[int, ...]


def validate_failed_tag_retirement(
    *,
    tag: str,
    expected_tag_object: str,
    expected_commit: str,
    expected_failed_run_id: int,
    tag_ref: object,
    tag_object: object,
    releases: object,
    workflow_runs: object,
    jobs_by_run: dict[int, object],
) -> TagRetirementEvidence:
    """Validate preserved evidence for one failed Production attempt tag."""

    errors: list[str] = []
    if not TAG_PATTERN.fullmatch(tag):
        errors.append("tag must use canonical prod-vX.Y.Z form")
    if not SHA_PATTERN.fullmatch(expected_tag_object):
        errors.append("expected tag object must be an exact lowercase SHA")
    if not SHA_PATTERN.fullmatch(expected_commit):
        errors.append("expected commit must be an exact lowercase SHA")
    if expected_failed_run_id < 1:
        errors.append("expected failed run ID must be positive")

    if not isinstance(tag_ref, dict):
        errors.append("tag ref response must be an object")
    else:
        ref_object = tag_ref.get("object")
        if tag_ref.get("ref") != f"refs/tags/{tag}":
            errors.append("remote tag ref does not match the requested tag")
        if not isinstance(ref_object, dict):
            errors.append("remote tag ref is missing its object")
        elif ref_object.get("type") != "tag":
            errors.append("attempt tag must be annotated, not lightweight")
        elif ref_object.get("sha") != expected_tag_object:
            errors.append("remote annotated tag object changed")

    if not isinstance(tag_object, dict):
        errors.append("annotated tag response must be an object")
    else:
        target = tag_object.get("object")
        if tag_object.get("sha") != expected_tag_object:
            errors.append("annotated tag object does not match expected object")
        if tag_object.get("tag") != tag:
            errors.append("annotated tag name does not match requested tag")
        if not isinstance(target, dict):
            errors.append("annotated tag is missing its target")
        elif target.get("type") != "commit":
            errors.append("annotated tag must point directly to a commit")
        elif target.get("sha") != expected_commit:
            errors.append("annotated tag target changed")

    if not isinstance(releases, list):
        errors.append("GitHub Releases response must be a list")
    elif any(
        isinstance(release, dict) and release.get("tag_name") == tag
        for release in releases
    ):
        errors.append("a GitHub Release exists for this tag")

    if not isinstance(workflow_runs, list):
        errors.append("Production workflow response must be a list")
        matching_runs: list[dict] = []
    else:
        tag_runs = [
            run
            for run in workflow_runs
            if isinstance(run, dict)
            and run.get("head_branch") == tag
            and run.get("event") == "push"
        ]
        if any(run.get("head_sha") != expected_commit for run in tag_runs):
            errors.append(
                "Production history shows the tag associated with another commit"
            )
        matching_runs = [
            run for run in tag_runs if run.get("head_sha") == expected_commit
        ]

    if not matching_runs:
        errors.append("no Production push run matches the exact tag and commit")

    failed_run_ids: list[int] = []
    expected_run_found = False
    for run in matching_runs:
        run_id = run.get("id")
        if not isinstance(run_id, int):
            errors.append("matching Production run is missing an integer ID")
            continue
        if run_id == expected_failed_run_id:
            expected_run_found = True
        if run.get("status") != "completed":
            errors.append(f"Production run {run_id} is not completed")

        jobs_response = jobs_by_run.get(run_id)
        jobs = jobs_response.get("jobs") if isinstance(jobs_response, dict) else None
        if not isinstance(jobs, list):
            errors.append(f"Production run {run_id} is missing complete job evidence")
            continue
        deploy_jobs = [
            job
            for job in jobs
            if isinstance(job, dict) and job.get("name") == DEPLOY_JOB
        ]
        if len(deploy_jobs) != 1:
            errors.append(
                f"Production run {run_id} must contain exactly one deploy job"
            )
            continue
        deploy_conclusion = deploy_jobs[0].get("conclusion")
        if deploy_conclusion == "success":
            errors.append(
                f"Production run {run_id} completed deployment successfully"
            )
        elif deploy_conclusion not in {"failure", "cancelled", "timed_out"}:
            errors.append(
                f"Production run {run_id} has non-terminal deploy conclusion "
                f"{deploy_conclusion!r}"
            )
        else:
            failed_run_ids.append(run_id)

    if not expected_run_found:
        errors.append("expected failed Production run is not exact tag/commit evidence")
    if expected_failed_run_id not in failed_run_ids:
        errors.append("expected Production run does not prove a failed deployment")

    if errors:
        raise TagRetirementValidationError("; ".join(errors))
    return TagRetirementEvidence(
        tag=tag,
        tag_object_sha=expected_tag_object,
        commit_sha=expected_commit,
        failed_run_ids=tuple(sorted(failed_run_ids)),
    )


class GitHubEvidenceReader:
    """Read GitHub REST evidence through the repository-required CLI context."""

    def __init__(self, repository: str, gh_command: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise TagRetirementValidationError("repository must use owner/name form")
        self.repository = repository
        self.gh_command = gh_command

    def get(self, path: str) -> object:
        result = subprocess.run(
            [self.gh_command, "api", path],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
            raise TagRetirementValidationError(
                f"GitHub evidence lookup failed for {path}: {detail[0]}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise TagRetirementValidationError(
                f"GitHub evidence lookup returned invalid JSON for {path}"
            ) from error

    def paged(self, path: str, field: str) -> list[object]:
        records: list[object] = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            response = self.get(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(response, dict) or not isinstance(
                response.get(field), list
            ):
                raise TagRetirementValidationError(
                    f"GitHub evidence page must contain {field}"
                )
            current = response[field]
            records.extend(current)
            if len(current) < 100:
                return records
            page += 1

    def releases(self) -> list[object]:
        records: list[object] = []
        page = 1
        while True:
            response = self.get(
                f"repos/{self.repository}/releases?per_page=100&page={page}"
            )
            if not isinstance(response, list):
                raise TagRetirementValidationError(
                    "GitHub Releases page must be a list"
                )
            records.extend(response)
            if len(response) < 100:
                return records
            page += 1


def collect_and_validate(
    *,
    reader: GitHubEvidenceReader,
    tag: str,
    expected_tag_object: str,
    expected_commit: str,
    expected_failed_run_id: int,
) -> TagRetirementEvidence:
    encoded_tag = quote(tag, safe="")
    tag_ref = reader.get(f"repos/{reader.repository}/git/ref/tags/{encoded_tag}")
    tag_object = reader.get(
        f"repos/{reader.repository}/git/tags/{expected_tag_object}"
    )
    releases = reader.releases()
    workflow_runs = reader.paged(
        f"repos/{reader.repository}/actions/workflows/deploy-prod.yml/runs"
        f"?branch={encoded_tag}&event=push",
        "workflow_runs",
    )
    jobs_by_run: dict[int, object] = {}
    for run in workflow_runs:
        if isinstance(run, dict) and isinstance(run.get("id"), int):
            jobs_by_run[run["id"]] = {
                "jobs": reader.paged(
                    f"repos/{reader.repository}/actions/runs/{run['id']}/jobs"
                    "?filter=all",
                    "jobs",
                )
            }

    evidence = validate_failed_tag_retirement(
        tag=tag,
        expected_tag_object=expected_tag_object,
        expected_commit=expected_commit,
        expected_failed_run_id=expected_failed_run_id,
        tag_ref=tag_ref,
        tag_object=tag_object,
        releases=releases,
        workflow_runs=workflow_runs,
        jobs_by_run=jobs_by_run,
    )
    # Recheck the externally mutable facts after the complete scan. The command
    # still does not delete the tag; the operator performs that separate action.
    if reader.get(f"repos/{reader.repository}/git/ref/tags/{encoded_tag}") != tag_ref:
        raise TagRetirementValidationError("remote tag ref changed during validation")
    if reader.releases() != releases:
        raise TagRetirementValidationError(
            "GitHub Release state changed during validation"
        )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-tag-object", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--failed-run-id", required=True, type=int)
    parser.add_argument("--gh-command", default="gh")
    args = parser.parse_args()
    try:
        evidence = collect_and_validate(
            reader=GitHubEvidenceReader(args.repository, args.gh_command),
            tag=args.tag,
            expected_tag_object=args.expected_tag_object,
            expected_commit=args.expected_commit,
            expected_failed_run_id=args.failed_run_id,
        )
    except TagRetirementValidationError as error:
        raise SystemExit(f"Tag retirement validation failed: {error}") from error

    run_ids = ",".join(str(run_id) for run_id in evidence.failed_run_ids)
    print(
        "Failed attempt tag is eligible for separately authorized manual retirement: "
        f"tag={evidence.tag} tag_object={evidence.tag_object_sha} "
        f"commit={evidence.commit_sha} failed_runs={run_ids}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
