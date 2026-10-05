"""One visitor's approved update must not change what another visitor reads.

The simulated HDstore records were module globals. On the public dashboard every
visitor shares one process, so an update one visitor approved rewrote the order
for everyone until the process restarted -- and because the stub proposed the
literal value "updated", the next visitor's lookup read back "está updated".

The dashboard now runs each session against its own :class:`WorkingSet` through
:func:`dataset_scope`. The process copy, which the CLI, the API and the tests
use, behaves exactly as before.
"""

from __future__ import annotations

import threading

from agent_platform.i18n import use_locale
from agent_platform.tools import fake_tools
from agent_platform.tools.fake_tools import WorkingSet, dataset_scope

ORDER = "ORD-1002"
WRITE = "Update order 1002 status to delivered"


def _approve(platform, request: str) -> None:
    suspended = platform.run(request)
    assert suspended.status == "awaiting_confirmation"
    resumed = platform.confirm(suspended.request_id, approved=True, actor="visitor")
    assert resumed.status == "success", resumed.response


def test_an_approved_update_stays_inside_the_visitors_scope(platform):
    original = fake_tools._orders[ORDER]["status"]
    alice, bob = WorkingSet(), WorkingSet()

    with dataset_scope(alice):
        _approve(platform, WRITE)

    assert alice.orders[ORDER]["status"] == "delivered"
    assert bob.orders[ORDER]["status"] == original
    assert fake_tools._orders[ORDER]["status"] == original, "the process copy changed"


def test_reads_inside_a_scope_see_that_scopes_writes(platform):
    alice = WorkingSet()
    with dataset_scope(alice):
        _approve(platform, WRITE)
        with use_locale("pt"):
            answer = platform.run("Qual e o status do pedido 1002?").response
    assert "entregue" in answer


def test_the_scope_follows_the_request_onto_worker_threads(platform):
    """Two visitors on two threads, concurrently, never see each other's copy."""
    scopes = {"a": WorkingSet(), "b": WorkingSet()}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def visit(name: str, request: str) -> None:
        try:
            barrier.wait(timeout=10)
            with dataset_scope(scopes[name]):
                _approve(platform, request)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [
        threading.Thread(target=visit, args=("a", WRITE)),
        threading.Thread(
            target=visit, args=("b", "Update order 1002 status to cancelled")
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, errors
    assert scopes["a"].orders[ORDER]["status"] == "delivered"
    assert scopes["b"].orders[ORDER]["status"] == "cancelled"


def test_the_stub_proposes_the_status_the_request_names(platform):
    suspended = platform.run("Atualizar o pedido 1002 para entregue")
    assert suspended.awaiting_confirmation is not None
    assert suspended.awaiting_confirmation.arguments["value"] == "delivered"


def test_an_unrecognised_status_is_quoted_not_passed_off_as_a_word(platform):
    working_set = WorkingSet()
    working_set.orders[ORDER]["status"] = "updated"
    with dataset_scope(working_set):
        with use_locale("pt"):
            pt = platform.run("Qual e o status do pedido 1002?").response
        with use_locale("en"):
            en = platform.run("What is the status of order 1002?").response
    assert "está updated" not in pt
    assert "“updated”" in pt
    assert "is updated" not in en
    assert "“updated”" in en
