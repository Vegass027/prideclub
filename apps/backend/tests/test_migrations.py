"""Локальный round-trip тест миграций на настоящем Postgres.

`make migrate-test` требует Docker, который в этой среде недоступен. Этот
тест поднимает временный Postgres-кластер через `testing.postgresql` и
прогоняет alembic upgrade head → downgrade base → upgrade head, плюс
проверяет, что критичные индексы/констрейнты на месте.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"


def _have_pg_ctl() -> bool:
    from shutil import which

    return which("pg_ctl") is not None and which("initdb") is not None


@pytest.mark.skipif(not _have_pg_ctl(), reason="pg_ctl/initdb not available")
def test_alembic_round_trip_on_real_postgres() -> None:
    from testing.postgresql import Postgresql

    pg = Postgresql()
    try:
        sync_url = pg.url()
        async_url = _to_async_url(sync_url)

        env = os.environ.copy()
        env["DATABASE_URL"] = async_url
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(BACKEND_ROOT)
            + os.pathsep
            + str(REPO_ROOT / "packages" / "shared")
            + os.pathsep
            + existing_pp
        )

        _alembic(["upgrade", "head"], cwd=BACKEND_ROOT, env=env)  # noqa: S603
        _assert_schema_loaded(sync_url)

        _alembic(["downgrade", "base"], cwd=BACKEND_ROOT, env=env)
        _assert_schema_loaded(sync_url, expect_empty=True)

        _alembic(["upgrade", "head"], cwd=BACKEND_ROOT, env=env)  # noqa: S603
        _assert_schema_loaded(sync_url)

        _assert_critical_invariants(sync_url)
    finally:
        pg.stop()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


def _alembic(args, *, cwd, env):
    cmd = [sys.executable, "-m", "alembic", *args]
    proc = subprocess.run(  # noqa: S603
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=180
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


def _run_sql(sync_url, sql):
    import psycopg

    with psycopg.connect(sync_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            try:
                return list(cur.fetchall())
            except psycopg.ProgrammingError:
                return []


def _assert_schema_loaded(sync_url, *, expect_empty=False):
    rows = _run_sql(sync_url, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    table_names = {r[0] for r in rows}
    if expect_empty:
        # alembic_version остается после downgrade base by design. это by design.
        assert table_names == {"alembic_version"}, (
            f"Expected only alembic_version after downgrade, got: {table_names}"
        )
    else:
        assert "users" in table_names, f"Missing users table. Got: {table_names}"
        assert "habits" in table_names, f"Missing habits table. Got: {table_names}"


def _assert_critical_invariants(sync_url):
    rows = _run_sql(
        sync_url,
        "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='checkins'",
    )
    idx_names = {r[0] for r in rows}
    assert "uq_checkins_membership_date" in idx_names, (
        f"Missing unique index on checkins(membership_id, date). Got: {idx_names}"
    )

    rows = _run_sql(
        sync_url,
        ("SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='suspicious_pairs'"),
    )
    idx_names = {r[0] for r in rows}
    assert "ix_suspicious_pairs_flagged_recent" in idx_names, (
        f"Missing partial index on suspicious_pairs. Got: {idx_names}"
    )

    rows = _run_sql(
        sync_url,
        ("SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='users'"),
    )
    idx_names = {r[0] for r in rows}
    assert "ix_users_deleted_at" in idx_names, (
        f"Missing partial index on users(deleted_at). Got: {idx_names}"
    )

    rows = _run_sql(
        sync_url,
        ("SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='habits'"),
    )
    idx_names = {r[0] for r in rows}
    assert "ix_habits_active" in idx_names, (
        f"Missing partial index ix_habits_active on habits(is_active) "
        f"WHERE is_active=true AND archived_at IS NULL. Got: {idx_names}"
    )
    assert "ix_habits_curator" in idx_names, (
        f"Missing partial index ix_habits_curator on habits(curator_id). Got: {idx_names}"
    )

    rows = _run_sql(
        sync_url,
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='habits'
        """,
    )
    col_names = {r[0] for r in rows}
    expected_new = {
        "archived_at",
        "photo_url",
        "telegram_invite_link",
        "stat_name",
        "stat_icon",
        "stat_gain_per_checkin",
        "stat_loss_per_miss",
        "member_limit",
        "curator_id",
    }
    missing = expected_new - col_names
    assert not missing, f"Missing habits columns: {missing}. Got: {sorted(col_names)}"

    rows = _run_sql(
        sync_url,
        """
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'habits'::regclass AND contype = 'c'
        """,
    )
    check_names = {r[0] for r in rows}
    expected = {
        "habits_stat_loss_positive",
        "habits_stat_gain_positive",
        "habits_member_limit_positive",
    }
    assert expected <= check_names, (
        f"Missing CHECK constraints on habits. Got: {check_names}"
    )

    rows = _run_sql(sync_url, "SELECT extname FROM pg_extension")
    ext_names = {r[0] for r in rows}
    assert "pgcrypto" in ext_names, f"pgcrypto extension missing. Got: {ext_names}"

    rows = _run_sql(
        sync_url,
        """
        SELECT typname FROM pg_type
        WHERE typtype = 'e' AND typname IN (
            'proof_type','membership_status','checkin_status','season_status'
        )
        """,
    )
    enums = {r[0] for r in rows}
    assert enums == {"proof_type", "membership_status", "checkin_status", "season_status"}, (
        f"Missing ENUM types. Got: {enums}"
    )

    rows = _run_sql(
        sync_url,
        ("SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'penalties'::regclass AND contype = 'u'"),
    )
    constraint_names = {r[0] for r in rows}
    assert "uq_penalty_per_day_reason" in constraint_names, (
        f"Missing unique constraint on penalties(membership_id, date, reason). "
        f"Got: {constraint_names}"
    )
