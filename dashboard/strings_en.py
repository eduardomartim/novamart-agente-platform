"""English catalogue for the dashboard.

Generated as a pair with its sibling: every key here exists there, and
`tests/integration/test_i18n.py` fails the build if that stops being true.
A key missing from one catalogue, or blank in one of them, shows the reader
the wrong thing -- so both are asserted rather than assumed.

Edit both files together. `t()` falls back to Portuguese for a key the English
catalogue lacks, which keeps a page readable but is a bug, not a feature.
"""

from __future__ import annotations

from typing import Final

STRINGS: Final[dict[str, str]] = {
    "overview.lede": (
        "NovaMart uses specialised AI agents to look up customers, orders and "
        "support. An orchestrator decides which agents take part, and a "
        "<strong>Policy Engine</strong> blocks risky operations before any tool"
        " runs."
    ),
    "overview.how_it_works": "How it works",
    "overview.same_path": (
        "The same path every time. No agent runs a tool: they propose, and the "
        "Policy Engine decides."
    ),
    "overview.try_it": "Try it",
    "overview.try_lede": "A question in plain language, and the decision the system made.",
    "overview.run_this": "Run this question",
    "overview.why_english": (
        "The questions are in English because that is the language the router "
        "and the dataset use — it is literally the text that enters the system."
    ),
    "overview.demonstrates": "What this project demonstrates",
    "overview.stack_note": (
        "RAG · MCP · Kubernetes · TLS · Prometheus · 727 security tests — "
        "detailed under **Architecture**."
    ),
    "overview.what_holds": "What holds this up",
    "overview.not_built": "What was not built",
    "overview.not_built_lede": (
        "The limits sit on the same screen as the claims. A project that lists "
        "only what it does well is not verifiable."
    ),
    "overview.see_architecture": "See the architecture",
    "company.eyebrow": "Demo context",
    "company.lede": (
        "A fictional retail and e-commerce company. A simulated environment "
        "used to demonstrate how AI agents can look up customers, orders, "
        "products and tickets, and run actions guarded by policy."
    ),
    "company.customers": "Customers",
    "company.orders": "Orders",
    "company.products": "Products",
    "company.open_tickets": "Open tickets",
    "company.the_data": "The data",
    "company.what_you_can_test": "What you can test",
    "company.ids_are_real": (
        "The identifiers above are real inside the simulation. Use them in your"
        " questions — the system answers about them."
    ),
    "company.ask": "Ask",
    "result.question_label": "**Question**",
    "result.answer_label": "**Answer**",
    "result.out_of_scope": (
        "**This question is outside the scope of this demo.** No available tool"
        " answers it, and nothing was invented."
    ),
    "result.how_processed": "How the request was handled",
    "result.decision": "DECISION",
    "result.agent": "AGENT",
    "result.tool": "TOOL",
    "result.time": "TIME",
    "confirm.bound_to_args": (
        "What you approve is bound to these exact arguments: a confirmation "
        "cannot be reused for a different action."
    ),
    "confirm.approve": "Approve",
    "confirm.decline": "Decline",
    "next.try_forbidden": "Try something forbidden",
    "next.contrast": (
        "That one was allowed. The contrast is the point: now ask for a "
        "deletion and watch the Policy Engine refuse before any tool runs."
    ),
    "next.see_policies": "See the policies",
    "next.rule_listed": (
        "The rule that refused this operation is listed there, along with the "
        "matrix of who may call what."
    ),
    "orch.eyebrow": "Main demonstration",
    "orch.title": "Try the orchestrator",
    "orch.lede": (
        "Ask a question in plain language. The system decides which agents and "
        "tools are needed."
    ),
    "orch.question": "Question",
    "orch.run": "Run",
    "orch.running": "Running…",
    "orch.examples": "Examples",
    "security.eyebrow": "Controls enforced at run time",
    "security.title": "Security",
    "security.lede": (
        "Security is not merely documented; it is enforced while the request "
        "runs. The policy engine is the only authority."
    ),
    "security.four_pillars": "Four pillars",
    "security.three_decisions": "The three decisions",
    "security.try_hostile": "Try it, from simple to hostile",
    "security.scenarios_lede": (
        "Five scenarios in increasing order of difficulty. Each one really runs"
        " — the result is what the platform does, not a description of what it "
        "would do."
    ),
    "security.rules_in_full": "The policy rules, in full",
    "security.who_may_call": "Who may call what",
    "security.matrix_lede": (
        "The capability matrix. A tool missing from an agent's row is "
        "unreachable by that agent, under any circumstance."
    ),
    "security.what_to_watch": "What to watch",
    "arch.eyebrow": "How the system is assembled",
    "arch.title": "Architecture",
    "arch.lede": "One request path, one authority, and the infrastructure that holds both up.",
    "arch.request_path": "The request path",
    "arch.platform": "Platform",
    "arch.the_agents": "The agents",
    "arch.agents_lede": "Five specialised roles. None of them runs a tool.",
    "arch.policy_not_agent": (
        "The **Policy Engine** is not an agent. It is the authority that "
        "decides what any agent may do, and it cannot be talked out of it."
    ),
    "arch.also_demonstrated": "Also demonstrated",
    "arch.also_lede": "What the landing page names without describing.",
    "obs.title": "Observability",
    "obs.lede": "Every request has an identifier, timed steps and a recorded decision.",
    "obs.requests": "Requests",
    "obs.blocked": "Blocked",
    "obs.tool_calls": "Tool calls",
    "obs.avg_latency": "Average latency",
    "obs.recent_requests": "Recent requests",
    "obs.none_recorded": "No requests recorded yet.",
    "obs.no_recent": "No recent requests.",
    "obs.details": "Details",
    "obs.counters_lede": "Counters per event type, read from the persisted event stream.",
    "obs.latency_caption": "Latency per request (ms), oldest to newest",
    "obs.no_latency": "No request has recorded a latency yet, so there is no series to draw.",
    "tech.see_details": "See technical details",
    "tech.correlation_id": "Correlation ID",
    "tech.policy_decision": "Policy decision",
    "tech.agents": "Agents",
    "tech.tools": "Tools",
    "tech.full_event_sequence": "Full event sequence",
    "nav.demo_group": "**DEMONSTRATION**",
    "nav.page": "Page",
    "nav.platform_group": "⚙ **PLATFORM** — the operational instruments.",
    "nav.overview": "Overview",
    "nav.company": "Company",
    "nav.orchestrator": "Orchestrator",
    "nav.security": "Security",
    "nav.architecture": "Architecture",
    "nav.observability": "Observability",
    "nav.language": "Language",
    "mode.live_caption": "Requests are served by a real provider.",
    "mode.stub_caption": (
        "No model is being called. Answers are deterministic and produced "
        "locally."
    ),
    "table.no_rows": "No rows to show.",
    "orch.placeholder": "What is the status of order ORD-1001?",
    "sidebar.simulated": (
        "{company} is a simulated company. Every tool runs in memory; no "
        "external system is contacted."
    ),
    "mode.live_banner": (
        "<strong>Live mode.</strong> Requests are processed by the configured "
        "provider (<code>{model}</code>)."
    ),
    "mode.stub_banner": (
        "<strong>Deterministic local simulation.</strong> No external AI "
        "provider is being called, and nothing here is attributed to one."
    ),
    "flow.user": "User",
    "flow.router": "Router",
    "flow.agent": "Specialised agent",
    "flow.policy": "Policy Engine",
    "flow.tool": "Tool",
    "flow.validator": "Validator",
    "flow.answer": "Answer",
    "holds.policy": "Policy",
    "holds.policy_body": "11 rules decide before anything runs.",
    "holds.auth": "Authentication",
    "holds.auth_body": "The API refuses with 401 without a credential.",
    "holds.audit": "Audit",
    "holds.audit_body": "Every request has its id, steps and decision recorded.",
    "holds.cost": "Cost",
    "holds.cost_body": "A ceiling of {budget} provider calls per day, enforced in live mode.",
    "holds.real_provider": "Real provider",
    "holds.real_provider_body": (
        "{calls} Gemini calls already recorded in this installation's physical "
        "ledger."
    ),
    "tools.name": "Tool",
    "tools.risk": "Risk",
    "tools.proposed_by": "Proposed by",
    "tools.decision": "Decision",
    "tools.execution": "Execution",
    "result.model_calls": "Model calls in this request: {n}",
    "confirm.needs_human": (
        "**A human has to approve this.** The executor proposed `{tool}` (risk "
        "{risk}). Nothing has run — the request is suspended until you decide."
    ),
    "block.policy_title": "Operation blocked by the security policy.",
    "block.policy_body": (
        "The requested action does not carry enough authorisation. No tool ran,"
        " and the refusal was recorded."
    ),
    "block.rate_title": "Request limit reached.",
    "block.rate_body": (
        "The demo limits how many requests one visitor makes per minute. Wait a"
        " moment and try again — nothing was refused for a security reason."
    ),
    "block.budget_title": "Daily provider-call budget spent.",
    "block.budget_body": (
        "This demo sets its own ceiling on model calls and stopped before "
        "spending more. The provider is up; this was a cost decision."
    ),
    "block.circuit_title": "Circuit opened after consecutive provider failures.",
    "block.circuit_body": (
        "The platform stopped trying after repeated errors, rather than "
        "hammering a service that is down. It will retry on its own."
    ),
    "block.limits_title": "Per-request resource ceiling reached.",
    "block.limits_body": (
        "The request exceeded a per-request limit on steps, time or size. That "
        "is a cost and latency control, not a security one."
    ),
    "block.generic_title": "Operation stopped by a platform control.",
    "block.generic_body": (
        "Execution stopped before finishing. The trace below shows where and "
        "why."
    ),
    "examples.read_note": "Read-only. They just answer.",
    "examples.action_note": "They propose a change, then stop and wait for you.",
    "examples.security_note": "They are refused. Watch which rule fires.",
    "examples.read_tab": "Look up",
    "examples.action_tab": "Change something",
    "examples.security_tab": "Try to break it",
    "tabs.customers": "Customers",
    "tabs.orders": "Orders",
    "tabs.products": "Products",
    "tabs.tickets": "Tickets",
    "table.see_all": "See all ({n})",
    "pillar.auth": "Authentication",
    "pillar.auth_body": (
        "The API demands a credential. Without one, 401 — and it never comes "
        "from the request body."
    ),
    "pillar.authz": "Authorisation",
    "pillar.authz_body": (
        "Separate scopes: whoever asks for a risky action is not whoever "
        "approves it."
    ),
    "pillar.policy": "Policy Engine",
    "pillar.policy_body": "Eleven rules decide ALLOW, DENY or CONFIRM before any tool runs.",
    "pillar.audit": "Audit",
    "pillar.audit_body": (
        "Every decision is recorded with who approved it, authenticated — not "
        "self-declared."
    ),
    "decision.allow_body": "The action runs. Low risk, and within what the agent may do.",
    "decision.confirm_body": (
        "Execution stops and waits for a human. Nothing happens until someone "
        "decides."
    ),
    "decision.deny_body": "Refused. No tool is called, and the refusal is recorded.",
    "platform.k8s": "Kubernetes",
    "platform.k8s_body": "A Deployment with probes, a default-deny NetworkPolicy and an HPA.",
    "platform.redis": "Redis",
    "platform.redis_body": "Confirmations, checkpoints and limits shared across replicas.",
    "platform.postgres": "PostgreSQL",
    "platform.postgres_body": "Requests, events and spend — one ledger for every replica.",
    "platform.observability": "Observability",
    "platform.observability_body": "JSON logs, Prometheus metrics and correlation IDs.",
    "platform.netpol": "Network Policies",
    "platform.netpol_body": "Nothing reaches the API beyond what was declared.",
    "platform.tls": "TLS / Ingress",
    "platform.tls_body": "Terminated at the edge; the Service stays ClusterIP.",
    "obs.latency_note": (
        "Median {median} ms · peak {peak} ms. A lone spike is usually a request"
        " that stopped for human confirmation: the clock keeps running while it"
        " waits for the decision."
    ),
    "obs.metric": "Metric",
    "obs.value": "Value",
    "step.request_started": "Request received",
    "step.input_flagged": "Input shaped like an injection",
    "step.input_sensitive": "Input contains sensitive data",
    "step.input_rejected": "Input rejected",
    "step.rate_limited": "Rate limit applied",
    "step.route_selected": "Router classified the request",
    "step.agent_started": "Agent started",
    "step.action_proposed": "Action proposed",
    "step.llm_retry": "Provider attempt failed, retrying",
    "step.llm_failed": "Provider failed on every attempt",
    "step.policy_decision": "Policy engine decided",
    "step.confirmation_requested": "Waiting for human approval",
    "step.confirmation_resolved": "Human decision recorded",
    "step.tool_call": "Tool executed",
    "step.validation": "Result validated",
    "step.resource_limit": "Resource ceiling reached",
    "step.circuit_open": "Provider circuit opened",
    "step.prompt_redacted": "Credentials removed before egress",
    "step.output_redacted": "Response redacted",
    "step.request_completed": "Response returned",
    "step.request_failed": "Request failed",
    "step.agent_named_started": "{agent} started",
    "budget.exhausted": (
        "**Today's capacity is spent.** This demo sets its own daily ceiling on"
        " AI provider calls, and the system stopped *before* making another "
        "one.  The provider is up and nothing broke — this environment simply "
        "chose not to spend more today. The deterministic simulation is intact:"
        " same orchestration graph, same policy engine, same security controls."
    ),
    "budget.simulation": (
        "{used} of {budget} model calls today. In simulation mode they are "
        "local and consume no provider quota at all; the counter is real, and "
        "the limit only takes effect in live mode."
    ),
    "budget.running_low": (
        "**The demo is running low on capacity.** {remaining} of {budget} "
        "provider calls remain today, roughly {requests} requests."
    ),
    "budget.live_ok": (
        "Live mode. {remaining} of {budget} provider calls remain within "
        "today's ceiling, roughly {requests} requests."
    ),
}
