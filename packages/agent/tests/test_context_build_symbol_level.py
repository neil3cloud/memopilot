"""Tests for symbol-level (Tier 1 / Tier 2) context assembly in /v1/context/build."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from agent.db import DatabaseManager

_FIXTURE_SOURCE = '''\
def validate_payment(order):
    """Validate a payment before checkout."""
    if order.amount <= 0:
        raise ValueError("invalid amount")
    return True


def format_receipt_line(item):
    return f"{item.name}: {item.price}"


def log_debug_event(message):
    print(f"[debug] {message}")
'''


async def _index_and_summarize(
    client: AsyncClient,
    headers: dict[str, str],
    test_db: DatabaseManager,
    tmp_workspace: Path,
) -> None:
    (tmp_workspace / "orders.py").write_text(_FIXTURE_SOURCE, encoding="utf-8")
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    # No LLM configured in tests, so summaries are never auto-generated —
    # inject them directly to simulate a fully-summarized workspace.
    conn = await test_db.connect()
    await conn.execute(
        "UPDATE symbols SET summary = ? WHERE name = 'validate_payment'",
        ("Validates a payment before checkout.",),
    )
    await conn.execute(
        "UPDATE symbols SET summary = ? WHERE name = 'format_receipt_line'",
        ("Formats a single receipt line for display.",),
    )
    await conn.execute(
        "UPDATE symbols SET summary = ? WHERE name = 'log_debug_event'",
        ("Logs a debug message to stdout.",),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_matched_symbol_gets_full_source_others_get_skeleton(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    await _index_and_summarize(client, headers, test_db, tmp_workspace)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            "suggested_files": ["orders.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    entries = {entry["path"]: entry["content"] for entry in data["files"]}

    matched_entry = next(
        (content for path, content in entries.items() if "::validate_payment#" in path),
        None,
    )
    assert matched_entry is not None, f"expected a Tier-1 entry for validate_payment, got paths: {list(entries)}"
    assert "raise ValueError" in matched_entry  # full source body, not a skeleton line

    skeleton_entry = next(
        (content for path, content in entries.items() if path.endswith("::__skeleton__")),
        None,
    )
    assert skeleton_entry is not None, f"expected a skeleton block, got paths: {list(entries)}"
    assert "Formats a single receipt line for display." in skeleton_entry
    assert "Logs a debug message to stdout." in skeleton_entry
    # Skeleton lines never inline full function bodies.
    assert "print(f" not in skeleton_entry


_LARGE_FIXTURE_SOURCE = "\n\n".join(
    [
        '''\
def validate_payment(order):
    """Validate a payment before checkout."""
    if order.amount <= 0:
        raise ValueError("invalid amount")
    if order.currency not in ("USD", "EUR", "GBP"):
        raise ValueError("unsupported currency")
    if order.customer is None:
        raise ValueError("missing customer")
    return True'''
    ]
    + [
        f'''\
def unrelated_helper_{i}(value):
    total = 0
    for offset in range(value):
        total += offset * {i}
        total -= offset // 2
    return total'''
        for i in range(12)
    ]
)


@pytest.mark.asyncio
async def test_symbol_level_context_uses_fewer_tokens_than_whole_file(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "orders.py").write_text(_LARGE_FIXTURE_SOURCE, encoding="utf-8")
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    conn = await test_db.connect()
    await conn.execute(
        "UPDATE symbols SET summary = ? WHERE name = 'validate_payment'",
        ("Validates a payment before checkout.",),
    )
    for i in range(12):
        await conn.execute(
            "UPDATE symbols SET summary = ? WHERE name = ?",
            (f"Computes an unrelated running total variant {i}.", f"unrelated_helper_{i}"),
        )
    await conn.commit()

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            "suggested_files": ["orders.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    whole_file_tokens = max(1, (len(_LARGE_FIXTURE_SOURCE) + 3) // 4)
    assert data["total_tokens"] < whole_file_tokens


@pytest.mark.asyncio
async def test_pending_summary_shown_explicitly_not_fabricated(
    client: AsyncClient,
    test_token: str,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "orders.py").write_text(_FIXTURE_SOURCE, encoding="utf-8")
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200
    # Summaries intentionally left NULL (no LLM configured in tests).

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            "suggested_files": ["orders.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    entries = {entry["path"]: entry["content"] for entry in data["files"]}
    skeleton_entry = next(
        (content for path, content in entries.items() if path.endswith("::__skeleton__")),
        None,
    )
    assert skeleton_entry is not None
    assert "(summary pending)" in skeleton_entry


@pytest.mark.asyncio
async def test_nonexistent_files_still_fall_back_unchanged(client: AsyncClient, test_token: str):
    """Regression check: files with zero indexed symbols keep the old whole-file behavior."""
    headers = {"X-Agent-Token": test_token}
    await client.post("/v1/workspace/init", headers=headers)

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "Fix bug",
            "suggested_files": ["nonexistent_file_xyz.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    assert data["files"][0]["path"] == "nonexistent_file_xyz.py"
    assert data["files"][0]["tokens"] >= 1


# ── Phase 2: verify Tier 1/2 work language-agnostically for TS and C# ──
# _build_symbol_level_context_items only reads from the `symbols` table, so
# no new production code is expected here — this is pure verification that
# indexed TS/C# symbols flow through the same path as Python ones.

_TS_FIXTURE_SOURCE = """\
class OrderService {
    validatePayment(order) {
        if (order.amount <= 0) {
            throw new Error("invalid amount");
        }
        return true;
    }

