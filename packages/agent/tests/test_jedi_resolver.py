"""Regression test for JediResolver.resolve() — proves cross-file call
resolution actually works end to end.

jedi.Script()'s `source` kwarg was renamed to `code` in Jedi >=0.18; the
resolver was still calling it with `source=`, which raised TypeError on
every invocation, silently caught by a broad except-return-[] in resolve().
This meant Python cross-file "calls" relationships were NEVER resolved in
this environment despite `available` reporting True — no existing test
caught it because none exercised a real cross-file goto() resolution.
"""

from __future__ import annotations

from pathlib import Path

from agent.jedi_resolver import JediResolver

_ORDERS_SOURCE = '''\
from billing import charge_customer


def validate_payment(order):
    if order.amount <= 0:
        raise ValueError("invalid amount")
    charge_customer(order)
    return True
'''

_BILLING_SOURCE = '''\
def charge_customer(order):
    return True
'''


def test_resolve_finds_cross_file_call_target(tmp_path: Path):
    (tmp_path / "orders.py").write_text(_ORDERS_SOURCE, encoding="utf-8")
    (tmp_path / "billing.py").write_text(_BILLING_SOURCE, encoding="utf-8")

    resolver = JediResolver(str(tmp_path))
    assert resolver.available, "Jedi should be available with the package installed"

    # Position at the last character of "charge_customer" on its call line
    # (line 7, 1-indexed; "    charge_customer(order)" — 0-indexed column of
    # the final 'r').
    call_line = 7
    call_col = len("    charge_customer") - 1

    results = resolver.resolve(
        source=_ORDERS_SOURCE,
        abs_file_path=str(tmp_path / "orders.py"),
        call_sites=[(call_line, call_col, "rel-1")],
    )

    assert len(results) == 1
    assert results[0].bare_name == "charge_customer"
    assert results[0].module_path == str(tmp_path / "billing.py")


def test_resolve_returns_empty_for_unavailable_jedi():
    resolver = JediResolver("/nonexistent/workspace/path/that/does/not/exist")
    # Even if Jedi itself is importable, an unresolvable project path should
    # not raise — resolve() must degrade gracefully to no results.
    results = resolver.resolve(source="x = 1", abs_file_path="/tmp/x.py", call_sites=[])
    assert results == []
