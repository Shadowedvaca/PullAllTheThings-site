"""Require a successful PATT test deployment for an exact commit SHA."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


class ProvenanceValidationError(ValueError):
    """Raised when workflow-run evidence does not prove the requested SHA."""


@dataclass(frozen=True)
class TestProvenance:
    run_id: int
    run_url: str
    sha: str


def validate_test_provenance(data: object, sha: str) -> TestProvenance:
    """Select exact-SHA evidence from Deploy to Test workflow-run API data."""

    if not SHA_PATTERN.fullmatch(sha):
        raise ProvenanceValidationError(
            "release SHA must be 40 lowercase hexadecimal characters"
        )
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        raise ProvenanceValidationError(
            "workflow response must contain a workflow_runs list"
        )

    for run in data["workflow_runs"]:
        if not isinstance(run, dict):
            continue
        if (
            run.get("head_sha") == sha
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and isinstance(run.get("id"), int)
            and isinstance(run.get("html_url"), str)
        ):
            return TestProvenance(run["id"], run["html_url"], sha)
    raise ProvenanceValidationError(
        f"No successful Deploy to Test push run proves exact SHA {sha}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        data = json.load(sys.stdin)
        evidence = validate_test_provenance(data, args.sha)
    except (json.JSONDecodeError, ProvenanceValidationError) as error:
        raise SystemExit(f"Test provenance validation failed: {error}") from error

    print(
        f"Exact-SHA test provenance valid: sha={evidence.sha} "
        f"run_id={evidence.run_id} url={evidence.run_url}"
    )
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"run_id={evidence.run_id}\n")
            output.write(f"run_url={evidence.run_url}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
