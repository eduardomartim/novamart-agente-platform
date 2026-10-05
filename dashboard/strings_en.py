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
        "NovaMart is an enterprise AI agent orchestrator. It reads a request "
        "written in plain language, decides which specialised agents take it "
        "on, reaches tools and data, and a <strong>Policy Engine</strong> "
        "decides what may run — before any tool runs."
    ),
    "hero.chip_autonomy": "Autonomy",
    "hero.chip_governance": "Governance",
    "hero.chip_security": "Security",
    "hero.chip_observability": "Observability",
    "hero.chip_scale": "Scalability",
    "scene.live": "Live (demo)",
    "ov.env_title": "Demonstration environment",
    "ov.tech_title": "Technologies used",
    "ov.state_title": "State of this installation",
    "ov.state_mode": "Mode",
    "ov.state_requests": "Requests recorded",
    "ov.state_provider": "Provider calls in the ledger",
    "ov.state_rules": "Active policies",
    "ov.state_note": (
        "Numbers read from this installation's database and code. There is no "
        "uptime, SLA or user count here because this project has none of the "
        "three."
    ),
    "hero.headline": "Enterprise AI Agent Orchestration Platform",
    "hero.sub": (
        "NovaMart orchestrates specialised agents to carry out business work "
        "under governance. The orchestrator decides who steps in, a "
        "<strong>Policy Engine</strong> authorises before anything runs, a "
        "gateway is the only route to the tools, and every step is recorded."
    ),
    "hero.cta_explore": "Explore the platform",
    "hero.cta_arch": "See the architecture",
    "metric.rules": "policies",
    "metric.rules_sub": "They decide before anything runs",
    "metric.agents": "agents",
    "metric.agents_sub": "Router, researcher, executor, validator, answerer",
    "metric.tools": "tools",
    "metric.tools_sub": "Reachable only through the gateway",
    "metric.audit": "Audit",
    "metric.audit_head": "per request",
    "metric.audit_sub": "Id, timed steps and decision recorded",
    "scene.title": "Architecture in action",
    "scene.trace_title": "Live execution",
    "scene.demo_tag": "Demonstration",
    "scene.now": "Scenario",
    "scene.note": (
        "Ten staged workflows, on repeat. The specialised agents represent the "
        "architecture's capacity for specialisation — the backend has five "
        "agents, named under Architecture. The integration systems are "
        "possible connection points; none is connected, and nothing here makes "
        "an external call. For a real execution, use the Orchestrator."
    ),
    "scene.idle": "Agent messages appear here.",
    "scene.verdict_idle": "The outcome appears when the scenario ends.",
    "scene.leg_running": "Running",
    "scene.leg_success": "Done",
    "scene.leg_waiting": "Awaiting approval",
    "scene.leg_blocked": "Blocked",
    "scene.caption": (
        "A platform working through different workflows — not a chatbot "
        "answering questions."
    ),
    "overview.problem": "The problem it solves",
    "overview.problem_body": (
        "An operations team spends all day receiving requests written by "
        "people: simple lookups, changes that must not happen without a human "
        "saying yes, and attempts to talk the system into something it should "
        "refuse. A language model on its own does not separate the three, "
        "because whatever reads the request is also what would decide to grant "
        "it. NovaMart splits those apart: agents propose, and authorisation is "
        "decided outside them, from the tool's metadata — never from what the "
        "model claims."
    ),
    "overview.demo_env": (
        "Demonstrated on <strong>HDstore</strong>, a fictional retailer "
        "created only for that. The data is simulated; the platform is real."
    ),
    "overview.how_it_works": "How it works",
    "overview.same_path": (
        "The same path every time. No agent runs a tool: they propose, and the "
        "Policy Engine decides."
    ),
    "overview.try_it": "Try it",
    "overview.try_lede": "A question in plain language, and the decision the system made.",
    "overview.run_this": "Run this question",
    "overview.ask_any_language": (
        "Ask in Portuguese or English — the router understands both. "
        "The answer follows the interface language."
    ),
    "overview.demonstrates": "What this project demonstrates",
    "overview.stack_note": (
        "RAG · MCP · Kubernetes · TLS · Prometheus · 840+ security tests — "
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
    "company.lede": "The business environment the platform is tested against.",
    "company.fictional_title": "HDstore is a fictional company",
    "company.fictional_body": (
        "It was created solely to demonstrate what NovaMart does and what it "
        "can do. **It is not a real company and it is not a real customer.** "
        "Every customer, order, product, ticket, shipment and article below is "
        "generated and deterministic. They exist to give the orchestrator a "
        "realistic business context to be tested in — and so that the "
        "questions you ask have a verifiable answer."
    ),
    "company.tab_customers": "Customers",
    "company.tab_orders": "Orders",
    "company.tab_products": "Products",
    "company.tab_tickets": "Tickets",
    "company.tab_shipments": "Shipments",
    "company.tab_kb": "Knowledge base",
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
        "Ask a business question and watch NovaMart read the request, choose "
        "the route and the tools, coordinate the agents, reach the data, apply "
        "governance and build the answer. What appears below is the real "
        "execution — no step is staged."
    ),
    "orch.question": "Question",
    "orch.run": "Run",
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
    "arch.current": "Current demo",
    "arch.current_lede": (
        "What runs in this installation today. Every box exists in the code and "
        "is exercised by the tests."
    ),
    "arch.production": "Production architecture",
    "arch.production_lede": (
        "How the same platform would connect to a real company. "
        "<strong>Nothing on this line is implemented</strong> — it is the "
        "intended integration architecture, drawn here to show where a "
        "customer's systems would attach. What changes is where the data comes "
        "from; the middle — agents, policy, gateway — is the same as the line "
        "above."
    ),
    "flow.simdata": "Simulated data",
    "flow.tools_plural": "Tools",
    "flow.realco": "Real company",
    "flow.sources": "APIs · Webhooks · Databases · ERP · CRM · SaaS · Documents",
    "flow.layer": "Integration and data layer",
    "flow.authtools": "Authorised tools",
    "arch.the_agents": "Agents",
    "arch.group_governance": "Governance and services",
    "arch.group_governance_lede": (
        "They decide and they execute. None of the three is an agent, and that "
        "separation is what keeps authorisation from depending on anything the "
        "model says."
    ),
    "arch.group_data": "Data",
    "arch.group_data_lede": "What the agents can reach, and nothing beyond it.",
    "arch.group_infra": "Infrastructure and state",
    "arch.group_observability": "Observability",
    "svc.policy": "Policy Engine",
    "svc.policy_body": (
        "Eleven rules decide ALLOW, CONFIRM or DENY from the tool's metadata. "
        "It never reads the prompt."
    ),
    "svc.gateway": "Gateway",
    "svc.gateway_body": (
        "The only route to a tool. A direct call raises "
        "DirectToolInvocationError instead of running."
    ),
    "svc.mcp": "MCP",
    "svc.mcp_body": (
        "Tools sit behind a process boundary, reached with signed single-use "
        "grants."
    ),
    "data.dataset": "HDstore dataset",
    "data.dataset_body": (
        "Customers, orders, products, tickets and shipments — generated, "
        "deterministic, in memory."
    ),
    "data.kb": "Knowledge base / RAG",
    "data.kb_body": (
        "A vector index with provenance verified at load, fused with BM25 "
        "lexical ranking."
    ),
    "obsv.tracing": "Tracing",
    "obsv.tracing_body": (
        "Every request has an id and a trace id; every step becomes a "
        "persisted event, in order."
    ),
    "obsv.metrics": "Metrics",
    "obsv.metrics_body": "Counters and latencies exposed in Prometheus format.",
    "obsv.logs": "Logs",
    "obsv.logs_body": "Structured JSON logs, correlated by request id.",
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
    "obs.median_latency": "Median latency · {mode}",
    "obs.median_latency_help": (
        "Median of {samples} requests recorded in {mode} mode. Requests from "
        "the other mode are not counted."
    ),
    "obs.median_latency_none": (
        "Fewer than {minimum} requests recorded in {mode} mode. Too small a "
        "sample for a median — and mixing the two modes would give a number "
        "that describes neither."
    ),
    "obs.real_vs_demo": (
        "Everything on this page is measured rather than estimated: it comes "
        "from the events the requests actually recorded. What is demonstration "
        "is where the requests came from — questions asked in this "
        "installation, about a fictional dataset. There is no uptime, SLA or "
        "user count here because this project has none of the three."
    ),
    "obs.recent_requests": "Recent requests",
    "obs.col_request": "Request",
    "obs.col_status": "Status",
    "obs.col_route": "Route",
    "obs.col_retries": "Retries",
    "obs.col_ms": "ms",
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
    "sidebar.status": "Platform running",
    "sidebar.group_env": "Environment",
    "sidebar.env_fictional": "{company} — fictional demonstration environment",
    "nav.demo_group": "Demonstration",
    "nav.page": "Page",
    "nav.platform_group": "Platform — the operational instruments",
    "nav.overview": "Overview",
    "flow.agents": "Agents",
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
    "orch.placeholder": "What is the status of Ana Ribeiro's order?",
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
    "progress.router": "Classifying your question...",
    "progress.researcher": "Querying the data...",
    "progress.executor": "Preparing the action...",
    "progress.validator": "Validating the response...",
    "progress.finishing": "Finalizing...",
    "progress.done": "Done",

    # Run states, table headings and data values, translated at display
    # time: the records keep their English keys and stored values.
    "state.not_reached": "NOT REACHED",
    "state.running": "RUNNING",
    "state.waiting": "WAITING",
    "state.success": "SUCCESS",
    "state.blocked": "BLOCKED",
    "state.failed": "FAILED",
    "state.out_of_scope": "NO ANSWER",
    "hint.populate": "Populate the database with `agent-platform demo`.",
    "tech.rules": "rules",
    "tech.col_agent": "Agent",
    "tech.col_state": "State",
    "tech.col_detail": "Detail",
    "tech.col_event": "Event",
    "tech.col_tool": "Tool",
    "tech.col_status": "Status",
    "col.ID": "ID",
    "col.Name": "Name",
    "col.Tier": "Tier",
    "col.City": "City",
    "col.State": "State",
    "col.Customer since": "Customer since",
    "col.Customer": "Customer",
    "col.Status": "Status",
    "col.Placed on": "Placed on",
    "col.Items": "Items",
    "col.Total (R$)": "Total (R$)",
    "col.SKU": "SKU",
    "col.Category": "Category",
    "col.Price (R$)": "Price (R$)",
    "col.Warranty (months)": "Warranty (months)",
    "col.Order": "Order",
    "col.Carrier": "Carrier",
    "col.Shipped on": "Shipped on",
    "col.Delivered on": "Delivered on",
    "col.Article": "Article",
    "col.Characters": "Characters",
    "col.Subject": "Subject",
    "col.Priority": "Priority",
    "col.Opened on": "Opened on",
    "col.Ticket": "Ticket",
    "col.Shipped": "Shipped",
    "col.Order status": "Order status",
    "val.processing": "processing",
    "val.shipped": "shipped",
    "val.delivered": "delivered",
    "val.cancelled": "cancelled",
    "val.returned": "returned",
    "val.open": "open",
    "val.escalated": "escalated",
    "val.resolved": "resolved",
    "val.high": "high",
    "val.normal": "normal",
    "val.low": "low",
    "val.gold": "Gold",
    "val.platinum": "Platinum",
    "val.standard": "Standard",
    "val.in_transit": "in transit",
    "val.returned_to_sender": "returned to sender",
    "val.accessories": "accessories",
    "val.audio": "audio",
    "val.displays": "displays",
    "val.furniture": "furniture",
    "val.peripherals": "peripherals",
    "val.storage": "storage",
    "step.detail_tool": "tool {tool}",
    "step.detail_risk": "{risk} risk",
    "step.detail_rule": "rule {rules}",
    "risk.low": "low",
    "risk.medium": "medium",
    "risk.high": "high",
    "risk.critical": "critical",
}
