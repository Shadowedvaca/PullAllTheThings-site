"""Validate PATT's version and curated release-note contract."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re


SEMVER_PATTERN = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
REQUIRED_SECTIONS = (
    "Highlights",
    "Fixes/Changes",
    "Validation",
    "Deployment/Migrations",
    "Rollback",
    "Known Limitations",
)
PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:TODO|TBD|FIXME|PLACEHOLDER)\b", re.IGNORECASE),
    re.compile(r"\bX\.Y\.Z\b", re.IGNORECASE),
)
TEMPLATE_SENTENCES = (
    "Summarize the most important user-visible or operational outcomes.",
    "List shipped behavior and intentional changes.",
    "Record automated, migration, deployment, and manual validation evidence.",
    "Describe deployment requirements and database or data-contract impact.",
    "Describe the tested or actionable recovery path.",
    "Record accepted limitations and focused follow-up work.",
)
SECRET_PATTERNS = (
    (
        "private key material",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    ),
    (
        "credential-bearing URL",
        re.compile(r"\b(?:https?|ssh)://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
    ),
    (
        "database URL",
        re.compile(
            r"\b(?:postgres(?:ql)?(?:\+asyncpg)?|mysql|mariadb|mongodb|redis)://",
            re.IGNORECASE,
        ),
    ),
    (
        "access token",
        re.compile(
            r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b"
        ),
    ),
    (
        "assigned credential value",
        re.compile(
            r"(?im)^\s*(?:[-*]\s*)?(?:`)?(?:password|secret|token|api[_-]?key|client[_-]?secret|database_url)(?:`)?\s*[:=]\s*[^\s`]+"
        ),
    ),
)


class ReleaseValidationError(ValueError):
    """Raised when release metadata is unsafe or inconsistent."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    notes_path: Path


def _section_body(content: str, section: str) -> str:
    match = re.search(
        rf"(?ms)^## {re.escape(section)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        content,
    )
    return match.group("body").strip() if match else ""


def _validate_note(path: Path, expected_heading: str) -> list[str]:
    errors: list[str] = []
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    if not lines or lines[0] != expected_heading:
        errors.append(f"{path.name}: first heading must be '{expected_heading}'")

    positions: list[int] = []
    for section in REQUIRED_SECTIONS:
        heading = f"## {section}"
        count = content.count(heading)
        if count != 1:
            errors.append(
                f"{path.name}: expected exactly one '{heading}', found {count}"
            )
            continue
        positions.append(content.index(heading))
        if not _section_body(content, section):
            errors.append(f"{path.name}: '{heading}' must not be empty")

    if len(positions) == len(REQUIRED_SECTIONS) and positions != sorted(positions):
        errors.append(f"{path.name}: required sections must remain in template order")
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern.search(content):
            errors.append(f"{path.name}: placeholder text matched {pattern.pattern!r}")
    for sentence in TEMPLATE_SENTENCES:
        if sentence in content:
            errors.append(f"{path.name}: replace template sentence {sentence!r}")
    for description, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            errors.append(f"{path.name}: prohibited {description} detected")
    return errors


def validate_release(repository_root: Path, tag: str | None = None) -> ReleaseInfo:
    """Validate curated notes and return the current release metadata."""

    root = repository_root.resolve()
    version_path = root / "VERSION"
    releases_path = root / "docs" / "releases"
    errors: list[str] = []
    if not version_path.is_file():
        raise ReleaseValidationError("missing authoritative VERSION file")

    version = version_path.read_text(encoding="utf-8").strip()
    if not SEMVER_PATTERN.fullmatch(version):
        errors.append(
            "VERSION must be canonical semantic version X.Y.Z without prefixes or leading zeroes"
        )

    template_path = releases_path / "TEMPLATE.md"
    if not template_path.is_file():
        errors.append("missing release-note template")
    else:
        template = template_path.read_text(encoding="utf-8")
        if not template.startswith("# Pull All The Things X.Y.Z\n"):
            errors.append("release-note template heading must use X.Y.Z")
        for section in REQUIRED_SECTIONS:
            if template.count(f"## {section}") != 1:
                errors.append(
                    f"release-note template must contain exactly one '## {section}'"
                )

    unreleased_path = releases_path / "UNRELEASED.md"
    if not unreleased_path.is_file():
        errors.append("missing cumulative docs/releases/UNRELEASED.md")
    else:
        errors.extend(
            _validate_note(unreleased_path, "# Pull All The Things Unreleased")
        )

    for notes_path in sorted(releases_path.glob("*.md")):
        if notes_path.name in {"TEMPLATE.md", "UNRELEASED.md"}:
            continue
        note_version = notes_path.stem
        if not SEMVER_PATTERN.fullmatch(note_version):
            errors.append(
                f"{notes_path.name}: filename must be a canonical X.Y.Z version"
            )
            continue
        errors.extend(
            _validate_note(notes_path, f"# Pull All The Things {note_version}")
        )

    notes_path = releases_path / f"{version}.md"
    if not notes_path.is_file():
        errors.append(
            f"authoritative version {version!r} requires docs/releases/{version}.md"
        )

    expected_tag = f"prod-v{version}"
    if tag is not None and tag != expected_tag:
        errors.append(
            f"production tag {tag!r} must exactly match authoritative version as {expected_tag!r}"
        )

    if errors:
        raise ReleaseValidationError("\n".join(f"- {error}" for error in errors))
    return ReleaseInfo(version=version, tag=expected_tag, notes_path=notes_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--tag")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        release = validate_release(args.repository_root, args.tag)
    except ReleaseValidationError as error:
        raise SystemExit(f"Release validation failed:\n{error}") from error

    print(
        f"Release contract valid: version={release.version} tag={release.tag} notes={release.notes_path.name}"
    )
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"version={release.version}\n")
            output.write(f"tag={release.tag}\n")
            output.write(
                f"notes_path={release.notes_path.relative_to(args.repository_root.resolve()).as_posix()}\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