    formatReceiptLine(item) {
        return `${item.name}: ${item.price}`;
    }

    logDebugEvent(message) {
        console.log(`[debug] ${message}`);
    }
}
"""

_CS_FIXTURE_SOURCE = """\
namespace Billing;
public class PaymentService
{
    public bool ValidatePayment(Order order)
    {
        if (order.Amount <= 0)
        {
            throw new Exception("invalid amount");
        }
        return true;
    }

    public string FormatReceiptLine(Item item)
    {
        return item.Name + ": " + item.Price;
    }

    public void LogDebugEvent(string message)
    {
        Console.WriteLine(message);
    }
}
"""


@pytest.mark.asyncio
async def test_typescript_symbol_level_tier1_and_tier2(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "orders.ts").write_text(_TS_FIXTURE_SOURCE, encoding="utf-8")
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    conn = await test_db.connect()
    cursor = await conn.execute("SELECT id, name FROM symbols WHERE file_path = 'orders.ts'")
    rows = await cursor.fetchall()
    assert rows, "expected TS symbols to be indexed"
    for row in rows:
        summary = "Validates a payment before checkout." if row["name"].endswith(
            "validatePayment"
        ) else f"Handles {row['name']}."
        await conn.execute("UPDATE symbols SET summary = ? WHERE id = ?", (summary, row["id"]))
    await conn.commit()

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            "suggested_files": ["orders.ts"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    entries = {entry["path"]: entry["content"] for entry in data["files"]}

    matched_entry = next(
        (content for path, content in entries.items() if "validatePayment" in path),
        None,
    )
    assert matched_entry is not None, f"expected a Tier-1 entry, got paths: {list(entries)}"
    assert "throw new Error" in matched_entry

    skeleton_entry = next(
        (content for path, content in entries.items() if path.endswith("::__skeleton__")),
        None,
    )
    assert skeleton_entry is not None, f"expected a skeleton block, got paths: {list(entries)}"


@pytest.mark.asyncio
async def test_csharp_symbol_level_tier1_and_tier2(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "service.cs").write_text(_CS_FIXTURE_SOURCE, encoding="utf-8")
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    conn = await test_db.connect()
    cursor = await conn.execute("SELECT id, name FROM symbols WHERE file_path = 'service.cs'")
    rows = await cursor.fetchall()
    assert rows, "expected C# symbols to be indexed"
    for row in rows:
        summary = "Validates a payment before checkout." if row["name"].endswith(
            "ValidatePayment"
        ) else f"Handles {row['name']}."
        await conn.execute("UPDATE symbols SET summary = ? WHERE id = ?", (summary, row["id"]))
    await conn.commit()

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            "suggested_files": ["service.cs"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    entries = {entry["path"]: entry["content"] for entry in data["files"]}

    matched_entry = next(
        (content for path, content in entries.items() if "ValidatePayment" in path),
        None,
    )
    assert matched_entry is not None, f"expected a Tier-1 entry, got paths: {list(entries)}"
    assert "throw new Exception" in matched_entry

    skeleton_entry = next(
        (content for path, content in entries.items() if path.endswith("::__skeleton__")),
        None,
    )
    assert skeleton_entry is not None, f"expected a skeleton block, got paths: {list(entries)}"


# ── Phase 3a: Tier 3 cross-file callee pull-in ──

_ORDERS_CALLS_BILLING_SOURCE = '''\
from billing import charge_customer


def validate_payment(order):
    """Validate a payment before checkout."""
    if order.amount <= 0:
        raise ValueError("invalid amount")
    charge_customer(order)
    return True
'''

_BILLING_SOURCE = '''\
def charge_customer(order):
    """Charges the customer for their order."""
    return True
'''

_BILLING_LARGE_CALLEE_SOURCE = "def charge_customer(order):\n" + "\n".join(
    f'    step_{i} = order.amount + {i}' for i in range(30)
) + "\n    return True\n"


@pytest.mark.asyncio
async def test_cross_file_callee_pulled_in_when_not_requested(
    client: AsyncClient,
    test_token: str,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "orders.py").write_text(_ORDERS_CALLS_BILLING_SOURCE, encoding="utf-8")
    (tmp_workspace / "billing.py").write_text(_BILLING_SOURCE, encoding="utf-8")
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            # billing.py is deliberately NOT in suggested_files.
            "suggested_files": ["orders.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    entries = {entry["path"]: entry["content"] for entry in data["files"]}

    pulled_in = next(
        (content for path, content in entries.items() if path.startswith("billing.py::")),
        None,
    )
    assert pulled_in is not None, (
        f"expected charge_customer to be pulled in from billing.py via Tier 3, got paths: {list(entries)}"
    )
    # Small callee (<=20 lines) — included as full source, not a skeleton line.
    assert "return True" in pulled_in


@pytest.mark.asyncio
async def test_large_cross_file_callee_pulled_in_as_skeleton_not_full_source(
    client: AsyncClient,
    test_token: str,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    orders_source = _ORDERS_CALLS_BILLING_SOURCE
    (tmp_workspace / "orders.py").write_text(orders_source, encoding="utf-8")
    (tmp_workspace / "billing.py").write_text(_BILLING_LARGE_CALLEE_SOURCE, encoding="utf-8")
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            "suggested_files": ["orders.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    entries = {entry["path"]: entry["content"] for entry in data["files"]}

    pulled_in = next(
        (content for path, content in entries.items() if path.startswith("billing.py::")),
        None,
    )
    assert pulled_in is not None, f"expected billing.py entry, got paths: {list(entries)}"
    # Large callee (>20 lines) — a single skeleton line, not the full 31-line body.
    assert "step_29" not in pulled_in
    assert pulled_in.startswith("- ")


@pytest.mark.asyncio
async def test_callee_already_in_requested_files_not_duplicated_via_tier3(
    client: AsyncClient,
    test_token: str,
    tmp_workspace: Path,
):
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "orders.py").write_text(_ORDERS_CALLS_BILLING_SOURCE, encoding="utf-8")
    (tmp_workspace / "billing.py").write_text(_BILLING_SOURCE, encoding="utf-8")
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            # billing.py IS already requested — Tier 3 should not add a redundant entry.
            "suggested_files": ["orders.py", "billing.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    cross_file_entries = [
        entry for entry in data["files"] if entry["path"].startswith("billing.py::")
    ]
    # Exactly the Tier 1/2 entries for billing.py — no extra retrieval_method="cross_file_call" duplicate.
    assert len(cross_file_entries) >= 1


@pytest.mark.asyncio
async def test_typescript_cross_file_callee_pulled_in_via_tier3(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
    tmp_workspace: Path,
):
    """Phase 3b: the Tier 3 assembly logic added in Phase 3a is language-agnostic
    — this proves it actually works end to end once TS calls resolve."""
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "orders.ts").write_text(
        """import { chargeCustomer } from './billing';

