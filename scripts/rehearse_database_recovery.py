"""Rehearse a production-shaped PostgreSQL backup and recovery in isolation.

This command is intended for CI or an explicitly isolated non-production
database.  It never accepts a restore database outside the ``patt_recovery_``
namespace and removes the restored database after verification.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import asyncpg
from sqlalchemy.engine import URL, make_url


SAFE_RESTORE_DATABASE = re.compile(r"^patt_recovery_[a-z0-9_]+$")
PROBE_TABLE = "recovery_rehearsal_probe"


@dataclass(frozen=True)
class PgConnection:
    host: str
    port: int
    username: str
    password: str
    database: str

    def command_args(self) -> list[str]:
        return [
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--username",
            self.username,
            "--dbname",
            self.database,
        ]

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PGPASSWORD"] = self.password
        return environment


def _database_url(raw_url: str, database: str, *, async_driver: bool) -> str:
    url = make_url(raw_url).set(database=database)
    driver = "postgresql+asyncpg" if async_driver else "postgresql"
    return url.set(drivername=driver).render_as_string(hide_password=False)


def _connection(raw_url: str, database: str | None = None) -> PgConnection:
    url: URL = make_url(raw_url)
    resolved_database = database or url.database
    if not url.host or not url.username or not resolved_database:
        raise ValueError("PostgreSQL URL must include host, user, and database")
    return PgConnection(
        host=url.host,
        port=url.port or 5432,
        username=url.username,
        password=url.password or "",
        database=resolved_database,
    )


def validate_restore_database(source_database: str, restore_database: str) -> None:
    if source_database == restore_database:
        raise ValueError("restore database must differ from the source database")
    if not SAFE_RESTORE_DATABASE.fullmatch(restore_database):
        raise ValueError(
            "restore database must use the bounded patt_recovery_<name> namespace"
        )


def stable_fingerprint_payload(
    *, heads: list[str], tables: list[str], probe_rows: list[tuple[int, str]]
) -> tuple[str, str]:
    payload = json.dumps(
        {
            "alembic_heads": sorted(heads),
            "probe_rows": sorted([list(row) for row in probe_rows]),
            "tables": sorted(tables),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run(command: list[str], *, environment: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "command failed").strip()
        raise RuntimeError(detail) from None
    return result.stdout


async def _prepare_probe(database_url: str) -> None:
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(
            f"""
            CREATE TABLE patt.{PROBE_TABLE} (
                probe_id integer PRIMARY KEY,
                probe_value text NOT NULL
            )
            """
        )
        await connection.executemany(
            f"INSERT INTO patt.{PROBE_TABLE} (probe_id, probe_value) VALUES ($1, $2)",
            [(1, "alpha"), (2, "omega")],
        )
    finally:
        await connection.close()


async def _drop_probe(database_url: str) -> None:
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(f"DROP TABLE IF EXISTS patt.{PROBE_TABLE}")
    finally:
        await connection.close()


async def _fingerprint(database_url: str) -> tuple[str, str, list[str]]:
    connection = await asyncpg.connect(database_url)
    try:
        heads = [
            row["version_num"]
            for row in await connection.fetch(
                "SELECT version_num FROM patt.alembic_version ORDER BY version_num"
            )
        ]
        table_rows = await connection.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        )
        tables = [f"{row['table_schema']}.{row['table_name']}" for row in table_rows]
        probe_rows = [
            (row["probe_id"], row["probe_value"])
            for row in await connection.fetch(
                f"""
                SELECT probe_id, probe_value
                FROM patt.{PROBE_TABLE}
                ORDER BY probe_id
                """
            )
        ]
    finally:
        await connection.close()
    payload, digest = stable_fingerprint_payload(
        heads=heads, tables=tables, probe_rows=probe_rows
    )
    return payload, digest, heads


async def _replace_restore_database(admin_url: str, restore_database: str) -> None:
    connection = await asyncpg.connect(admin_url)
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            restore_database,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{restore_database}"')
        await connection.execute(f'CREATE DATABASE "{restore_database}"')
    finally:
        await connection.close()


async def _drop_restore_database(admin_url: str, restore_database: str) -> None:
    connection = await asyncpg.connect(admin_url)
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            restore_database,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{restore_database}"')
    finally:
        await connection.close()


async def rehearse(args: argparse.Namespace) -> dict[str, object]:
    source = _connection(args.database_url)
    validate_restore_database(source.database, args.restore_database)

    source_async_url = _database_url(
        args.database_url, source.database, async_driver=False
    )
    admin_async_url = _database_url(args.database_url, "postgres", async_driver=False)
    restore_async_url = _database_url(
        args.database_url, args.restore_database, async_driver=False
    )
    restore_alembic_url = _database_url(
        args.database_url, args.restore_database, async_driver=True
    )
    restore = _connection(args.database_url, args.restore_database)
    archive = args.archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)

    await _prepare_probe(source_async_url)
    try:
        source_payload, source_digest, source_heads = await _fingerprint(
            source_async_url
        )
        _run(
            [
                "pg_dump",
                *source.command_args(),
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(archive),
            ],
            environment=source.environment(),
        )
        listing = _run(
            ["pg_restore", "--list", str(archive)],
            environment=source.environment(),
        )
        if PROBE_TABLE not in listing or "alembic_version" not in listing:
            raise RuntimeError("backup archive is missing required recovery objects")

        await _replace_restore_database(admin_async_url, args.restore_database)
        _run(
            [
                "pg_restore",
                *restore.command_args(),
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                str(archive),
            ],
            environment=restore.environment(),
        )
        restored_payload, restored_digest, restored_heads = await _fingerprint(
            restore_async_url
        )
        if restored_payload != source_payload or restored_digest != source_digest:
            raise RuntimeError("restored database fingerprint does not match source")
        if restored_heads != source_heads:
            raise RuntimeError("restored migration identity does not match source")

        alembic_environment = os.environ.copy()
        alembic_environment["DATABASE_URL"] = restore_alembic_url
        _run(
            [sys.executable, "-m", "alembic", "current", "--check-heads"],
            environment=alembic_environment,
        )
        _run(
            [sys.executable, "-m", "alembic", "downgrade", "-1"],
            environment=alembic_environment,
        )
        _, _, downgraded_heads = await _fingerprint(restore_async_url)
        if downgraded_heads == source_heads:
            raise RuntimeError(
                "representative downgrade did not change migration identity"
            )
        _run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            environment=alembic_environment,
        )
        final_payload, final_digest, final_heads = await _fingerprint(restore_async_url)
        if final_payload != source_payload or final_digest != source_digest:
            raise RuntimeError("downgrade/re-upgrade changed the stable fingerprint")
        if final_heads != source_heads:
            raise RuntimeError("downgrade/re-upgrade did not return to the source head")

        return {
            "archive": str(archive),
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "fingerprint_sha256": source_digest,
            "migration_heads": source_heads,
            "restore_database": args.restore_database,
        }
    finally:
        await _drop_restore_database(admin_async_url, args.restore_database)
        await _drop_probe(source_async_url)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rehearse PostgreSQL backup, isolated restore, and migration recovery."
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--restore-database", required=True)
    parser.add_argument("--archive", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    try:
        result = asyncio.run(rehearse(parse_args()))
    except (RuntimeError, ValueError, asyncpg.PostgresError) as exc:
        print(f"Recovery rehearsal failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
