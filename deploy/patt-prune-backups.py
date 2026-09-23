#!/usr/bin/env python3
"""Apply the bounded post-deployment PATT backup-retention policy."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


BACKUP_ROOT = Path("/opt/backups/patt-db")
RETENTION = {"development": 3, "test": 7}
ARCHIVE_PATTERN = re.compile(
    r"^patt_db_(?P<timestamp>[0-9]{8}T[0-9]{6}Z)_"
    r"(?P<sha>[0-9a-f]{40})\.dump$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RetentionError(RuntimeError):
    """Raised when backup evidence is not safe to prune."""


@dataclass(frozen=True)
class BackupPair:
    archive: Path
    manifest: Path


def _manifest_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RetentionError(f"Cannot read backup manifest: {path}") from error
    for line in lines:
        if "=" not in line:
            raise RetentionError(f"Malformed backup manifest: {path}")
        key, value = line.split("=", 1)
        if not key or key in values:
            raise RetentionError(f"Malformed backup manifest: {path}")
        values[key] = value
    return values


def plan_retention(
    backup_dir: Path,
    *,
    retain: int,
    verified_archive: Path,
) -> tuple[list[BackupPair], list[BackupPair]]:
    """Return retained and expired complete pairs after a fail-closed preflight."""
    if retain < 1:
        raise RetentionError("Retention count must be positive")
    if not backup_dir.is_dir() or backup_dir.is_symlink():
        raise RetentionError(f"Backup directory is not a real directory: {backup_dir}")
    backup_dir = backup_dir.resolve(strict=True)

    archives: dict[str, Path] = {}
    manifests: dict[str, Path] = {}
    for entry in backup_dir.iterdir():
        if not entry.name.startswith("patt_db_"):
            continue
        if entry.is_symlink() or not entry.is_file():
            raise RetentionError(f"Unexpected backup evidence: {entry}")
        archive_name = entry.name.removesuffix(".manifest")
        if not ARCHIVE_PATTERN.fullmatch(archive_name):
            raise RetentionError(f"Unexpected backup evidence: {entry}")
        target = manifests if entry.name.endswith(".manifest") else archives
        target[archive_name] = entry

    if set(archives) != set(manifests):
        missing_manifests = sorted(set(archives) - set(manifests))
        missing_archives = sorted(set(manifests) - set(archives))
        details = []
        if missing_manifests:
            details.append(f"missing manifests: {', '.join(missing_manifests)}")
        if missing_archives:
            details.append(f"missing archives: {', '.join(missing_archives)}")
        raise RetentionError("Incomplete backup evidence; " + "; ".join(details))

    pairs: list[BackupPair] = []
    for archive_name in sorted(archives, reverse=True):
        archive = archives[archive_name]
        manifest = manifests[archive_name]
        if archive.stat().st_size == 0 or manifest.stat().st_size == 0:
            raise RetentionError(f"Empty backup evidence: {archive_name}")
        match = ARCHIVE_PATTERN.fullmatch(archive_name)
        assert match is not None
        values = _manifest_values(manifest)
        expected = {
            "schema_version": "1",
            "deployment_sha": match.group("sha"),
            "archive": str(archive),
            "restore_authority": "explicit_required",
            "automatic_database_downgrade": "false",
        }
        if any(values.get(key) != value for key, value in expected.items()):
            raise RetentionError(f"Manifest identity mismatch: {manifest}")
        if not SHA256_PATTERN.fullmatch(values.get("archive_sha256", "")):
            raise RetentionError(f"Manifest checksum is invalid: {manifest}")
        pairs.append(BackupPair(archive=archive, manifest=manifest))

    verified_archive = verified_archive.resolve(strict=True)
    if not pairs or pairs[0].archive != verified_archive:
        raise RetentionError(
            "The deployment's verified archive is not the newest complete backup"
        )
    return pairs[:retain], pairs[retain:]


def apply_retention(
    expired: list[BackupPair],
    *,
    environment: str,
    retained_count: int,
    dry_run: bool,
) -> None:
    """Report every selected path, then delete only the preflighted expired pairs."""
    action = "WOULD_DELETE" if dry_run else "DELETE"
    for pair in expired:
        print(
            f"PATT_BACKUP_RETENTION_{action} environment={environment} "
            f"archive={pair.archive} manifest={pair.manifest}"
        )
    if not dry_run:
        for pair in expired:
            pair.archive.unlink()
            pair.manifest.unlink()
    print(
        f"PATT_BACKUP_RETENTION_COMPLETE environment={environment} "
        f"retained={retained_count} deleted={0 if dry_run else len(expired)} "
        f"dry_run={'true' if dry_run else 'false'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--verified-archive", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.environment == "production":
        parser.error("Production backup retention is never automatic")
    if args.environment not in RETENTION:
        parser.error("Environment must be development or test")

    backup_dir = BACKUP_ROOT / args.environment
    try:
        retained, expired = plan_retention(
            backup_dir,
            retain=RETENTION[args.environment],
            verified_archive=args.verified_archive,
        )
        apply_retention(
            expired,
            environment=args.environment,
            retained_count=len(retained),
            dry_run=args.dry_run,
        )
    except RetentionError as error:
        parser.exit(1, f"Backup retention refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
