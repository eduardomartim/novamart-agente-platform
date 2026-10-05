"""The offline evaluation fell to 65/68 and this pins why it is back at 68.

Three defects in the deterministic stub, none in the platform itself:

* "pedido 1002" was not an order identifier -- only ``ORD-`` and the English
  "order 1002" were -- so a Portuguese order lookup was declined as
  unanswerable;
* an imperative that opens a sentence ("Search", "Consultar", "Notify") was read
  as a person's name, turning a document search, an order lookup and a write
  request into customer searches; and
* a single unrecognised name refused the request before the documentation check
  could accept it.

The write-side half matters more than the score. Before the fix "Atualizar o
pedido 1002 para entregue" and "Notify bruno@... that his order shipped" were
quietly served by a read and reported success, so the confirmation step the
platform exists to demonstrate never happened.
"""

from __future__ import annotations

import pytest

from agent_platform.evaluation.evaluator import Evaluator, load_all
from agent_platform.llm.stub import StubProvider

_EXECUTOR_TOOLS = (
    '{"name": "get_order"} {"name": "search"} {"name": "update_record"} '
    '{"name": "send_email"} {"name": "find_customer"} {"name": "get_customer"}'
)


def _executor_prompt(request: str) -> str:
    """A prompt shaped like the executor's: offered tools plus fenced input."""
    return (
        f"{_EXECUTOR_TOOLS}\n<<<UNTRUSTED_USER_CONTENT\n{request}\n"
        "UNTRUSTED_USER_CONTENT>>>"
    )


@pytest.mark.parametrize(
    ("request_text", "order_id"),
    [
        ("Qual e o status do pedido 1002?", "ORD-1002"),
        ("Consultar o pedido 1001", "ORD-1001"),
        ("pedido nº 1003", "ORD-1003"),
        ("What is the status of order 1001?", "ORD-1001"),
    ],
)
def test_portuguese_and_english_order_references_carry_the_identifier(
    request_text, order_id
):
    tool, arguments = StubProvider._choose_tool(request_text)
    assert tool == "get_order"
    assert arguments == {"order_id": order_id}


@pytest.mark.parametrize(
    "request_text",
    ["Qual e o status do pedido 1002?", "Consultar o pedido 1001"],
)
def test_portuguese_order_lookups_reach_the_researcher(request_text):
    assert StubProvider._choose_route(request_text) == "researcher"


def test_a_sentence_opening_imperative_is_not_a_customer_name():
    assert StubProvider._choose_route("Search for the refund policy") == "researcher"
    assert StubProvider._choose_tool("Search for the refund policy")[0] == "search"


def test_a_single_unknown_word_still_reaches_the_documentation_check():
    """The name branch used to refuse before `shipping` could be considered."""
    assert StubProvider._servable("Explain Brazilian shipping times")


@pytest.mark.parametrize(
    ("request_text", "tool"),
    [
        ("Atualizar o pedido 1002 para entregue", "update_record"),
        ("Change order 1001 status to refunded", "update_record"),
        ("Notify bruno.carvalho@example.com that his order shipped", "send_email"),
    ],
)
def test_a_write_request_is_never_downgraded_to_a_read(request_text, tool):
    assert StubProvider._choose_route(request_text) == "executor"
    assert StubProvider._choose_tool(_executor_prompt(request_text))[0] == tool


def test_the_portuguese_update_targets_the_order_it_names():
    _tool, arguments = StubProvider._choose_tool(
        _executor_prompt("Atualizar o pedido 1002 para entregue")
    )
    assert arguments["record_id"] == "ORD-1002"


def test_a_question_about_an_unsupported_subject_is_still_guarded():
    """The write exemption must not reopen "was ORD-1001 refunded?"."""
    tool, _arguments = StubProvider._choose_tool("Was ORD-1001 refunded?")
    assert tool == "search"


def test_evaluation_cases_do_not_share_pending_confirmations(platform):
    """Every write case suspends; none may hold a slot for the next case.

    Left pending, the seventh write case of a sweep was refused by the
    per-caller pending cap and scored as a failure of the case.
    """
    evaluator = Evaluator(platform)
    write_cases = [case for case in load_all() if case.expect_confirmation]
    write_cases += [
        case for case in load_all() if case.expected_status == "awaiting_confirmation"
    ]
    assert len(write_cases) > platform.settings.effective_max_pending_per_quota_key

    scores = [evaluator.run_case(case) for case in write_cases]

    assert all(score.passed for score in scores), [
        (score.case_id, score.failures) for score in scores if not score.passed
    ]
