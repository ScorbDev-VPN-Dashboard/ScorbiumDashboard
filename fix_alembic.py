#!/usr/bin/env python3
"""
Fix Alembic migration version vs actual DB schema mismatches.
Uses asyncpg (project dependency) instead of sync psycopg2.

Run inside container:
    docker exec vpn_app uv run python fix_alembic.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

import asyncpg

from app.core.config import config


MIGRATIONS = [
    ("4d5f8377eff0", "initial schema"),
    ("a1b2c3d4e5f6", "add user language"),
    ("b3c4d5e6f7a8", "add user autorenew"),
    ("c4d5e6f7a8b9", "add payment type"),
    ("d5e6f7a8b9c0", "add admins table"),
]


def get_dsn() -> str:
    db = config.database
    return (
        f"postgresql://{db.db_user}:{db.db_password.get_secret_value()}"
        f"@{db.db_host}:{db.db_port}/{db.db_name}"
    )


async def get_actual_revision(conn: asyncpg.Connection) -> str:
    """Inspect DB schema and return the revision that actually matches."""
    # 1. Does users table exist?
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'users'
        """
    )
    if not row:
        return "4d5f8377eff0"  # fresh DB → initial

    # 2. Check for language column (a1b2c3d4e5f6)
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'language'
        """
    )
    if not row:
        return "4d5f8377eff0"

    # 3. Check for autorenew column (b3c4d5e6f7a8)
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'autorenew'
        """
    )
    if not row:
        return "a1b2c3d4e5f6"

    # 4. Check for payment_type column (c4d5e6f7a8b9)
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'payments' AND column_name = 'payment_type'
        """
    )
    if not row:
        return "b3c4d5e6f7a8"

    # 5. Check for admins table (d5e6f7a8b9c0)
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'admins'
        """
    )
    if not row:
        return "c4d5e6f7a8b9"

    return "d5e6f7a8b9c0"


async def get_stamped_revisions(conn: asyncpg.Connection) -> list[str]:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'alembic_version'
        """
    )
    if not row:
        return []

    rows = await conn.fetch("SELECT version_num FROM alembic_version")
    return [r[0] for r in rows]


async def set_revision(conn: asyncpg.Connection, revision: str) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        )
        """
    )
    await conn.execute("DELETE FROM alembic_version")
    await conn.execute(
        "INSERT INTO alembic_version (version_num) VALUES ($1)",
        revision,
    )


async def run_alembic_upgrade() -> int:
    """Run alembic upgrade head and return exit code."""
    print("[INFO] Running: alembic upgrade head")
    proc = await asyncio.create_subprocess_exec(
        "alembic", "upgrade", "head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        print(stdout.decode())
    if stderr:
        print(stderr.decode(), file=sys.stderr)
    return proc.returncode or 0


async def main() -> int:
    print("=" * 60)
    print("Alembic Migration Auto-Fix")
    print("=" * 60)

    dsn = get_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        stamped_revs = await get_stamped_revisions(conn)
        actual = await get_actual_revision(conn)

        print(f"\n1. Stamped revisions: {stamped_revs}")
        print(f"2. Actual schema     : {actual}")

        needs_fix = False

        if len(stamped_revs) != 1:
            print(f"\n[WARN] Found {len(stamped_revs)} stamped revisions (expected 1)")
            needs_fix = True
        elif stamped_revs[0] != actual:
            print(f"\n[WARN] Stamped ({stamped_revs[0]}) != actual ({actual})")
            needs_fix = True

        if needs_fix:
            print(f"[INFO] Cleaning alembic_version and stamping as {actual}")
            await set_revision(conn, actual)
            print("[OK] alembic_version updated")
        else:
            print(f"\n[OK] Stamped and actual match ({actual})")

        # Always run upgrade head so idempotent migrations fix any drift
        code = await run_alembic_upgrade()
        if code != 0:
            print("[ERR] alembic upgrade head failed")
            return code

        final_revs = await get_stamped_revisions(conn)
        print(f"\n[OK] Final revisions: {final_revs}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
