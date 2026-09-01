"""Construction of the LangGraph orchestration graph.

The confirmation flow is the security-critical part of this module. LangGraph's
``interrupt`` suspends execution and persists state in the checkpointer; the
application later resumes with ``Command(resume=...)``. The resumed payload
carries only *whether* a human approved and who they were. **What** they
approved is re-derived here from the action already stored in state, and the
confirmation fingerprint is recomputed from it.

That is what makes a forged confirmation useless: a model cannot inject a
confirmation into the resume channel (it does not control it), and even a
tampered resume payload cannot redirect approval onto a different action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..agent.answer import (
    Answer,
    AnswerOutcome,
    provider_error,
    resource_blocked,
)
from ..agent.answerer import AnswererAgent
from ..agent.executor import ExecutorAgent
from ..agent.researcher import ResearcherAgent
from ..agent.router import RouterAgent
from ..agent.validator import ValidatorAgent
from ..config import Settings
from ..cost.tracker import BudgetExceededError
from ..guardrails.input import InputAssessment
from ..guardrails.output import secure_output
from ..guardrails.policy import Confirmation
from ..guardrails.rules import action_fingerprint
from ..llm.budget import ProviderBudgetExhausted
from ..llm.circuit import CircuitOpenError
from ..llm.provider import LLMError
from ..models import ProposedAction
from ..observability.events import EventStatus, EventType
from ..observability.tracing import Tracer
from ..security.resources import ResourceLimitExceeded
from .routing import (
    NODE_ANSWER,
    NODE_CONFIRM,
    NODE_EXECUTE,
    NODE_RESEARCH,
    NODE_RESPOND,
    NODE_VALIDATE,
    after_confirm,
    after_execute,
    after_research,
    after_route,
    after_validate,
)
from .state import AgentState, append_error

NODE_ROUTE = "route"


@dataclass(slots=True)
class GraphDeps:
    """Request-scoped collaborators bound into the graph's nodes."""

    router: RouterAgent
    researcher: ResearcherAgent
    executor: ExecutorAgent
    validator: ValidatorAgent
    answerer: AnswererAgent
    tracer: Tracer
    settings: Settings
    input_assessment: InputAssessment | None = None
    known_secrets: tuple[str, ...] = ()
    use_judge: bool = True


def _answer_dict(answer: Answer) -> dict[str, Any]:
    """Flatten an Answer for the state, like every other record here.

    Deliberately a plain dict: the state is checkpointed and rendered, and the
    rest of it holds dicts. Note what travels and what does not -- text,
    outcome, citations and reason, but nothing a downstream node could mistake
    for permission.
    """
    return {
        "outcome": answer.outcome.value,
        "text": answer.text,
        "citations": [
            {"doc_id": citation.doc_id, "title": citation.title}
            for citation in answer.citations
        ],
        "reason": answer.reason,
        "metadata": dict(answer.metadata),
    }


def _decision_dict(result: Any) -> dict[str, Any]:
    return {
        "decision": result.decision.decision.value,
        "risk_level": result.decision.risk_level.value,
        "reason": result.decision.reason,
        "rule_ids": list(result.decision.rule_ids),
    }


def _result_dict(result: Any) -> dict[str, Any]:
    return {
        "tool": result.result.tool,
        "status": result.result.status,
        "output": result.result.output,
        "error": result.result.error,
        "latency_ms": result.result.latency_ms,
        "simulated": result.result.simulated,
    }


