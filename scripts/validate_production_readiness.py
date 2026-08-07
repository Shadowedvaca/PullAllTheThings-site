"""Fail-closed validation for PATT Production deployment readiness."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


READINESS_PATH = Path(".github/production-readiness.json")
REQUIRED_ISSUES = (54, 55)
REQUIRED_CONTROLS = (
    "migration_backup_rollback",
    "ssh_trust_credential_isolation",
    "github_environment_branch_protection",
    "exact_sha_test_provenance",
)
VERIFIED = "implemented_and_verified"


class ProductionReadinessError(ValueError):
    """Raised when Production readiness is malformed or remains blocked."""


@dataclass(frozen=True)
class ProductionReadiness:
    enabled: bool
    controls: dict[str, str]

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(
            name for name in REQUIRED_CONTROLS if self.controls.get(name) != VERIFIED
        )


def validate_production_readiness(
    repository_root: Path, *, configuration_only: bool = False
) -> ProductionReadiness:
    """Validate the readiness file and, by default, require Production enabled."""

    path = repository_root.resolve() / READINESS_PATH
    if not path.is_file():
        raise ProductionReadinessError(f"missing readiness file: {READINESS_PATH}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ProductionReadinessError(
            f"cannot read readiness file: {error}"
        ) from error

    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if type(data.get("production_enabled")) is not bool:
        errors.append("production_enabled must be a boolean")
    if data.get("required_foundation_issues") != list(REQUIRED_ISSUES):
        errors.append("required_foundation_issues must be exactly [54, 55]")

    controls = data.get("required_controls")
    if not isinstance(controls, dict):
        errors.append("required_controls must be an object")
        controls = {}
    elif set(controls) != set(REQUIRED_CONTROLS):
        errors.append(
            "required_controls must contain exactly: " + ", ".join(REQUIRED_CONTROLS)
        )
    for name, state in controls.items():
        if state not in {"blocked", "implemented_unverified", VERIFIED}:
            errors.append(f"{name} has unsupported readiness state {state!r}")

    if errors:
        raise ProductionReadinessError("; ".join(errors))

    readiness = ProductionReadiness(data["production_enabled"], controls)
    if readiness.enabled and readiness.blockers:
        raise ProductionReadinessError(
            "production_enabled cannot be true while controls are unverified: "
            + ", ".join(readiness.blockers)
        )
    if not configuration_only and not readiness.enabled:
        raise ProductionReadinessError(
            "Production is repository-blocked pending issues #54 and #55: "
            + ", ".join(readiness.blockers)
        )
    return readiness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--configuration-only",
        action="store_true",
        help="validate the fail-closed configuration without requiring enablement",
    )
    args = parser.parse_args()
    try:
        readiness = validate_production_readiness(
            args.repository_root, configuration_only=args.configuration_only
        )
    except ProductionReadinessError as error:
        raise SystemExit(f"Production readiness validation failed: {error}") from error

    state = "enabled" if readiness.enabled else "blocked"
    print(f"Production readiness configuration valid: state={state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
