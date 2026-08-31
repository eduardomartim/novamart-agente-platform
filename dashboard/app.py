"""Streamlit operations dashboard.

Every figure shown here is read from the database. Where there is no data, the
page says so rather than rendering a plausible-looking placeholder -- a
dashboard that invents numbers is worse than no dashboard, and the whole point
of this project is that the observability is real.

Run with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# Support running via `streamlit run dashboard/app.py` from the repo root
# without requiring the package to be installed first.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_budget  # noqa: E402
import demo_content as demo  # noqa: E402
import execution_view  # noqa: E402

from agent_platform.config import Settings  # noqa: E402
from agent_platform.cost.pricing import (  # noqa: E402
    FREE_TIER_MODELS,
    PRICING_VERIFIED_ON,
    is_stale,
    pricing_notice,
)
from agent_platform.drift import DriftMonitor, snapshot  # noqa: E402
from agent_platform.guardrails.authorization import describe_matrix  # noqa: E402
from agent_platform.guardrails.rules import describe_rules  # noqa: E402
from agent_platform.models import AgentName  # noqa: E402
from agent_platform.observability.metrics import collect_metrics  # noqa: E402
from agent_platform.platform import AgentPlatform  # noqa: E402

st.set_page_config(page_title="Agent Platform", page_icon=":shield:", layout="wide")


@st.cache_resource
def get_platform() -> AgentPlatform:
    """One platform instance per Streamlit session."""
    return AgentPlatform(Settings.from_env())


DEFAULT_HINT = "Seed the database with `agent-platform demo`."


def no_data(message: str, *, hint: str | None = DEFAULT_HINT) -> None:
    """Empty state carrying the command that actually populates *this* page.

    The hint is a parameter rather than a constant because the pages are not
    filled by the same command: `demo` seeds traffic but records no evaluation
    run, so telling a reader to run it on the evaluation page sends them in a
    circle.
    """
    st.info(message if hint is None else f"{message}\n\n{hint}")


def provider_banner(platform: AgentPlatform) -> None:
    """State which engine is answering, in language a non-engineer can read.

    This used to lead with the name of a missing environment variable, which
    is the least useful thing a first-time visitor could be told. The fact that
    matters is whether a real model is involved.
    """
    info = platform.provider_info
    if info.live:
        st.success(
            f"**LIVE MODE** -- requests are processed by the configured AI "
            f"provider (`{info.model}`)."
        )
    else:
        st.info(
            "**STUB MODE** -- responses come from a deterministic local "
            "simulation. No AI provider is being called, and nothing here is "
            "attributed to one.",
            icon=":material/science:",
        )


# ----------------------------------------------------------------------- pages


def page_overview(platform: AgentPlatform) -> None:
    st.header("Overview")
    metrics = collect_metrics(platform.repository)

    if not metrics.has_data:
        no_data("No requests have been recorded yet.")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Requests", metrics.requests)
    c2.metric("Success rate", f"{metrics.success_rate:.0%}")
    c3.metric("Blocked actions", metrics.policy_denials)
    c4.metric("Estimated cost", f"${metrics.estimated_cost_usd:.6f}")
    c5.metric("Avg latency", f"{metrics.avg_latency_ms:.0f} ms")

    st.caption(pricing_notice())

    st.subheader("Recent requests")
    rows = platform.repository.recent_requests(limit=25)
    if rows:
        frame = pd.DataFrame(rows)[
            ["created_at", "provider", "status", "route", "latency_ms",
             "retry_count", "blocked"]
        ]
        st.dataframe(frame, width="stretch", hide_index=True)


def page_agent_flow(platform: AgentPlatform) -> None:
    st.header("Agent flow")
    st.markdown(
        "Every request follows the same path. An agent proposes; the policy "
        "engine decides; the gateway is the only component that executes."
    )
    st.code(
        "user request\n"
        "     |\n"
        "  [router]            classifies, holds no tools\n"
        "     |\n"
        "  [researcher]        read-only context gathering\n"
        "     |\n"
        "  [executor]          proposes one action\n"
        "     |\n"
        "  policy engine  -->  DENY / REQUIRE_CONFIRMATION / ALLOW\n"
        "     |\n"
        "  tool gateway        the only path to a tool\n"
        "     |\n"
        "  [validator]         deterministic checks, then optional judge\n"
        "     |\n"
        "  response",
        language="text",
    )

    st.subheader("Inspect a request trace")
    rows = platform.repository.recent_requests(limit=50)
    if not rows:
        no_data("No traces recorded yet.")
        return

    labels = {
        f"{r['created_at']}  {r['status']:22}  {r['request_id']}": r["request_id"]
        for r in rows
    }
    chosen = st.selectbox("Request", list(labels))
    events = platform.repository.events_for_request(labels[chosen])
    if not events:
        st.info("No events for this request.")
        return

    frame = pd.DataFrame(events)[
        ["sequence", "event_type", "status", "agent", "tool", "policy_decision",
         "risk_level", "rule_ids", "latency_ms"]
    ]
    st.dataframe(frame, width="stretch", hide_index=True)

    with st.expander("Event payloads (sanitised before storage)"):
        for event in events:
            st.markdown(f"**{event['sequence']}. {event['event_type']}**")
            st.json(json.loads(event["payload"] or "{}"))


def page_security(platform: AgentPlatform) -> None:
    st.header("Security")
    metrics = collect_metrics(platform.repository)

    if not metrics.has_data:
        # Without this, a fresh database renders four zeroes and two empty
        # tables, which reads as "nothing was detected" rather than "nothing
        # has run yet" -- the two are very different claims on a security page.
        no_data("No requests have been recorded, so there is nothing to report yet.")
        st.subheader("Controls that would be enforced")
        st.dataframe(pd.DataFrame(describe_rules()), width="stretch", hide_index=True)
        st.caption(
            "These rules are enforced on every request. The counters above stay "
            "at zero until traffic has been recorded."
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Policy denials", metrics.policy_denials)
    c2.metric("Confirmations required", metrics.confirmations)
    c3.metric("Injection signals", metrics.injection_flags)
    c4.metric("Sensitive inputs", metrics.sensitive_inputs)

    redactions = [
        e for e in platform.repository.recent_events(limit=400)
        if e["event_type"] == "prompt_redacted"
    ]
    if redactions:
        st.metric("Prompts redacted before egress", len(redactions))
        st.caption(
            "Credentials are stripped from every prompt before it reaches a "
            "provider. Only the category is recorded, never the value."
        )
    st.caption(
        "Injection signals and sensitive inputs are counted separately: an "
        "email address in a support request is not an attack."
    )

    events = platform.repository.recent_events(limit=400)
    denials = [e for e in events if e["policy_decision"] == "deny"
               and e["event_type"] == "policy_decision"]
    if denials:
        st.subheader("Blocked actions")
        frame = pd.DataFrame(denials)[
            ["created_at", "agent", "tool", "risk_level", "rule_ids"]
        ]
        st.dataframe(frame, width="stretch", hide_index=True)
    else:
        st.info("No blocked actions recorded yet.")

    st.subheader("Enforced policy rules")
    st.dataframe(pd.DataFrame(describe_rules()), width="stretch", hide_index=True)

    st.subheader("Capability matrix (least privilege)")
    matrix = describe_matrix()
    st.dataframe(
        pd.DataFrame(matrix).T.replace({True: "yes", False: "-"}), width="stretch"
    )
    st.caption(
        "No role holds the delete capability. Authorisation requires passing "
        "both this matrix and the tool's own allow-list."
    )


def page_evaluation(platform: AgentPlatform) -> None:
    st.header("Evaluation")
    runs = platform.repository.eval_runs(limit=20)
    if not runs:
        no_data("No evaluation runs recorded yet. Run `agent-platform eval`.", hint=None)
        return

    latest = runs[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cases", latest["case_count"])
    c2.metric("Passed", latest["passed_count"])
    c3.metric("Safety", _fmt(latest["safety"]))
    c4.metric("Overall", _fmt(latest["overall"]))

    c5, c6, c7 = st.columns(3)
    c5.metric("Correctness", _fmt(latest["correctness"]))
    c6.metric("Tool accuracy", _fmt(latest["tool_accuracy"]))
    c7.metric("Relevance", _fmt(latest["relevance"]))

    if not latest["judge_used"]:
        st.info(
            "Relevance is unavailable in this run. It requires a live model "
            "judge; the deterministic stub declines to score rather than "
            "returning a fixed number that would look like a measurement."
        )

    live_runs = [r for r in runs if r["provider"] != "stub"]
    stub_runs = [r for r in runs if r["provider"] == "stub"]
    if live_runs and stub_runs:
        st.warning(
            "This database holds both stub and live evaluation runs. Their "
            "scores are not comparable: stub runs measure the platform, live "
            "runs measure a model on this dataset."
        )
    if latest["provider"] == "stub":
        st.info(
            "The most recent run used the deterministic stub. A perfect score "
            "here means the **platform** behaved correctly given predictable "
            "model output -- it says nothing about model quality."
        )

    st.subheader("Run history")
    frame = pd.DataFrame(runs)[
        ["created_at", "provider", "model", "dataset", "case_count",
         "passed_count", "safety", "overall", "judge_used"]
    ]
    st.dataframe(frame, width="stretch", hide_index=True)


def page_cost(platform: AgentPlatform) -> None:
    st.header("Cost")
    metrics = collect_metrics(platform.repository)

    if not metrics.llm_calls:
        no_data("No model calls recorded yet.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Model calls", metrics.llm_calls)
    c2.metric("Total tokens", f"{metrics.total_tokens:,}")
    c3.metric("Estimated cost", f"${metrics.estimated_cost_usd:.6f}")
    c4.metric("Cost / request", f"${metrics.cost_per_request:.6f}")

    settings = platform.settings
    status = platform.budget_guard.status()
    st.subheader("Budget")
    st.progress(
        min(1.0, status.daily_used_fraction),
        text=(
            f"${status.daily_spent_usd:.6f} of ${status.daily_limit_usd:.2f} "
            f"daily budget used"
        ),
    )
    st.caption(
        f"Per-request cap ${settings.max_request_cost_usd:.4f}. "
        "Requests projected to exceed either limit are refused by rule PL007."
    )

    st.subheader("Spend by provider")
    breakdown = platform.repository.cost_by_provider()
    if breakdown:
        frame = pd.DataFrame(
            [
                {
                    "provider": row["provider"],
                    "model": row["model"],
                    "calls": row["calls"],
                    "tokens": row["tokens"],
                    "cost_usd": float(row["cost_usd"]),
                    "estimated": f"{row['estimated_calls']}/{row['calls']}",
                }
                for row in breakdown
            ]
        )
        st.dataframe(frame, width="stretch", hide_index=True)
        providers = {row["provider"] for row in breakdown}
        if "stub" in providers and len(providers) > 1:
            st.info(
                "This database holds both stub and live calls. Stub rows are "
                "structurally $0, so a blended cost-per-request figure would "
                "describe neither -- read the per-provider rows instead."
            )

    st.subheader("Rate card")
    if is_stale():
        st.error(
            f"The rate card was verified on {PRICING_VERIFIED_ON.isoformat()} and is "
            "now stale. Cost figures may be wrong until it is re-checked."
        )
    else:
        st.caption(pricing_notice())

    if platform.provider.model in FREE_TIER_MODELS:
        st.info(
            f"`{platform.provider.model}` has a free tier. The figures above are "
            "what these calls **would** cost at paid-tier rates, which is the "
            "number worth tracking before a project leaves the free tier."
        )


def page_reliability(platform: AgentPlatform) -> None:
    st.header("Reliability")

    # --- provider circuit ---------------------------------------------------
    st.subheader("Provider circuit")
    circuit = platform.circuit.snapshot()
    state = str(circuit["state"])
    if state == "closed":
        st.success(f"Circuit **closed** - provider calls flowing "
                   f"({circuit['consecutive_failures']} consecutive failures)")
    elif state == "half_open":
        st.warning("Circuit **half-open** - one trial call will be admitted")
    else:
        st.error(
            f"Circuit **open** after {circuit['consecutive_failures']} consecutive "
            f"failures. Calls fail fast for another "
            f"{circuit['retry_after_seconds']}s."
        )
    st.caption(
        f"Opens after {circuit['failure_threshold']} consecutive failures; "
        f"cooldown {circuit['cooldown_seconds']:.0f}s. The circuit can only "
        "prevent a provider call - it never authorises an action."
    )

    # --- hard resource limits ----------------------------------------------
    st.subheader("Resource limits")
    limits = platform.resources.limits
    st.dataframe(
        pd.DataFrame(
            [
                {"limit": "LLM calls / request", "value": limits.max_llm_calls_per_request},
                {"limit": "Tool calls / request", "value": limits.max_tool_calls_per_request},
                {"limit": "Request deadline (s)", "value": limits.request_deadline_seconds},
                {"limit": "Tool output (bytes)", "value": limits.max_tool_output_bytes},
                {"limit": "Pending confirmations", "value": limits.max_pending_confirmations},
                {
                    "limit": "Confirmation TTL (s)",
                    "value": platform.settings.confirmation_ttl_seconds,
                },
                {"limit": "Graph recursion limit", "value": platform.settings.recursion_limit},
                {"limit": "Requests / minute", "value": platform.settings.requests_per_minute},
                {"limit": "Requests / hour", "value": platform.settings.requests_per_hour},
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    c1, c2 = st.columns(2)
    c1.metric("Requests in flight", platform.resources.active_requests())
    c2.metric("Awaiting confirmation", len(platform._pending))
    st.caption(
        "These ceilings are denominated in calls, seconds and bytes rather than "
        "money, so they hold even when the provider is free. None of them can "
        "be influenced by model output."
    )

    events = platform.repository.recent_events(limit=400)
    stops = [e for e in events if e["event_type"] in ("resource_limit", "circuit_open")]
    if stops:
        st.subheader("Recent resource stops")
        st.dataframe(
            pd.DataFrame(stops)[["created_at", "event_type", "agent", "tool", "error"]],
            width="stretch",
            hide_index=True,
        )

    st.divider()
    metrics = collect_metrics(platform.repository)

    if not metrics.has_data:
        no_data("No requests recorded yet.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Avg latency", f"{metrics.avg_latency_ms:.0f} ms")
    c2.metric("Failure rate", f"{metrics.failure_rate:.0%}")
    c3.metric("Retries", metrics.retries)
    c4.metric("Tool failures", metrics.tool_failures)

    st.caption(
        f"Retries are capped at {platform.settings.max_retries} per request, and the "
        f"graph runs under a recursion limit of {platform.settings.recursion_limit}. "
        "Refused actions are never retried."
    )

    rows = platform.repository.recent_requests(limit=50)
    if rows:
        frame = pd.DataFrame(rows)
        st.subheader("Latency by request")
        st.bar_chart(frame.set_index("created_at")["latency_ms"])


def page_drift(platform: AgentPlatform) -> None:
    st.header("Drift")
    monitor = DriftMonitor(platform.repository)
    provider = platform.provider.name
    model = platform.provider.model
    st.caption(
        f"Baselines are scoped to provider and model. Showing: `{provider}` / `{model}`."
    )
    baseline = monitor.baseline(provider=provider, model=model)

    if baseline is None:
        no_data(
            f"No baseline captured for `{provider}` / `{model}`. Run "
            "`agent-platform eval` then `agent-platform baseline` while this "
            "provider is active.\n\nBaselines from a different provider are "
            "deliberately not reused: the stub and a live model differ in "
            "quality and latency by construction, so the comparison would "
            "report a dramatic regression that describes neither.",
            hint=None,
        )
        return

    latest = platform.repository.latest_eval_run() or {}
    current = snapshot(
        collect_metrics(platform.repository),
        quality=latest.get("overall"),
        safety=latest.get("safety"),
        tool_accuracy=latest.get("tool_accuracy"),
    )
    report = monitor.compare(current, provider=provider, model=model)
    if report is None:
        no_data("Baseline could not be compared.")
        return

    if report.safety_regressed:
        st.error("Safety has regressed. This dimension has zero tolerance.")
    elif report.has_regression:
        st.warning(
            f"{len(report.regressions)} dimension(s) moved beyond tolerance: "
            + ", ".join(d.name for d in report.regressions)
        )
    else:
        st.success("All tracked dimensions are within tolerance.")

    st.dataframe(
        pd.DataFrame([d.as_dict() for d in report.dimensions]),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        f"Baseline captured {report.baseline_created_at}. Drift indicates that "
        "behaviour changed; it does not identify a cause."
    )


def page_try_it(platform: AgentPlatform) -> None:
    st.header("Try the orchestrator")
    st.markdown(
        f"Ask anything about {demo.COMPANY_NAME}. The request is routed to the "
        "agent that should handle it, and the policy engine decides whether any "
        "action it proposes is allowed to run."
    )

    with st.expander("What changes between simulation and live mode?"):
        st.markdown(demo.STUB_VS_LIVE)

    st.markdown("**Examples that work against this dataset**")
    read_tab, action_tab, security_tab = st.tabs(
        ["Look something up", "Change something", "Try to break it"]
    )
    for tab, examples, note in (
        (read_tab, demo.READ_ONLY_EXAMPLES, "Read-only. These just answer."),
        (
            action_tab,
            demo.ACTION_EXAMPLES,
            "These propose a change, so they stop and wait for you.",
        ),
        (
            security_tab,
            demo.SECURITY_EXAMPLES,
            "These are refused. Watch which rule fires.",
        ),
    ):
        with tab:
            st.caption(note)
            for index, (question, hint) in enumerate(examples):
                columns = st.columns([5, 3])
                columns[0].code(question, language="text")
                if columns[1].button(
                    "Ask this", key=f"ex_{id(examples)}_{index}", width="stretch"
                ):
                    st.session_state["queued_question"] = question
                    st.rerun()
                columns[1].caption(hint)

    queued = st.session_state.pop("queued_question", None)
    text = st.text_input(
        "Request",
        value=queued or st.session_state.get("last_question")
        or "What is the status of order ORD-1001?",
    )
    st.session_state["last_question"] = text

    # The demo's own provider budget, checked *before* a request is started.
    # It can only prevent a call; it grants nothing and touches none of the
    # platform's controls. See dashboard/demo_budget.py for the measured
    # reasoning behind the number.
    budget = demo_budget.budget_state(
        platform.repository, live=platform.provider_info.live
    )
    if budget.status is demo_budget.BudgetStatus.RUNNING_LOW and budget.gating:
        st.warning(budget.message)
    else:
        st.caption(budget.message)
    if budget.exhausted:
        st.warning(budget.message)
        return

    if st.button("Run", type="primary") or queued:
        result = platform.run(text)
        st.session_state["last_result"] = {
            "request_id": result.request_id,
            "status": result.status,
            "route": result.route,
            "response": result.response,
            "pending": (
                {
                    "tool": result.awaiting_confirmation.tool,
                    "arguments": result.awaiting_confirmation.arguments,
                    "risk_level": result.awaiting_confirmation.risk_level,
                    "reason": result.awaiting_confirmation.reason,
                }
                if result.awaiting_confirmation
                else None
            ),
        }

    last: dict[str, Any] | None = st.session_state.get("last_result")
    if not last:
        return

    _status_badge(last["status"])
    st.markdown(f"**Answer**\n\n{last['response']}")
    route = last.get("route") or "-"
    st.caption(f"Handled by: `{route}`")

    if last["pending"]:
        pending = last["pending"]
        st.warning(
            f"**A human has to approve this.** The executor proposed "
            f"`{pending['tool']}` ({pending['risk_level']} risk). Nothing has "
            "run yet -- the request is suspended until you decide."
        )
        st.caption(
            "What you approve is bound to these exact arguments: a confirmation "
            "cannot be reused for a different action."
        )
        st.json(pending["arguments"])
        col1, col2 = st.columns(2)
        if col1.button("Approve", type="primary"):
            resumed = platform.confirm(
                last["request_id"], approved=True, actor="dashboard-user", source="ui"
            )
            st.session_state["last_result"] = {
                **last, "status": resumed.status,
                "response": resumed.response, "pending": None,
            }
            st.rerun()
        if col2.button("Decline"):
            resumed = platform.confirm(
                last["request_id"], approved=False, actor="dashboard-user", source="ui"
            )
            st.session_state["last_result"] = {
                **last, "status": resumed.status,
                "response": resumed.response, "pending": None,
            }
            st.rerun()

    events = platform.repository.events_for_request(last["request_id"])
    if events:
        _handling_summary(events, last.get("route"))
        st.divider()
        _render_execution(
            execution_view.build(events, status=last["status"]),
            execution_view.model_calls(events),
        )
        with st.expander("Full event table"):
            st.dataframe(
                pd.DataFrame(events)[
                    ["sequence", "event_type", "status", "agent", "tool",
                     "policy_decision", "risk_level", "rule_ids"]
                ],
                width="stretch",
                hide_index=True,
            )


# ===================================================== recruiter-facing demo


def _status_badge(status: str) -> None:
    """Render a request outcome as something a visitor can read at a glance."""
    meaning = demo.STATUS_MEANING.get(status, "")
    if status == "success":
        st.success(f"**SUCCESS** -- {meaning}")
    elif status == "awaiting_confirmation":
        st.warning(f"**CONFIRMATION REQUIRED** -- {meaning}")
    elif status in {"blocked", "rejected", "rate_limited", "declined"}:
        st.error(f"**{status.replace('_', ' ').upper()}** -- {meaning}")
    else:
        st.info(f"**{status.replace('_', ' ').upper()}** -- {meaning}")


def _state_chip(state: execution_view.State) -> str:
    """A short, readable marker per state.

    NOT REACHED is the one that matters: it is the difference between "this
    step did not happen" and "this step happened and went well", and the panel
    this replaces could not express it at all.
    """
    return {
        execution_view.State.SUCCESS: "OK",
        execution_view.State.WAITING: "WAITING",
        execution_view.State.BLOCKED: "BLOCKED",
        execution_view.State.FAILED: "FAILED",
        execution_view.State.RUNNING: "RUNNING",
        execution_view.State.NOT_REACHED: "NOT REACHED",
    }[state]


def _render_execution(view: execution_view.ExecutionView, calls: int) -> None:
    """Render what the recorded stream says happened, and only that."""

    st.markdown("#### Orchestration")
    st.caption(
        "Derived from the events this request actually recorded. A step that "
        "did not run is shown as NOT REACHED rather than omitted."
    )

    for activity in view.agents:
        chip = _state_chip(activity.state)
        detail = f" -- {activity.detail}" if activity.detail else ""
        if activity.state is execution_view.State.NOT_REACHED:
            st.markdown(
                f"&nbsp;&nbsp;`{chip}`&nbsp;&nbsp;{activity.name}{detail}"
            )
        else:
            st.markdown(
                f"&nbsp;&nbsp;`{chip}`&nbsp;&nbsp;**{activity.name}**{detail}"
            )

    if view.policy_decision:
        rules = f" ({view.policy_rules})" if view.policy_rules else ""
        st.markdown(
            f"&nbsp;&nbsp;`{view.policy_decision.replace('_', ' ').upper()}`"
            f"&nbsp;&nbsp;**policy engine**{rules} -- not an agent; the only "
            "authority on whether an action may run"
        )

    if view.blocked_by:
        st.markdown(f"&nbsp;&nbsp;Stopped by: **{view.blocked_by}**")

    if view.tools:
        st.markdown("#### Tools")
        for tool in view.tools:
            risk = f" -- {tool.risk} risk" if tool.risk else ""
            by = f", proposed by {tool.proposed_by}" if tool.proposed_by else ""
            st.markdown(
                f"&nbsp;&nbsp;`{_state_chip(tool.execution)}`&nbsp;&nbsp;"
                f"**{tool.name}**{risk}{by} -- {tool.detail}"
            )

    if view.steps:
        with st.expander("Step-by-step timeline"):
            st.caption(
                f"{len(view.steps)} recorded steps. Model calls made for this "
                f"request: {calls}. Prompts and model reasoning are never "
                "recorded, on either provider."
            )
            for step in view.steps:
                detail = f" -- {step.detail}" if step.detail else ""
                st.markdown(
                    f"`{step.sequence:>2}`&nbsp;&nbsp;{step.label}{detail}"
                )


def _handling_summary(events: list[dict[str, Any]], route: str | None) -> None:
    """A short, non-technical account of how the request was handled."""
    tools = [e["tool"] for e in events if e["event_type"] == "tool_call" and e.get("tool")]
    decisions = [
        e["policy_decision"]
        for e in events
        if e["event_type"] == "policy_decision" and e.get("policy_decision")
    ]
    columns = st.columns(3)
    columns[0].markdown(f"**Handled by**\n\n`{route or 'direct response'}`")
    columns[1].markdown(
        "**Tools used**\n\n"
        + (", ".join(f"`{t}`" for t in dict.fromkeys(tools)) if tools else "none")
    )
    if decisions:
        verdict = (
            "denied" if "deny" in decisions
            else "confirmation required" if "require_confirmation" in decisions
            else "passed"
        )
    else:
        verdict = "no action to evaluate"
    columns[2].markdown(f"**Policy check**\n\n{verdict}")


def _ask(question: str) -> None:
    """Queue a question for the request runner and jump to it."""
    st.session_state["queued_question"] = question
    st.session_state["page_choice"] = "Try a request"


def page_start_here(platform: AgentPlatform) -> None:
    st.header(f"{demo.COMPANY_NAME} -- {demo.PRODUCT_NAME}")
    st.caption(f"{demo.COMPANY_TAGLINE} -- a simulated environment")
    st.markdown(f"### {demo.PRODUCT_LINE}")
    provider_banner(platform)
    st.markdown(demo.ELEVATOR)

    st.subheader("What happens to one request")
    st.code(
        """one request
     |
  rate limit + input inspection
     |
  [router]        classifies it; holds no tools
     |
  [researcher]    reads context, if the request needs it
     |
  [executor]      proposes one action, if something must change
     |
  POLICY ENGINE   ALLOW  /  CONFIRM  /  DENY      <-- the only authority
     |
  tool gateway    the only component that can run a tool
     |
  [validator]     checks the result
     |
  safe response""",
        language="text",
    )

    st.subheader("How to test this in five steps")
    steps = [
        ("1. Find something to ask about",
         "Open **Company** for the live situation board, or **Data explorer** "
         "for every customer, order, product and ticket with its ID."),
        ("2. Ask the orchestrator",
         "Go to **Try the orchestrator** and click one of the example questions, or "
         "type your own using an ID you saw."),
        ("3. Watch the agents work",
         "Each request shows the route it took and a plain-English timeline of "
         "what happened."),
        ("4. Try something restricted",
         "Ask it to update or delete a record. Watch the policy engine stop it."),
        ("5. Try to talk it into something",
         "Use the prompt-injection example. Detection is not what saves it -- "
         "the policy engine is."),
    ]
    for title, body in steps:
        st.markdown(f"**{title}** -- {body}")

    st.subheader("What can I ask?")
    st.caption(
        "Every example below is run end to end by the test suite, so each one "
        "returns a real answer from the dataset."
    )
    for question, hint in demo.READ_ONLY_EXAMPLES[:3] + demo.SECURITY_EXAMPLES[:1]:
        columns = st.columns([5, 2])
        columns[0].code(question, language="text")
        columns[1].caption(hint)
    st.caption(
        "The full set, grouped by what it demonstrates, is on "
        "**Try the orchestrator**."
    )

    st.subheader("What can I test?")
    st.caption(
        "Five requests, easiest first. Each one exercises a different part of "
        "the system, and each is run end to end by the test suite."
    )
    for scenario in demo.SCENARIOS:
        tier = scenario["level"].split(" - ")[-1]
        with st.container(border=True):
            columns = st.columns([1, 4])
            columns[0].markdown(f"**{tier}**")
            columns[1].code(scenario["ask"], language="text")
            columns[1].caption(f"Should end as `{scenario['outcome']}`")

    st.subheader("What this project demonstrates")
    left, right = st.columns(2)
    with left:
        st.markdown("**Built and exercised here**")
        for title, detail in demo.DEMONSTRATED:
            st.markdown(f"- **{title}** -- {detail}")
    with right:
        st.markdown("**Not built -- and not claimed**")
        for title, detail in demo.NOT_BUILT:
            st.markdown(f"- **{title}** -- {detail}")
        st.caption(
            "A demo that overstates its scope is worth less than one that says "
            "plainly where it stops."
        )

    st.subheader("Demo capacity")
    budget = demo_budget.budget_state(
        platform.repository, live=platform.provider_info.live
    )
    columns = st.columns(3)
    columns[0].metric("Provider calls used today", budget.used)
    columns[1].metric("Daily demo limit", budget.budget)
    columns[2].metric("Status", str(budget.status))
    st.caption(
        "Calls to the AI provider are capped so a visitor cannot exhaust the "
        "day's quota. The simulation is never capped, because it calls no "
        "provider at all."
    )

    st.subheader("Where to go")
    c1, c2, c3 = st.columns(3)
    if c1.button("Try the orchestrator", type="primary", width="stretch"):
        st.session_state["page_choice"] = "Try a request"
        st.rerun()
    if c2.button("Explore the agents", width="stretch"):
        st.session_state["page_choice"] = "Agents"
        st.rerun()
    if c3.button("View company data", width="stretch"):
        st.session_state["page_choice"] = "Data explorer"
        st.rerun()


def page_company(platform: AgentPlatform) -> None:
    st.header(demo.COMPANY_NAME)
    st.caption(f"{demo.COMPANY_TAGLINE} -- simulated environment")

    totals = demo.company_totals()
    columns = st.columns(len(totals))
    for column, (label, value) in zip(columns, totals.items(), strict=True):
        column.metric(label, value)

    st.subheader("What you can test")
    st.caption(
        "Every row below is read from the same dataset the agents query, so "
        "anything you can see here is something you can ask about."
    )
    for title, what, example in demo.WHAT_YOU_CAN_TEST:
        with st.container(border=True):
            st.markdown(f"**{title}** -- {what}")
            st.caption(example)

    st.subheader("Customers")
    st.dataframe(
        pd.DataFrame(demo.customers_table()).head(6),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        f"{len(demo.customers_table())} customers in total -- the full list is "
        "on **Data explorer**."
    )

    st.subheader("Products")
    st.dataframe(
        pd.DataFrame(demo.products_table()).head(6),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Price, category and warranty. The catalogue holds no stock levels, "
        "so the agents cannot answer inventory questions."
    )

    st.subheader("Current operations")
    st.caption(
        "The situations worth asking about right now."
    )

    st.markdown("**Tickets that are still open**")
    tickets = demo.open_tickets()
    if tickets:
        st.dataframe(pd.DataFrame(tickets), width="stretch", hide_index=True)
        top = tickets[0]
        if st.button(
            f"Ask about {top['Ticket']}", key="ask_ticket"
        ):
            _ask(f"What is ticket {top['Ticket']} about?")
            st.rerun()
    else:
        st.caption("No open tickets in the dataset.")

    st.markdown("**Orders in transit**")
    transit = demo.orders_in_transit()
    if transit:
        st.dataframe(pd.DataFrame(transit), width="stretch", hide_index=True)
        first = transit[0]
        if st.button(f"Ask about {first['Order']}", key="ask_order"):
            _ask(f"What is the status of order {first['Order']}?")
            st.rerun()

    returned = demo.returned_shipments()
    if returned:
        st.markdown("**Shipments returned to sender**")
        st.caption("Deliveries that came back -- usually what a ticket is about.")
        st.dataframe(pd.DataFrame(returned), width="stretch", hide_index=True)


def page_data_explorer(platform: AgentPlatform) -> None:
    st.header("Data explorer")
    st.markdown(
        "These are the entities the agents can reason about. Every ID here is "
        "one you can ask about by name."
    )
    st.caption(demo.HONESTY)

    customers, orders, products, tickets = st.tabs(
        ["Customers", "Orders", "Products", "Tickets"]
    )
    with customers:
        st.dataframe(
            pd.DataFrame(demo.customers_table()), width="stretch", hide_index=True
        )
        st.caption('Try: "Tell me about customer CUS-2001"')
    with orders:
        st.dataframe(
            pd.DataFrame(demo.orders_table()), width="stretch", hide_index=True
        )
        st.caption('Try: "What is the status of order ORD-1001?"')
    with products:
        st.dataframe(
            pd.DataFrame(demo.products_table()), width="stretch", hide_index=True
        )
        st.caption(
            "The catalogue carries price and warranty, not stock levels -- so "
            "the agents cannot answer inventory questions."
        )
    with tickets:
        st.dataframe(
            pd.DataFrame(demo.tickets_table()), width="stretch", hide_index=True
        )
        st.caption('Try: "What is ticket TKT-4002 about?"')


def page_agents(platform: AgentPlatform) -> None:
    st.header("Agents")
    st.markdown(
        "Five agents and one authority. The split is the point: an agent that "
        "cannot reach a tool cannot misuse one."
    )

    registry = platform.registry if hasattr(platform, "registry") else None
    for role in demo.AGENT_ROLES:
        with st.container(border=True):
            st.markdown(f"### {role['title']}")
            st.markdown(role["job"])
            st.caption(role["holds"])
            if registry is not None:
                try:
                    specs = registry.specs_for(AgentName(role["name"]))
                    names = sorted(s["name"] for s in specs)
                except Exception:  # display only; never break the page
                    names = []
                if names:
                    st.markdown("**Tools it may propose:** " + ", ".join(
                        f"`{n}`" for n in names))
                else:
                    st.markdown("**Tools it may propose:** none")

    with st.container(border=True):
        st.markdown(f"### {demo.POLICY_ROLE['title']}")
        st.markdown(demo.POLICY_ROLE["job"])
        st.caption(demo.POLICY_ROLE["holds"])

    st.subheader("Who may reach what")
    st.caption(
        "Authorisation requires passing both this matrix and the tool's own "
        "allow-list. No role holds the delete capability."
    )
    st.dataframe(pd.DataFrame(describe_matrix()), width="stretch", hide_index=True)


def page_scenarios(platform: AgentPlatform) -> None:
    st.header("Demo scenarios")
    st.markdown(
        "Five requests that show the system doing something different each "
        "time. Run them in order; each one clicks through to the runner."
    )
    with st.expander("What changes between simulation and live mode?"):
        st.markdown(demo.STUB_VS_LIVE)
    for index, scenario in enumerate(demo.SCENARIOS):
        with st.container(border=True):
            st.caption(scenario["level"])
            st.markdown(f"### {scenario['title']}")
            st.markdown("**What to try**")
            st.code(scenario["ask"], language="text")
            st.markdown(f"**What the system should do** -- {scenario['expect']}")
            st.markdown(f"**What to watch** -- {scenario['watch']}")
            if st.button("Run this scenario", key=f"scenario_{index}"):
                _ask(scenario["ask"])
                st.rerun()


def _fmt(value: Any) -> str:
    """Format a score, distinguishing "not measured" from zero."""
    if value is None:
        return "n/a"
    return f"{float(value):.3f}"


#: Two groups, in the order a first-time visitor should meet them: the demo
#: explains what this is, the platform pages are the operational instruments.
DEMO_PAGES = {
    "Start here": page_start_here,
    "Company": page_company,
    "Data explorer": page_data_explorer,
    "Agents": page_agents,
    "Try a request": page_try_it,
    "Demo scenarios": page_scenarios,
}

PLATFORM_PAGES = {
    "Overview": page_overview,
    "Agent flow": page_agent_flow,
    "Security": page_security,
    "Evaluation": page_evaluation,
    "Cost": page_cost,
    "Reliability": page_reliability,
    "Drift": page_drift,
}

PAGES = {**DEMO_PAGES, **PLATFORM_PAGES}


def main() -> None:
    platform = get_platform()
    st.sidebar.title(demo.COMPANY_NAME)
    st.sidebar.caption(demo.PRODUCT_LINE)
    info = platform.provider_info
    if info.live:
        st.sidebar.success(f"**LIVE**\n\n{info.name} / `{info.model}`")
    else:
        st.sidebar.warning(
            f"**DEMO / STUB**\n\n`{info.model}`\n\nNo language model is being called."
        )

    options = list(PAGES)
    queued = st.session_state.get("page_choice")
    index = options.index(queued) if queued in options else 0

    def label(page: str) -> str:
        """Show the two groups in the list itself.

        `format_func` changes only what is displayed, never the value, so the
        page keys stay stable for anything selecting a page by name.
        """
        if page == "Try a request":
            return "Try the orchestrator"
        return page if page in DEMO_PAGES else f"\u2699 {page}"

    st.sidebar.caption("**DEMO** -- start here")
    # `index` is what lets an in-page button navigate: it sets page_choice and
    # reruns, and the radio comes back selecting that page.
    choice = st.sidebar.radio(
        "Page", options, index=index, format_func=label, label_visibility="collapsed"
    )
    st.session_state["page_choice"] = choice
    st.sidebar.caption(
        "\u2699 **PLATFORM** -- the operational instruments behind the demo: "
        "traces, policy decisions, evaluation, cost, reliability and drift."
    )
    st.sidebar.divider()
    st.sidebar.caption(
        f"{demo.COMPANY_NAME} is a simulated company. All tools operate in "
        "memory; no external system is contacted."
    )

    # The landing page places the mode line itself, under the product identity,
    # so a stranger meets the product before a status banner.
    if choice != "Start here":
        provider_banner(platform)
    PAGES[choice](platform)


main()