def build_graph(deps: GraphDeps, checkpointer: InMemorySaver) -> Any:
    """Compile the orchestration graph.

    Construction is cheap, so a graph is built per request and bound to that
    request's tracer. The checkpointer is shared, which is what allows an
    interrupted request to be resumed later.
    """

    # ------------------------------------------------------------------- nodes

    def route_node(state: AgentState) -> dict[str, Any]:
        deps.tracer.event(
            EventType.AGENT_STARTED, status=EventStatus.INFO, agent=deps.router.name
        )
        route = deps.router.route(state["user_input"])
        return {"route": route.value, "current_agent": deps.router.name.value}

    def research_node(state: AgentState) -> dict[str, Any]:
        deps.tracer.event(
            EventType.AGENT_STARTED, status=EventStatus.INFO, agent=deps.researcher.name
        )
        found, error = deps.researcher.gather(
            state["user_input"], input_assessment=deps.input_assessment
        )
        context = list(state.get("context") or []) + found
        updates: dict[str, Any] = {
            "current_agent": deps.researcher.name.value,
            "context": context[-deps.settings.max_context_items :],
        }
        if error:
            updates["errors"] = append_error(state, error)
        return updates

    def answer_node(state: AgentState) -> dict[str, Any]:
        """Write an answer from what the researcher gathered.

        Reachable only from the read path, and its only exit is RESPOND. It
        holds no tools and asks the policy engine nothing, because it decides
        nothing -- it turns documents into prose.

        The exception handling below is the whole reason this lives in the node
        rather than in the agent. Only a caller that has caught a *specific*
        exception may say what went wrong, and the distinction it preserves is
        the one that matters most here: "the knowledge base does not cover
        that" and "the model was unreachable" are different facts, and a user
        shown the first when the second is true has been misled about the
        corpus. So each named failure becomes its own outcome, and anything
        unrecognised is left to propagate rather than being flattened into a
        polite non-answer.
        """
        deps.tracer.event(
            EventType.AGENT_STARTED, status=EventStatus.INFO, agent=deps.answerer.name
        )
        context = list(state.get("context") or [])
        errors = list(state.get("errors") or [])

        # An empty context has two very different causes, and the answerer
        # cannot tell them apart: retrieval ran and found nothing, or retrieval
        # did not run. Only the second leaves an error behind.
        #
        # Without this branch the second becomes "I could not find enough
        # information in the available documents" -- a confident claim about
        # the corpus derived from an infrastructure fault. Reproduced before
        # the fix by making the search tool raise: the request reported
        # success, and the user was told the knowledge base had no answer.
        #
        # On this path ``errors`` can only have been written by
        # ``research_node``; the executor's is on the action branch, which
        # never reaches here. A test pins that assumption.
        if errors and not context:
            answer = provider_error(f"retrieval did not complete: {errors[-1]}")
            deps.tracer.event(
                EventType.ANSWER_COMPOSED,
                status=EventStatus.FAILURE,
                agent=deps.answerer.name,
                payload={
                    "outcome": answer.outcome.value,
                    "citations": [],
                    "document_count": 0,
                    "structured": False,
                    "reason": answer.reason,
                },
            )
            return {
                "answer": _answer_dict(answer),
                "current_agent": deps.answerer.name.value,
            }

        try:
            answer = deps.answerer.answer(state["user_input"], context)
        except ProviderBudgetExhausted as exc:
            # Checked before LLMError: it *is* one, but it is a decision not to
            # spend rather than a provider that failed.
            answer = resource_blocked(f"provider budget exhausted: {exc}")
        except (CircuitOpenError, ResourceLimitExceeded, BudgetExceededError) as exc:
            answer = resource_blocked(f"{type(exc).__name__}: {exc}")
        except LLMError as exc:
            answer = provider_error(f"{type(exc).__name__}: {exc}")

        deps.tracer.event(
            EventType.ANSWER_COMPOSED,
            status=(
                EventStatus.SUCCESS
                if answer.outcome is AnswerOutcome.GROUNDED
                else EventStatus.INFO
            ),
            agent=deps.answerer.name,
            payload={
                # Identifiers and counts only. The answer text is user-facing
                # output and reaches the trace through the response path, which
                # already redacts; duplicating it here would put unredacted
                # model prose in the event store.
                "outcome": answer.outcome.value,
                "citations": list(answer.cited_ids),
                "document_count": answer.metadata.get("document_count", 0),
                # Whether the model honoured the structured contract. Recorded
                # because it decides whether the answer is shown at all: an
                # unstructured answer cannot have its support verified, so the
                # response falls back to the deterministic summary. Without
                # this in the trace, "grounded but not shown" would look like
                # an inconsistency rather than a deliberate refusal to claim.
                "structured": bool(answer.metadata.get("structured", False)),
                "reason": answer.reason,
            },
        )

        return {"answer": _answer_dict(answer), "current_agent": deps.answerer.name.value}

    def execute_node(state: AgentState) -> dict[str, Any]:
        deps.tracer.event(
            EventType.AGENT_STARTED, status=EventStatus.INFO, agent=deps.executor.name
        )
        # Re-entering this node means the validator rejected the last attempt.
        is_retry = state.get("validation") is not None
        retry_count = state.get("retry_count", 0) + (1 if is_retry else 0)
        if is_retry:
            deps.tracer.event(
                EventType.RETRY,
                status=EventStatus.INFO,
                agent=deps.executor.name,
                payload={"attempt": retry_count},
            )

        action = deps.executor.propose(state["user_input"], list(state.get("context") or []))
        if action is None:
            return {
                "current_agent": deps.executor.name.value,
                "retry_count": retry_count,
                "errors": append_error(state, "executor did not produce a valid action"),
                "status": "failed",
            }

        outcome = deps.executor.act(action, input_assessment=deps.input_assessment)
        updates: dict[str, Any] = {
            "current_agent": deps.executor.name.value,
            "retry_count": retry_count,
            "proposed_action": {"tool": action.tool, "arguments": action.arguments},
            "policy_decision": _decision_dict(outcome),
            "validation": None,
        }

        if outcome.awaiting_confirmation:
            updates["pending_confirmation"] = {
                "tool": action.tool,
                "arguments": action.arguments,
                "risk_level": outcome.decision.risk_level.value,
                "reason": outcome.decision.reason,
                "fingerprint": outcome.fingerprint,
            }
            return updates

        updates["pending_confirmation"] = None
        updates["tool_result"] = _result_dict(outcome)
        return updates

    def confirm_node(state: AgentState) -> dict[str, Any]:
        pending = dict(state.get("pending_confirmation") or {})

        # Execution suspends here. Everything above this line has already run
        # and is not repeated on resume, so the proposal is not re-generated
        # (and not re-billed) while waiting for a human.
        response = interrupt(
            {
                "type": "confirmation_required",
                "tool": pending.get("tool"),
                "arguments": pending.get("arguments"),
                "risk_level": pending.get("risk_level"),
                "reason": pending.get("reason"),
            }
        )

        approved = False
        actor = "unknown"
        source = "unknown"
        if isinstance(response, dict):
            approved = bool(response.get("approved", False))
            actor = str(response.get("actor", "unknown"))[:64]
            source = str(response.get("source", "unknown"))[:32]
        elif isinstance(response, bool):
            approved = response

        # The action is rebuilt from platform-held state, never from the resume
        # payload, so an approval cannot be redirected onto a different action.
        action = ProposedAction(
            tool=str(pending.get("tool", "")),
            arguments=dict(pending.get("arguments") or {}),
        )

        deps.tracer.event(
            EventType.CONFIRMATION_RESOLVED,
            status=EventStatus.SUCCESS if approved else EventStatus.BLOCKED,
            agent=deps.executor.name,
            tool=action.tool,
            payload={"approved": approved, "actor": actor, "source": source},
        )

        if not approved:
            return {
                "pending_confirmation": None,
                "tool_result": {
                    "tool": action.tool,
                    "status": "denied",
                    "output": None,
                    "error": "the action was declined by a human reviewer",
                    "latency_ms": 0.0,
                    "simulated": True,
                },
                "status": "declined",
            }

        confirmation = Confirmation(
            approved=True,
            source=source,
            actor=actor,
            action_fingerprint=action_fingerprint(action),
        )
        # Policy is evaluated a second time. Budget, rate limits and risk may
        # all have changed while the request sat waiting for a human.
        outcome = deps.executor.act(
            action, input_assessment=deps.input_assessment, confirmation=confirmation
        )
        return {
            "pending_confirmation": None,
            "policy_decision": _decision_dict(outcome),
            "tool_result": _result_dict(outcome),
        }

    def validate_node(state: AgentState) -> dict[str, Any]:
        deps.tracer.event(
            EventType.AGENT_STARTED, status=EventStatus.INFO, agent=deps.validator.name
        )
        raw_result = state.get("tool_result") or {}
        from ..models import ToolResult  # local import avoids a cycle at module load

        result = ToolResult(
            tool=str(raw_result.get("tool", "")),
            status=raw_result.get("status", "error"),
            output=raw_result.get("output"),
            error=raw_result.get("error"),
            latency_ms=float(raw_result.get("latency_ms", 0.0)),
            simulated=bool(raw_result.get("simulated", True)),
        )
        proposed = state.get("proposed_action") or {}
        action = ProposedAction(
            tool=str(proposed.get("tool", "")), arguments=dict(proposed.get("arguments") or {})
        )

        outcome = deps.validator.validate(
            user_input=state["user_input"],
            action=action,
            result=result,
            use_judge=deps.use_judge,
        )
        return {
            "current_agent": deps.validator.name.value,
            "validation": {
                "approved": outcome.approved,
                "reasons": outcome.reasons,
                "deterministic_passed": outcome.deterministic_passed,
                "judge_used": outcome.judge_used,
            },
        }

    def respond_node(state: AgentState) -> dict[str, Any]:
        text, status = _compose_response(state)

        assessment = secure_output(
            text, known_secrets=deps.known_secrets, max_chars=4000
        )
        if assessment.redacted or assessment.blocked:
            deps.tracer.event(
                EventType.OUTPUT_REDACTED,
                status=EventStatus.BLOCKED if assessment.blocked else EventStatus.INFO,
                payload={"findings": list(assessment.findings), "blocked": assessment.blocked},
            )

        return {
            "final_response": assessment.text,
            "status": "blocked" if assessment.blocked else status,
        }

    # -------------------------------------------------------------------- wiring

    builder: Any = StateGraph(AgentState)
    builder.add_node(NODE_ROUTE, route_node)
    builder.add_node(NODE_RESEARCH, research_node)
    builder.add_node(NODE_ANSWER, answer_node)
    builder.add_node(NODE_EXECUTE, execute_node)
    builder.add_node(NODE_CONFIRM, confirm_node)
    builder.add_node(NODE_VALIDATE, validate_node)
    builder.add_node(NODE_RESPOND, respond_node)

    builder.add_edge(START, NODE_ROUTE)
    builder.add_conditional_edges(
        NODE_ROUTE,
        after_route,
        {
            NODE_RESEARCH: NODE_RESEARCH,
            NODE_EXECUTE: NODE_EXECUTE,
            NODE_RESPOND: NODE_RESPOND,
        },
    )
    builder.add_conditional_edges(
        NODE_RESEARCH,
        after_research,
        {NODE_EXECUTE: NODE_EXECUTE, NODE_ANSWER: NODE_ANSWER},
    )
    # Unconditional, and the only edge out of ANSWER. The answer node cannot
    # reach EXECUTE, CONFIRM, VALIDATE or the retry loop -- not because it
    # declines to, but because no edge exists.
    builder.add_edge(NODE_ANSWER, NODE_RESPOND)
    builder.add_conditional_edges(
        NODE_EXECUTE,
        after_execute,
        {
            NODE_CONFIRM: NODE_CONFIRM,
            NODE_VALIDATE: NODE_VALIDATE,
            NODE_RESPOND: NODE_RESPOND,
        },
    )
    builder.add_conditional_edges(
        NODE_CONFIRM,
        after_confirm,
        {NODE_VALIDATE: NODE_VALIDATE, NODE_RESPOND: NODE_RESPOND},
    )
    builder.add_conditional_edges(
        NODE_VALIDATE,
        lambda state: after_validate(state, max_retries=deps.settings.max_retries),
        {NODE_EXECUTE: NODE_EXECUTE, NODE_RESPOND: NODE_RESPOND},
    )
    builder.add_edge(NODE_RESPOND, END)

    return builder.compile(checkpointer=checkpointer)


