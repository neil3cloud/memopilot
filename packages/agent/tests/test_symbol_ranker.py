"""Tests for FTS5-based symbol relevance ranking."""

from __future__ import annotations

import pytest

from agent.db import DatabaseManager
from agent.symbol_ranker import rank_symbols_for_task


async def _seed_symbol(
    db: DatabaseManager,
    *,
    id: str,
    file_path: str,
    name: str,
    kind: str = "function",
    signature: str = "",
    summary: str | None = None,
) -> None:
    conn = await db.connect()
    await conn.execute(
        """
        INSERT INTO symbols
            (id, file_path, name, kind, start_line, end_line, signature, summary, content_hash)
        VALUES (?, ?, ?, ?, 1, 5, ?, ?, 'hash')
        """,
        (id, file_path, name, kind, signature, summary),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_ranks_name_match_above_unrelated_symbol(test_db: DatabaseManager):
    await _seed_symbol(
        test_db,
        id="s1",
        file_path="orders.py",
        name="validate_payment",
        signature="def validate_payment(order):",
        summary="Validates a payment before checkout.",
    )
    await _seed_symbol(
        test_db,
        id="s2",
        file_path="orders.py",
        name="unrelated_helper",
        signature="def unrelated_helper():",
        summary="Formats a log line.",
    )

    results = await rank_symbols_for_task(
        db=test_db,
        task_description="fix the validate payment logic",
        file_paths=["orders.py"],
        limit=10,
    )

    assert [r.id for r in results][:1] == ["s1"]


@pytest.mark.asyncio
async def test_scoped_to_given_file_paths(test_db: DatabaseManager):
    await _seed_symbol(
        test_db,
        id="s1",
        file_path="orders.py",
        name="validate_payment",
        summary="Validates payment.",
    )
    await _seed_symbol(
        test_db,
        id="s2",
        file_path="billing.py",
        name="validate_payment",
        summary="Validates payment in billing.",
    )

    results = await rank_symbols_for_task(
        db=test_db,
        task_description="validate payment",
        file_paths=["orders.py"],
        limit=10,
    )

    assert {r.file_path for r in results} == {"orders.py"}


@pytest.mark.asyncio
async def test_empty_file_paths_returns_empty(test_db: DatabaseManager):
    results = await rank_symbols_for_task(
        db=test_db,
        task_description="validate payment",
        file_paths=[],
        limit=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_no_usable_keywords_returns_empty(test_db: DatabaseManager):
    await _seed_symbol(test_db, id="s1", file_path="orders.py", name="validate_payment")

    # All short/stopword tokens — extract_search_keywords yields nothing.
    results = await rank_symbols_for_task(
        db=test_db,
        task_description="fix the bug",
        file_paths=["orders.py"],
        limit=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_limit_is_respected(test_db: DatabaseManager):
    for i in range(5):
        await _seed_symbol(
            test_db,
            id=f"s{i}",
            file_path="orders.py",
            name=f"validate_payment_{i}",
            summary="Validates payment.",
        )

    results = await rank_symbols_for_task(
        db=test_db,
        task_description="validate payment",
        file_paths=["orders.py"],
        limit=2,
    )
    assert len(results) == 2


@pytest.mark.asyncio
async def test_unscoped_search_finds_symbol_regardless_of_filename(test_db: DatabaseManager):
    """file_paths=None searches by symbol content, not file path — this is
    what makes it useful for file discovery: a class can be relevant to a
    task even when its containing file's name doesn't mention it at all
    (e.g. a "ReservationService" class living in a generically-named
    "Services.cs" file alongside several other unrelated services)."""
    await _seed_symbol(
        test_db,
        id="s1",
        file_path="Services.cs",
        name="ReservationService",
        kind="class",
        summary="Handles reservation creation and cancellation.",
    )
    await _seed_symbol(
        test_db,
        id="s2",
        file_path="unrelated.cs",
        name="UnrelatedThing",
        summary="Does something else entirely.",
    )

    results = await rank_symbols_for_task(
        db=test_db,
        task_description="fix the CreateAsync method in ReservationService",
        file_paths=None,
        limit=10,
    )

    assert [r.id for r in results][:1] == ["s1"]
    assert results[0].file_path == "Services.cs"


@pytest.mark.asyncio
async def test_unscoped_search_with_no_keywords_returns_empty(test_db: DatabaseManager):
    results = await rank_symbols_for_task(
        db=test_db,
        task_description="fix the bug",
        file_paths=None,
        limit=10,
    )
    assert results == []