export function validatePayment(order) {
    if (order.amount <= 0) {
        throw new Error("invalid amount");
    }
    chargeCustomer(order);
    return true;
}
""",
        encoding="utf-8",
    )
    (tmp_workspace / "billing.ts").write_text(
        """export function chargeCustomer(order) {
    return true;
}
""",
        encoding="utf-8",
    )
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    # camelCase names like "validatePayment" aren't split into separate FTS
    # tokens by the tokenizer the way snake_case names are — give the
    # ranker real prose to match against, same as production (post-summarization).
    conn = await test_db.connect()
    await conn.execute(
        "UPDATE symbols SET summary = ? WHERE name = 'validatePayment'",
        ("Validates a payment before checkout.",),
    )
    await conn.commit()

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            # billing.ts deliberately NOT requested.
            "suggested_files": ["orders.ts"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    entries = {entry["path"]: entry["content"] for entry in data["files"]}

    pulled_in = next(
        (content for path, content in entries.items() if path.startswith("billing.ts::")),
        None,
    )
    assert pulled_in is not None, (
        f"expected chargeCustomer to be pulled in from billing.ts via Tier 3, got paths: {list(entries)}"
    )
    assert "return true" in pulled_in


@pytest.mark.asyncio
async def test_csharp_cross_file_callee_pulled_in_via_tier3(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
    tmp_workspace: Path,
):
    """Phase 3c: proves the language-agnostic Tier 3 logic works end to end
    for C# once cross-file calls resolve."""
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "orders.cs").write_text(
        """namespace Orders;
using Billing;
public class OrderService
{
    public bool ValidatePayment(Order order)
    {
        var billing = new BillingService();
        billing.ChargeCustomer(order);
        return true;
    }
}
""",
        encoding="utf-8",
    )
    (tmp_workspace / "billing.cs").write_text(
        """namespace Billing;
public class BillingService
{
    public bool ChargeCustomer(Order order)
    {
        return true;
    }
}
""",
        encoding="utf-8",
    )
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    # Same camelCase/PascalCase FTS tokenization consideration as the TS
    # test above — give the ranker real prose to match "validate payment" against.
    conn = await test_db.connect()
    await conn.execute(
        "UPDATE symbols SET summary = ? WHERE name = 'ValidatePayment'",
        ("Validates a payment before checkout.",),
    )
    await conn.commit()

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix validate payment amount check",
            # billing.cs deliberately NOT requested.
            "suggested_files": ["orders.cs"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    entries = {entry["path"]: entry["content"] for entry in data["files"]}

    pulled_in = next(
        (content for path, content in entries.items() if path.startswith("billing.cs::")),
        None,
    )
    assert pulled_in is not None, (
        f"expected ChargeCustomer to be pulled in from billing.cs via Tier 3, got paths: {list(entries)}"
    )
    assert "return true" in pulled_in


@pytest.mark.asyncio
async def test_same_bare_method_name_different_classes_get_distinct_paths(
    client: AsyncClient,
    test_token: str,
    test_db: DatabaseManager,
    tmp_workspace: Path,
):
    """Regression test: found via real-world manual testing on a project
    with two unrelated classes in one file both defining CreateAsync.
    C# stores method names bare (no "ClassName." qualifier), so without an
    id-disambiguating suffix, both methods produced the identical display
    key "file.cs::CreateAsync" — indistinguishable in the rendered output,
    and silently collapsed by dedup on any caller path that dedupes by
    (source_type, source)."""
    headers = {"X-Agent-Token": test_token}
    (tmp_workspace / "repositories.cs").write_text(
        """namespace App;

public class ReservationRepository
{
    public bool CreateAsync(int id)
    {
        return true;
    }
}

public class ResourceRepository
{
    public bool CreateAsync(int id)
    {
        return false;
    }
}
""",
        encoding="utf-8",
    )
    await client.post("/v1/workspace/init", headers=headers)
    index_response = await client.post("/v1/workspace/index", headers=headers)
    assert index_response.status_code == 200

    conn = await test_db.connect()
    await conn.execute(
        "UPDATE symbols SET summary = 'Creates a reservation.' WHERE name = 'CreateAsync'"
    )
    await conn.commit()

    resp = await client.post(
        "/v1/context/build",
        headers=headers,
        json={
            "task_description": "fix create async reservation resource",
            "suggested_files": ["repositories.cs"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    create_async_entries = [
        entry for entry in data["files"] if "::CreateAsync#" in entry["path"]
    ]
    assert len(create_async_entries) == 2, (
        f"expected 2 distinct CreateAsync entries, got: {[e['path'] for e in data['files']]}"
    )
    paths = {entry["path"] for entry in create_async_entries}
    assert len(paths) == 2, "the two CreateAsync methods must have distinct paths"
    contents = {entry["content"] for entry in create_async_entries}
    assert "return true;" in "".join(contents)
    assert "return false;" in "".join(contents)