def _compose_response(state: AgentState) -> tuple[str, str]:
    """Build the user-facing answer from what actually happened.

    Every branch reports the real outcome. A refusal is stated as a refusal; a
    simulated side effect is labelled simulated.
    """
    decision = state.get("policy_decision") or {}
    result = state.get("tool_result") or {}
    validation = state.get("validation") or {}
    context = state.get("context") or []

    if state.get("status") == "declined":
        return (
            "The action was not carried out because it was declined during review.",
            "declined",
        )

    if decision.get("decision") == "deny":
        rules = ", ".join(decision.get("rule_ids") or []) or "policy"
        return (
            f"That request was refused by the platform's policy engine ({rules}). "
            f"Reason: {decision.get('reason', 'not permitted')}",
            "blocked",
        )

    if result.get("status") == "success":
        body = _summarise_output(result.get("output"))
        note = ""
        if result.get("simulated"):
            note = " (this tool is simulated: no external system was contacted)"
        if validation and not validation.get("approved", True):
            reasons = "; ".join(validation.get("reasons") or [])
            return (
                f"The action completed but did not pass validation ({reasons}). "
                f"Result: {body}{note}",
                "failed",
            )
        return f"{body}{note}", "success"

    if result:
        return (
            f"The action could not be completed: {result.get('error') or result.get('status')}.",
            "failed",
        )

    answer = state.get("answer") or {}
    outcome = answer.get("outcome")

    if outcome == AnswerOutcome.GROUNDED.value:
        # Only reachable for a structured, checked answer: Answer.__post_init__
        # refuses to construct a grounded answer any other way.
        citations = answer.get("citations") or []
        sources = "; ".join(str(c.get("title")) for c in citations)
        suffix = f" (source: {sources})" if sources else ""
        return f"{answer.get('text', '')}{suffix}", "success"

    if outcome == AnswerOutcome.INSUFFICIENT_EVIDENCE.value and _asked_for_documents(
        context
    ):
        # A knowledge-base question that retrieved nothing. The answerer's
        # message is the correct one: the corpus genuinely does not cover it.
        return str(answer.get("text") or ""), "success"

    # Everything else falls through to the deterministic summary below -- the
    # behaviour this platform had before the answer node existed.
    #
    # UNVERIFIABLE, PROVIDER_ERROR and RESOURCE_BLOCKED are recorded as three
    # different outcomes because they are three different facts, but none of
    # them licenses showing the user an answer: one was never checked, and the
    # other two never happened. Saying "no article covers that" here would turn
    # any of them into a false claim about the corpus.
    #
    # INSUFFICIENT_EVIDENCE reaches here too when the request was a record
    # lookup rather than a knowledge-base question -- see the guard above.

    if context:
        return _summarise_output(context[-1].get("data")), "success"

    errors = state.get("errors") or []
    if errors:
        return f"The request could not be completed: {errors[-1]}", "failed"

    # Nothing ran and nothing was retrieved. That is not a completed request,
    # and reporting `success` here is how an unanswerable question came back
    # looking answered. The status says so and the sentence says why.
    return (
        "Não consigo responder essa pergunta com os dados e as ferramentas "
        "disponíveis nesta demonstração. Consigo consultar clientes, pedidos, "
        "tickets e a base de conhecimento — inclusive totais e rankings.",
        "declined",
    )


def _asked_for_documents(context: list[dict[str, Any]]) -> bool:
    """Was the last thing gathered a document search, or a record lookup?

    This decides whose words the user sees when the answer node reports
    ``INSUFFICIENT_EVIDENCE``, and the two cases are not alike:

    * a knowledge-base question that retrieved nothing -- the answerer's "I
      could not find enough information in the available documents" is exactly
      right;
    * a record lookup -- ``get_order`` and friends return ``{"found": True,
      "order": {...}}``, which is not document-shaped, so the answer node
      truthfully reports having no *documents* to answer from. Showing that
      message to someone who asked about an order tells them the platform has
      no information about a record it just read successfully.

    The second case is a regression introduced when the answer node was added:
    ``INSUFFICIENT_EVIDENCE`` short-circuited the deterministic summary that
    had always handled these, and five of the six read-only tools started
    denying data they had in hand.

    ``search`` is the only tool whose output carries ``results``; every other
    read tool returns ``found`` plus a record. That shape is produced by the
    tool layer, never by a model, so it is safe to key on. An empty context is
    treated as a document question because there is nothing else it could be.
    """
    if not context:
        return True
    data = context[-1].get("data")
    return isinstance(data, dict) and "results" in data


def _summarise_output(output: Any) -> str:
    """Render a tool output as readable text without inventing detail."""
    if output is None:
        return "No data was returned."
    if isinstance(output, dict):
        # A tool that wrote its own sentence wins over every rule below. The
        # branches that follow each know the shape of one record tool, and an
        # aggregate result matches none of them -- it used to fall through to
        # `str(output)`, putting a Python dict on screen. Rather than teach
        # this function a new shape per tool, a tool may hand back the sentence
        # it wants read. The structured fields stay in the payload for the
        # trace; only the rendering changes.
        summary = output.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        if output.get("found") is False:
            identifier = (
                output.get("order_id") or output.get("customer_id") or "the requested record"
            )
            return f"No record was found for {identifier}."
        if "order" in output:
            order = output["order"]
            return (
                f"Order {order.get('order_id')} is {order.get('status')}, "
                f"total R$ {order.get('total_brl')}, tracking "
                f"{order.get('tracking') or 'not yet assigned'}."
            )
        if "matches" in output and "query" in output:
            matches = output.get("matches") or []
            if not matches:
                return (
                    f"No customer matching {output.get('query')!r} was found."
                )
            first = matches[0]
            orders = first.get("orders") or []
            lead = (
                f"{first.get('name')} ({first.get('customer_id')}) is a "
                f"{first.get('tier')} tier customer in {first.get('city')}."
            )
            if not orders:
                return f"{lead} They have no orders on record."
            listed = "; ".join(
                f"{o['order_id']} ({o['status']}, R$ {o['total_brl']})"
                for o in orders[:5]
            )
            more = "" if len(orders) <= 5 else f" and {len(orders) - 5} more"
            extra = (
                "" if len(matches) == 1
                else f" {len(matches) - 1} other customer(s) also matched."
            )
            return f"{lead} They have {len(orders)} order(s): {listed}{more}.{extra}"
        if "customer" in output:
            customer = output["customer"]
            return (
                f"Customer {customer.get('customer_id')} is {customer.get('name')} "
                f"({customer.get('tier')} tier, since {customer.get('since')})."
            )
        if "orders" in output and "order_count" in output:
            orders = output.get("orders") or []
            if not orders:
                return f"Customer {output.get('customer_id')} has no orders."
            lines = "; ".join(
                f"{o.get('order_id')} ({o.get('status')}, R$ {o.get('total_brl')})"
                for o in orders[:5]
            )
            more = "" if len(orders) <= 5 else f" and {len(orders) - 5} more"
            return (
                f"Customer {output.get('customer_id')} has {output.get('order_count')} "
                f"order(s): {lines}{more}."
            )
        if "ticket" in output:
            ticket = output["ticket"]
            resolution = ticket.get("resolution")
            tail = (
                f" Resolution: {resolution}"
                if resolution
                else " No resolution has been recorded yet."
            )
            return (
                f"Ticket {ticket.get('ticket_id')} on order {ticket.get('order_id')} "
                f"is {ticket.get('status')} ({ticket.get('priority')} priority): "
                f"{ticket.get('subject')}.{tail}"
            )
        if "results" in output:
            hits = output.get("results") or []
            if not hits:
                return "The knowledge base returned no matching articles."
            titles = "; ".join(str(h.get("title")) for h in hits)
            return f"Found {len(hits)} relevant article(s): {titles}."
        if output.get("updated"):
            return (
                f"Updated {output.get('field')} on {output.get('record_id')} "
                f"from {output.get('previous_value')!r} to {output.get('new_value')!r}."
            )
        if "status" in output and "to" in output:
            return f"Message prepared for {output.get('to')}: {output.get('status')}."
    return str(output)[:800]
