"""NovaMart — AI Agent Orchestrator, the console a visitor actually sees.

Six pages, not thirteen. The earlier version had one page per subsystem, which
is how the people who built it think about it and not how a first-time reader
does: cost, reliability, drift and evaluation each had a page, so understanding
the product meant opening ten of them. They are still here, as sections inside
the page whose question they answer.

The rule this file is arranged around: **the complexity belongs in the system,
not on the first screen.** A visitor should be able to say what this is, what it
does and what they can try within a minute; everything deeper sits one
disclosure away.

Nothing here holds authority. Every answer comes from ``AgentPlatform.run()``,
the same call the CLI and the HTTP API make, and every number is read from the
repository or the simulated dataset rather than written down as a claim.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import altair as alt
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import demo_budget
import demo_content as demo
import execution_view

from agent_platform.config import Settings
from agent_platform.cost.pricing import pricing_notice
from agent_platform.guardrails.authorization import describe_matrix
from agent_platform.guardrails.rules import describe_rules
from agent_platform.observability.metrics import collect_metrics
from agent_platform.platform import AgentPlatform

st.set_page_config(
    page_title="NovaMart — AI Agent Orchestrator",
    page_icon=":material/hub:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# One constant stylesheet, injected once.
#
# It is a single literal that never varies between reruns, so React sees byte
# identical markup every time and has nothing to reconcile. That matters here:
# the previous version of this app produced `insertBefore` errors, and the way
# to keep the fix is to make sure the only raw markup in the file is static.
#
# Colour carries meaning and nothing else. Blue is the product, green is
# ALLOW/healthy, red is DENY/blocked, amber is a warning. Nothing is coloured
# for decoration, so a colour in this interface is always information.
_STYLE = """
<style>
  :root {
    --ap-line:    #232B36;
    --ap-muted:   #8B98A9;
    --ap-blue:    #2F81F7;
    --ap-cyan:    #38BDF8;
    --ap-green:   #3FB950;
    --ap-red:     #F85149;
    --ap-amber:   #D29922;
    --ap-surface: #111721;
  }

  /* Wider gutters and a calmer rhythm than the Streamlit default. */
  .block-container { padding-top: 2.6rem; max-width: 1180px; }
  h1, h2, h3 { letter-spacing: -0.02em; }
  h1 { font-weight: 650; }
  /* Streamlit sizes headings through a generated `.st-emotion-cache-<hash>`
     class whose hash changes between releases, so matching it would break on
     upgrade. `[data-testid="stMain"]` is a stable hook, and `!important` is
     what wins without depending on stylesheet order. */
  [data-testid="stMain"] h1 { font-size: clamp(1.7rem, 3.4vw, 2.75rem) !important; }
  h2 { font-weight: 600; margin-top: 2.2rem; }
  h3 { font-weight: 600; font-size: 1.02rem; }
  hr { border-color: var(--ap-line); }

  /* Sections fade in rather than snapping. 160ms is below the threshold where
     motion starts to feel like an effect. */
  @media (prefers-reduced-motion: no-preference) {
    .ap-fade { animation: ap-in 0.16s ease-out both; }
  }
  @keyframes ap-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; } }

  .ap-eyebrow {
    font-size: 0.72rem; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--ap-muted); margin-bottom: 0.35rem;
  }
  .ap-lede { color: var(--ap-muted); font-size: 1.02rem; max-width: 62ch; }

  .ap-card {
    border: 1px solid var(--ap-line); background: var(--ap-surface);
    border-radius: 6px; padding: 0.95rem 1.05rem; height: 100%;
    transition: border-color 0.16s ease;
  }
  .ap-card:hover { border-color: #31465F; }
  .ap-card h4 { margin: 0 0 0.3rem; font-size: 0.94rem; font-weight: 600; }
  .ap-card p  { margin: 0; color: var(--ap-muted); font-size: 0.86rem; line-height: 1.5; }

  /* The pipeline. Static markup, so it costs nothing to reconcile. */
  .ap-flow {
    display: flex; flex-wrap: wrap; gap: 0.4rem;
    align-items: center; margin: 0.4rem 0 0.2rem;
  }
  .ap-node {
    border: 1px solid var(--ap-line); border-radius: 5px; padding: 0.4rem 0.7rem;
    font-size: 0.82rem; background: var(--ap-surface); white-space: nowrap;
  }
  .ap-node.is-gate { border-color: var(--ap-blue); color: #CFE3FF; }
  .ap-arrow { color: var(--ap-muted); font-size: 0.9rem; }

  /* The mode strip: a badge and a sentence on one line, with a hairline
     under it. Loud enough to be read, quiet enough not to look like a fault. */
  .ap-mode {
    display: flex; gap: 0.7rem; align-items: baseline; flex-wrap: wrap;
    padding: 0.6rem 0 0.75rem; border-bottom: 1px solid var(--ap-line);
    color: var(--ap-muted); font-size: 0.88rem; margin-bottom: 0.3rem;
  }
  .ap-mode code {
    background: var(--ap-surface); border: 1px solid var(--ap-line);
    border-radius: 4px; padding: 0.05rem 0.3rem; font-size: 0.82rem;
  }

  /* The evidence strip. Rules between rows rather than a box around each,
     so it reads as one statement instead of four competing ones. */
  .ap-guarantees { margin: 0.2rem 0 0.4rem; max-width: 72ch; }
  .ap-guarantee {
    display: flex; gap: 1rem; align-items: baseline;
    padding: 0.55rem 0; border-top: 1px solid var(--ap-line);
    font-size: 0.88rem; color: var(--ap-muted);
  }
  .ap-guarantee:last-child { border-bottom: 1px solid var(--ap-line); }
  .ap-guarantee-key {
    flex: 0 0 8.5rem; color: #C9D5E4; font-weight: 600;
    font-size: 0.8rem; letter-spacing: 0.03em;
  }

  .ap-pill {
    display: inline-block; font-size: 0.7rem; font-weight: 600;
    letter-spacing: 0.06em; padding: 0.16rem 0.5rem; border-radius: 4px;
    border: 1px solid currentColor;
  }
  .ap-allow { color: var(--ap-green); }
  /* Not an outcome anyone should read as good or bad -- the platform simply
     had nothing to answer with. */
  .ap-neutral { color: var(--ap-muted); }
  .ap-deny  { color: var(--ap-red); }
  .ap-hold  { color: var(--ap-amber); }

  /* Tables should breathe rather than be squeezed. */
  [data-testid="stDataFrame"] { border: 1px solid var(--ap-line); border-radius: 6px; }
  [data-testid="stMetricValue"] { font-size: 1.55rem; font-weight: 600; }
  [data-testid="stMetricLabel"] { color: var(--ap-muted); }

  section[data-testid="stSidebar"] { border-right: 1px solid var(--ap-line); }
  section[data-testid="stSidebar"] .block-container { padding-top: 1.4rem; }

  @media (max-width: 900px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; }
    [data-testid="stMain"] h2 { font-size: 1.25rem !important; margin-top: 1.7rem; }
    .ap-lede { font-size: 0.95rem; }
    /* 0.72rem is 11px, which is small for uppercase on a phone. */
    .ap-eyebrow { font-size: 0.78rem; }
    .ap-card p { font-size: 0.9rem; }
    .ap-node { font-size: 0.78rem; padding: 0.32rem 0.55rem; }
    .ap-guarantee { flex-direction: column; gap: 0.15rem; }
    .ap-guarantee-key { flex: none; }

    /* Streamlit keeps the sidebar as a fixed 300px panel until its own much
       narrower breakpoint, which is 39% of a 768px tablet -- the navigation
       taking more room than the thing being navigated. Narrowing it here
       gives the content back the majority of the screen. */
    section[data-testid="stSidebar"] { width: 210px !important; min-width: 210px !important; }
    section[data-testid="stSidebar"] .block-container {
      padding-left: 0.9rem; padding-right: 0.9rem;
    }
  }
</style>
"""


@st.cache_resource
def get_platform() -> AgentPlatform:
    """One platform instance per Streamlit session."""
    return AgentPlatform(Settings.from_env())


DEFAULT_HINT = "Popule o banco com `agent-platform demo`."


def no_data(message: str, *, hint: str | None = DEFAULT_HINT) -> None:
    """Empty state carrying the command that actually populates *this* page."""
    st.info(message if hint is None else f"{message}\n\n{hint}")


def eyebrow(text: str) -> None:
    st.markdown(f'<p class="ap-eyebrow">{text}</p>', unsafe_allow_html=True)


def lede(text: str) -> None:
    """A muted sub-heading paragraph.

    This is raw HTML, so markdown is not parsed inside it: emphasis has to be
    written as `<strong>`, not as `**bold**`, which renders as four literal
    asterisks. Same for `eyebrow`.
    """
    st.markdown(f'<p class="ap-lede">{text}</p>', unsafe_allow_html=True)


def cards(items: list[tuple[str, str]], per_row: int = 3) -> None:
    """A row of equal cards. Fixed count per row, so the layout never reflows
    into a different number of elements between reruns."""
    for start in range(0, len(items), per_row):
        row = items[start : start + per_row]
        columns = st.columns(per_row)
        for column, (title, body) in zip(columns, row, strict=False):
            column.markdown(
                f'<div class="ap-card ap-fade"><h4>{title}</h4><p>{body}</p></div>',
                unsafe_allow_html=True,
            )


def guarantees(items: list[tuple[str, str]]) -> None:
    """A quiet evidence strip: a label, a fact, a hairline between them.

    Cards would have made these compete with the capabilities above; they are
    not the argument, they are what makes the argument checkable. One block of
    static markup, so it costs nothing to reconcile.
    """
    rows = "".join(
        f'<div class="ap-guarantee"><span class="ap-guarantee-key">{key}</span>'
        f"<span>{value}</span></div>"
        for key, value in items
    )
    st.markdown(f'<div class="ap-guarantees">{rows}</div>', unsafe_allow_html=True)


def flow(nodes: list[str], gate: str | None = None) -> None:
    """The request pipeline, as static markup."""
    parts = []
    for index, node in enumerate(nodes):
        css = "ap-node is-gate" if node == gate else "ap-node"
        parts.append(f'<span class="{css}">{node}</span>')
        if index < len(nodes) - 1:
            parts.append('<span class="ap-arrow">&rarr;</span>')
    st.markdown(f'<div class="ap-flow">{"".join(parts)}</div>', unsafe_allow_html=True)


def mode_banner(platform: AgentPlatform) -> None:
    """Which engine is answering, in language a non-engineer can read.

    A one-line strip rather than `st.info`, whose full-width blue box was the
    second-largest element on the landing page and read as an alert. The
    statement has to be unmissable -- nobody should mistake the stub for a
    model -- but unmissable is a job for placement, not for area.
    """
    info = platform.provider_info
    # No badge here. The sidebar carries one permanently, and showing the same
    # DEMO / STUB pill twice on the first screen spent the reader's attention
    # on a repetition. This keeps the sentence, which is the part that says
    # something the badge cannot.
    if info.live:
        text = (
            f"<strong>Modo live.</strong> As requisições são processadas pelo "
            f"provedor configurado (<code>{info.model}</code>)."
        )
    else:
        text = (
            "<strong>Simulação local determinística.</strong> Nenhum provedor "
            "de IA externo está sendo chamado, e nada aqui é atribuído a um."
        )
    st.markdown(f'<div class="ap-mode"><span>{text}</span></div>',
                unsafe_allow_html=True)


def provider_calls_recorded(platform: AgentPlatform) -> int | None:
    """Physical calls to a real provider, counted from the ledger itself.

    Not the same number as `metrics_summary()["llm_calls"]`, which counts every
    model call including the stub's. This reads the provider-call ledger, which
    is incremented once per *physical* attempt immediately before the SDK call
    and never for the stub -- so a non-zero value here is evidence that the live
    path was actually exercised, not a claim that it exists.

    `None` when there is no ledger yet, so the strip can stay silent rather than
    report a zero it has not earned.
    """
    path = Path(platform.settings.database_path).parent / "provider_budget.db"
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute("SELECT SUM(calls) FROM provider_budget").fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return int(row[0]) if row and row[0] else 0


def live_call_count(platform: AgentPlatform) -> int:
    """Real calls to a real provider, read rather than assumed.

    In stub mode this is zero by construction -- no provider is reachable at
    all. Reading it instead of hard-coding a zero means the number stays true
    if the dashboard is ever pointed at a live deployment.
    """
    try:
        summary = platform.repository.metrics_summary()
    except Exception:
        return 0
    if platform.provider_info.live:
        return int(summary.get("llm_calls", 0) or 0)
    return 0


def as_prose(text: str) -> str:
    """Prepare platform text for `st.markdown` without letting it reinterpret it.

    Streamlit reads `$...$` as inline LaTeX. Brazilian currency is written
    `R$`, so a sentence with two amounts in it -- which every order summary and
    every revenue total has -- pairs the dollar signs, swallows them, and
    renders the text between as maths. Measured on the real page:

        "Os 40 pedidos somam R 33.002,50. Descontando (R 6.374,90),
         o valor efetivo é R$ 26.627,60."

    Three amounts, two spellings, one sentence. Escaping at the boundary fixes
    every caller at once: the tools keep writing `R$`, and the CLI and the API
    are unaffected because they never went through a markdown renderer.
    """
    return text.replace("$", chr(92) + "$")


def goto(page: str, question: str | None = None) -> None:
    """Queue a navigation for the next rerun.

    Writes state and returns. It deliberately does not call ``st.rerun()``:
    a rerun raised from inside a tab or an expander tears down a subtree that
    React is still holding a reference into, which is what produced
    `insertBefore` errors in the previous version. Streamlit reruns on its own
    after a button press, and the radio picks the queued page up then.
    """
    st.session_state["nav"] = page
    if question is not None:
        st.session_state["queued_question"] = question


# ============================================================ 1. Visão geral


def page_overview(platform: AgentPlatform) -> None:
    """The thirty-second answer.

    Six things in one order: who this is, what it does in one sentence, the
    path a request takes, the four capabilities worth naming, one button that
    starts the demonstration, and the guarantees under it. The dataset counters
    that used to sit here now live on Empresa, where the dataset is -- a number
    a visitor cannot act on is not an opening argument.
    """
    eyebrow(demo.COMPANY_TAGLINE)
    st.title(f"{demo.COMPANY_NAME} — {demo.PRODUCT_NAME}")

    # The whole product in one sentence, in the order a reader needs it:
    # who acts, over what, and who is allowed to stop them.
    lede(
        "A NovaMart usa agentes de IA especializados para consultar clientes, "
        "pedidos e suporte. Um orquestrador decide quais agentes entram em "
        "ação, e um <strong>Policy Engine</strong> bloqueia operações de "
        "risco antes que "
        "qualquer ferramenta rode."
    )
    st.write("")
    mode_banner(platform)

    # --- the path a request takes ------------------------------------------
    st.header("Como funciona")
    flow(
        [
            "Usuário",
            "Router",
            "Agente especializado",
            "Policy Engine",
            "Ferramenta",
            "Validador",
            "Resposta",
        ],
        gate="Policy Engine",
    )
    lede(
        "O mesmo caminho toda vez. Nenhum agente executa uma ferramenta: eles "
        "propõem, e o Policy Engine decide."
    )

    # --- the one call to action --------------------------------------------
    st.header("Experimente")
    starter = demo.READ_ONLY_EXAMPLES[0][0]
    lede("Uma pergunta em linguagem natural, e a decisão que o sistema tomou.")
    columns = st.columns([6, 2])
    columns[0].code(starter, language="text", wrap_lines=True)
    columns[1].button(
        "Executar esta pergunta",
        key="cta_try",
        type="primary",
        width="stretch",
        on_click=goto,
        args=("Orquestrador", starter),
    )
    st.caption(
        "As perguntas estão em inglês porque é o idioma que o roteador e o "
        "conjunto de dados usam — é literalmente o texto que entra no sistema."
    )

    st.header("O que este projeto demonstra")
    cards(list(demo.DEMONSTRATED[:4]), per_row=2)
    st.caption(
        "RAG · MCP · Kubernetes · TLS · Prometheus · 727 testes de segurança — "
        "detalhados em **Arquitetura**."
    )

    # --- what holds it up ---------------------------------------------------
    st.header("O que sustenta isso")
    rows = [
        ("Política", "11 regras decidem antes de qualquer execução."),
        ("Autenticação", "A API recusa com 401 sem credencial."),
        ("Auditoria", "Cada requisição tem id, passos e decisão gravados."),
        (
            "Custo",
            f"Limite de {demo_budget.LIVE_CALL_BUDGET} chamadas por dia ao "
            "provedor, aplicado em modo live.",
        ),
    ]
    # The live path was invisible: the interface said the mode exists and gave
    # a reader no way to tell whether it had ever run. This is the physical
    # ledger, incremented once per real provider call and never by the stub --
    # evidence, not a claim. Absent when there is nothing to show, rather than
    # a zero dressed up as a fact.
    calls = provider_calls_recorded(platform)
    if calls:
        rows.append(
            (
                "Provedor real",
                f"{calls} chamadas ao Gemini já registradas no ledger físico "
                "desta instalação.",
            )
        )
    guarantees(rows)

    # --- and what is not there ----------------------------------------------
    st.header("O que não foi construído")
    lede(
        "As limitações ficam na mesma tela que as afirmações. Um projeto que "
        "só lista o que faz bem não é verificável."
    )
    # The same strip as the guarantees above, on purpose: what is built and
    # what is not are the same kind of claim and deserve the same weight. As
    # six paragraphs this section was the largest thing on the page.
    guarantees(list(demo.NOT_BUILT))

    st.write("")
    st.button(
        "Ver a arquitetura",
        key="cta_arch",
        on_click=goto,
        args=("Arquitetura",),
    )


# ================================================================ 2. Empresa


def _table(rows: list[dict[str, Any]], preview: int, key: str) -> None:
    """Show a readable slice, with the rest one disclosure away."""
    if not rows:
        no_data("Sem registros para exibir.", hint=None)
        return
    st.dataframe(rows[:preview], width="stretch", hide_index=True)
    if len(rows) > preview:
        with st.expander(f"Ver todos ({len(rows)})"):
            st.dataframe(rows, width="stretch", hide_index=True, key=f"all_{key}")


def page_company(platform: AgentPlatform) -> None:
    eyebrow("Contexto da demonstração")
    st.title(demo.COMPANY_NAME)
    lede(
        "Empresa fictícia de varejo e e-commerce. Ambiente simulado usado para "
        "demonstrar como agentes de IA podem consultar clientes, pedidos, "
        "produtos e tickets e executar ações protegidas por políticas."
    )

    totals = demo.company_totals()
    st.write("")
    metrics = st.columns(4)
    metrics[0].metric("Clientes", totals["Customers"])
    metrics[1].metric("Pedidos", totals["Orders"])
    metrics[2].metric("Produtos", totals["Products"])
    metrics[3].metric("Tickets abertos", totals["Open tickets"])

    st.header("Os dados")
    customers, orders, products, tickets = st.tabs(
        ["Clientes", "Pedidos", "Produtos", "Tickets"]
    )
    with customers:
        _table(demo.customers_table(), 6, "customers")
    with orders:
        _table(demo.orders_table(), 6, "orders")
    with products:
        _table(demo.products_table(), 6, "products")
    with tickets:
        _table(demo.tickets_table(), 6, "tickets")

    st.header("O que você pode testar")
    lede(
        "Os identificadores acima são reais dentro da simulação. Use-os nas "
        "perguntas — o sistema responde sobre eles."
    )
    for index, (question, hint) in enumerate(demo.READ_ONLY_EXAMPLES[:3]):
        columns = st.columns([6, 2])
        columns[0].code(question, language="text", wrap_lines=True)
        columns[0].caption(hint)
        columns[1].button(
            "Perguntar",
            key=f"company_ask_{index}",
            width="stretch",
            on_click=goto,
            args=("Orquestrador", question),
        )


# =========================================================== 3. Orquestrador


def _decision_pill(decision: str | None) -> str:
    text = (decision or "—").upper()
    css = {"ALLOW": "ap-allow", "DENY": "ap-deny", "REQUIRE_CONFIRMATION": "ap-hold"}.get(
        text, "ap-hold"
    )
    return f'<span class="ap-pill {css}">{text}</span>'


def _outcome_pill(state: execution_view.State) -> str:
    css = {
        execution_view.State.SUCCESS: "ap-allow",
        execution_view.State.BLOCKED: "ap-deny",
        execution_view.State.FAILED: "ap-deny",
        # Neutral on purpose. Nothing refused anything, so the badge must not
        # borrow the colour of a denial.
        execution_view.State.OUT_OF_SCOPE: "ap-neutral",
    }.get(state, "ap-hold")
    return f'<span class="ap-pill {css}">{state}</span>'


def _result_summary(last: dict[str, Any], view: execution_view.ExecutionView) -> None:
    """Four facts, in the order a reader asks for them."""
    st.markdown("**Pergunta**")
    st.code(last["question"], language="text", wrap_lines=True)

    columns = st.columns(4)
    with columns[0]:
        st.caption("DECISÃO")
        st.markdown(_decision_pill(view.policy_decision), unsafe_allow_html=True)
    with columns[1]:
        st.caption("AGENTE")
        st.markdown(f"`{last.get('route') or '—'}`")
    with columns[2]:
        st.caption("FERRAMENTA")
        tool = view.tools[0].name if view.tools else "—"
        st.markdown(f"`{tool}`")
    with columns[3]:
        st.caption("TEMPO")
        st.markdown(f"**{last['latency_ms'] / 1000:.2f}s**")


def _timeline(view: execution_view.ExecutionView) -> None:
    """The essential shape of the run. Six-ish lines, not twenty."""
    # Plain characters rather than `:material/...:` shortcodes: st.markdown
    # renders those literally, which put the words "radio_button_unchecked"
    # into the timeline. A glyph that renders everywhere beats one that renders
    # in some widgets and leaks its own name in others.
    marks = {
        execution_view.State.SUCCESS: ("●", "ap-allow"),
        execution_view.State.BLOCKED: ("■", "ap-deny"),
        execution_view.State.FAILED: ("■", "ap-deny"),
        execution_view.State.WAITING: ("◐", "ap-hold"),
        execution_view.State.OUT_OF_SCOPE: ("○", "ap-neutral"),
    }
    for step in view.steps:
        glyph, css = marks.get(step.state, ("○", ""))
        st.markdown(
            f'<div class="ap-fade"><span class="{css}">{glyph}</span> '
            f"{step.label}</div>",
            unsafe_allow_html=True,
        )
        if step.detail:
            # An ideographic space indents the detail under its step, which a
            # normal space would not survive: markdown collapses leading runs
            # of ASCII whitespace.
            st.caption(f"　{step.detail}")


def _technical_details(last: dict[str, Any], view: execution_view.ExecutionView) -> None:
    with st.expander("Ver detalhes técnicos"):
        st.caption("Correlation ID")
        st.code(last["request_id"], language="text", wrap_lines=True)

        st.caption("Decisão de política")
        st.markdown(
            f"{_decision_pill(view.policy_decision)} &nbsp; "
            f"regras: `{view.policy_rules or '—'}`",
            unsafe_allow_html=True,
        )

        if view.tools:
            st.caption("Ferramentas")
            st.dataframe(
                [
                    {
                        "Ferramenta": tool.name,
                        "Risco": tool.risk or "—",
                        "Proposta por": tool.proposed_by or "—",
                        "Decisão": tool.decision or "—",
                        "Execução": str(tool.execution),
                    }
                    for tool in view.tools
                ],
                width="stretch",
                hide_index=True,
            )

        st.caption("Agentes")
        st.dataframe(
            [
                {"Agente": a.name, "Estado": str(a.state), "Detalhe": a.detail or "—"}
                for a in view.agents
            ],
            width="stretch",
            hide_index=True,
        )

        st.caption("Sequência completa de eventos")
        st.dataframe(
            [
                {
                    "#": event.get("sequence"),
                    "Evento": event.get("event_type"),
                    "Agente": event.get("agent") or "—",
                    "Ferramenta": event.get("tool") or "—",
                    "Status": event.get("status"),
                    "ms": event.get("latency_ms"),
                }
                for event in last["events"]
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(
            f"Chamadas de modelo nesta requisição: "
            f"{execution_view.model_calls(last['events'])}"
        )


def _resolve(platform: AgentPlatform, approved: bool) -> None:
    """Approve or decline the suspended action, then let Streamlit rerun.

    A callback rather than a body-level `st.rerun()`: the callback runs before
    the rerun Streamlit already schedules for a button press, so the state is
    current by the time the page redraws and no subtree is torn down mid-render.
    """
    last = st.session_state.get("last_result")
    if not last or not last.get("pending"):
        return
    resumed = platform.confirm(
        last["request_id"], approved=approved, actor="dashboard-user", source="ui"
    )
    st.session_state["last_result"] = {
        **last,
        "status": resumed.status,
        "response": resumed.response,
        "pending": None,
        "events": platform.repository.events_for_request(last["request_id"]),
    }


def _confirmation_panel(platform: AgentPlatform, last: dict[str, Any]) -> None:
    """The human-in-the-loop control, not a description of one."""
    pending = last["pending"]
    st.warning(
        f"**Um humano precisa aprovar isto.** O executor propôs "
        f"`{pending['tool']}` (risco {pending['risk_level']}). Nada foi "
        "executado — a requisição está suspensa até você decidir."
    )
    st.caption(
        "O que você aprova fica preso a estes argumentos exatos: uma "
        "confirmação não pode ser reaproveitada para outra ação."
    )
    st.json(pending.get("arguments") or {})
    decide = st.columns([1, 1, 4])
    decide[0].button(
        "Aprovar", key="confirm_approve", type="primary", width="stretch",
        on_click=_resolve, args=(platform, True),
    )
    decide[1].button(
        "Recusar", key="confirm_decline", width="stretch",
        on_click=_resolve, args=(platform, False),
    )


def _run_question(platform: AgentPlatform, question: str) -> None:
    """Run one request and keep everything the result pages need."""
    result = platform.run(question)
    # `events_for_request` already returns plain dicts, which is exactly what
    # `execution_view.build` consumes. Nothing is reshaped here: a second
    # projection of the same rows is a second place for the two to disagree.
    events = platform.repository.events_for_request(result.request_id)
    st.session_state["last_result"] = {
        "question": question,
        "request_id": result.request_id,
        "status": result.status,
        "route": result.route,
        "response": result.response,
        "latency_ms": result.latency_ms,
        "events": events,
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


#: What stopped the request, said in the words of the control that stopped it.
#: Every entry is keyed on a real `blocked_by` value produced by
#: `execution_view._blocked_by`; the dashboard used to attribute all of them to
#: the policy engine, so a visitor who simply clicked too fast was told the
#: security policy had refused them.
_BLOCK_EXPLANATIONS: dict[str, tuple[str, str, str]] = {
    "policy engine": (
        "error",
        "Operação bloqueada pela política de segurança.",
        "A ação solicitada não tem autorização suficiente. Nenhuma ferramenta "
        "foi executada, e a recusa ficou registrada.",
    ),
    "rate limit": (
        "warning",
        "Limite de requisições atingido.",
        "A demonstração limita quantas solicitações um mesmo usuário faz por "
        "minuto. Aguarde alguns instantes e tente de novo — nada foi recusado "
        "por motivo de segurança.",
    ),
    "provider budget": (
        "warning",
        "Orçamento diário de chamadas ao provedor esgotado.",
        "Esta demonstração define o próprio teto de chamadas ao modelo e parou "
        "antes de gastar mais. O provedor está de pé; foi uma decisão de custo.",
    ),
    "circuit breaker": (
        "warning",
        "Circuito aberto após falhas seguidas do provedor.",
        "A plataforma parou de tentar depois de erros repetidos, para não "
        "insistir contra um serviço indisponível. Ela volta a tentar sozinha.",
    ),
    "resource limit": (
        "warning",
        "Teto de recursos da requisição atingido.",
        "A requisição excedeu um limite de passos, tempo ou tamanho definido "
        "por requisição. É um controle de custo e de latência, não de segurança.",
    ),
}


def _explain_block(last: dict[str, Any], view: execution_view.ExecutionView) -> None:
    """Say which control stopped the request, and stop guessing that it was policy."""
    level, title, detail = _BLOCK_EXPLANATIONS.get(
        view.blocked_by or "",
        (
            "error",
            "Operação interrompida por um controle da plataforma.",
            "A execução parou antes de concluir. O rastro abaixo mostra em que "
            "ponto e por quê.",
        ),
    )
    banner = st.error if level == "error" else st.warning
    banner(f"**{title}**\n\n{detail}")


def _next_step(view: execution_view.ExecutionView) -> None:
    """Say what to try next, instead of leaving the visitor to guess.

    The demonstration only lands if someone sees both halves: a request that
    is allowed and one that is refused. After the first, the second is one
    button away rather than something to go looking for.
    """
    if view.outcome is execution_view.State.BLOCKED:
        columns = st.columns([3, 5])
        columns[0].button(
            "Ver as políticas",
            key="next_policies",
            width="stretch",
            on_click=goto,
            args=("Segurança",),
        )
        columns[1].caption(
            "A regra que recusou esta operação está listada lá, junto com a "
            "matriz de quem pode chamar o quê."
        )
        return

    blocked = demo.SECURITY_EXAMPLES[0][0] if demo.SECURITY_EXAMPLES else None
    if blocked is None:
        return
    columns = st.columns([3, 5])
    columns[0].button(
        "Tentar algo proibido",
        key="next_blocked",
        width="stretch",
        on_click=goto,
        args=("Orquestrador", blocked),
    )
    columns[1].caption(
        "Essa foi permitida. O contraste é o ponto: agora peça uma exclusão e "
        "veja o Policy Engine recusar antes de qualquer ferramenta rodar."
    )


def _result_panel(platform: AgentPlatform, last: dict[str, Any]) -> None:
    """Everything about the last run, in the order a reader asks for it."""
    view = execution_view.build(last["events"], status=last["status"])

    st.markdown(
        f'<div class="ap-fade">{_outcome_pill(view.outcome)}</div>',
        unsafe_allow_html=True,
    )
    _result_summary(last, view)

    if last["pending"]:
        # The platform's own sentence here says the action needs approval, in
        # English, immediately above a Portuguese panel saying the same thing
        # and offering the buttons. Two messages, one fact. The panel wins: it
        # is the one that can be acted on.
        _confirmation_panel(platform, last)
    else:
        st.markdown("**Resposta**")
        st.markdown(as_prose(last["response"] or ""))

    if view.outcome is execution_view.State.OUT_OF_SCOPE:
        st.info(
            "**Pergunta fora do alcance desta demonstração.** Nenhuma "
            "ferramenta disponível responde a ela, e nada foi inventado.",
            icon=":material/help:",
        )
    elif view.outcome is execution_view.State.BLOCKED:
        _explain_block(last, view)

    if not last["pending"]:
        _next_step(view)

    # The timeline stays on the surface. It is the demonstration: the policy
    # decision sitting in sequence between the proposal and the tool call is
    # the thing this project exists to show, and a reader will not open an
    # expander to find an argument nobody made to them.
    st.subheader("Como a solicitação foi processada")
    _timeline(view)
    _technical_details(last, view)


def page_orchestrator(platform: AgentPlatform) -> None:
    eyebrow("Demonstração principal")
    st.title("Experimente o orquestrador")
    lede(
        "Faça uma pergunta em linguagem natural. O sistema decide quais agentes "
        "e ferramentas são necessários."
    )

    queued = st.session_state.pop("queued_question", None)
    if queued is not None:
        st.session_state["question_box"] = queued

    st.text_input(
        "Pergunta",
        key="question_box",
        placeholder="What is the status of order ORD-1001?",
        label_visibility="collapsed",
    )

    budget = demo_budget.budget_state(
        platform.repository, live=platform.provider_info.live
    )
    run_columns = st.columns([1, 5])
    run_clicked = run_columns[0].button(
        "Executar", type="primary", key="run_question", width="stretch",
        disabled=budget.exhausted,
    )
    run_columns[1].caption(budget.message)

    if run_clicked:
        question = (st.session_state.get("question_box") or "").strip()
        if question:
            # A spinner is the only honest signal here: the run is synchronous,
            # so without it the page simply stops responding for a second.
            with st.spinner("Executando…"):
                _run_question(platform, question)

    # The result comes before the examples. It used to come after them, which
    # meant clicking Executar scrolled nothing and the answer appeared below
    # twelve example questions -- the one thing the visitor came for was the
    # last thing on the page.
    last: dict[str, Any] | None = st.session_state.get("last_result")
    if last:
        st.divider()
        _result_panel(platform, last)
        st.divider()

    st.header("Exemplos")
    read_tab, action_tab, security_tab = st.tabs(
        ["Consultar", "Alterar algo", "Tentar quebrar"]
    )
    for tab, group, note, prefix in (
        (read_tab, demo.READ_ONLY_EXAMPLES, "Somente leitura. Apenas respondem.", "r"),
        (
            action_tab,
            demo.ACTION_EXAMPLES,
            "Propõem uma mudança, então param e esperam por você.",
            "a",
        ),
        (
            security_tab,
            demo.SECURITY_EXAMPLES,
            "São recusadas. Observe qual regra dispara.",
            "s",
        ),
    ):
        with tab:
            st.caption(note)
            for index, (question, hint) in enumerate(group):
                columns = st.columns([6, 2])
                columns[0].code(question, language="text", wrap_lines=True)
                columns[0].caption(hint)
                # A stable, content-independent key. The previous version used
                # `id(examples)`, a memory address -- it changes when the module
                # is reloaded, so every widget got a new identity in the same
                # position and React was asked to move nodes that no longer
                # existed. That was the `insertBefore` error.
                columns[1].button(
                    "Perguntar",
                    key=f"ex_{prefix}_{index}",
                    width="stretch",
                    on_click=goto,
                    args=("Orquestrador", question),
                )


# ============================================================== 4. Segurança


def page_security(platform: AgentPlatform) -> None:
    eyebrow("Controles aplicados em execução")
    st.title("Segurança")
    lede(
        "Segurança não está apenas documentada; ela é aplicada durante a "
        "execução. O motor de políticas é a única autoridade."
    )

    st.header("Quatro pilares")
    cards(
        [
            (
                "Autenticação",
                "A API exige credencial. Sem ela, 401 — e ela nunca vem do "
                "corpo da requisição.",
            ),
            (
                "Autorização",
                "Escopos independentes: quem pede uma ação de risco não é quem "
                "a aprova.",
            ),
            (
                "Policy Engine",
                "Onze regras decidem ALLOW, DENY ou CONFIRM antes de qualquer "
                "ferramenta rodar.",
            ),
            (
                "Auditoria",
                "Cada decisão é gravada com quem aprovou, autenticado — não "
                "auto-declarado.",
            ),
        ],
        per_row=4,
    )

    st.header("As três decisões")
    decisions = st.columns(3)
    decisions[0].markdown(
        f'{_decision_pill("ALLOW")}<p class="ap-lede">A ação roda. Risco baixo e '
        "dentro do que o agente pode fazer.</p>",
        unsafe_allow_html=True,
    )
    decisions[1].markdown(
        f'{_decision_pill("REQUIRE_CONFIRMATION")}<p class="ap-lede">A execução '
        "para e espera um humano. Nada acontece até alguém decidir.</p>",
        unsafe_allow_html=True,
    )
    decisions[2].markdown(
        f'{_decision_pill("DENY")}<p class="ap-lede">Recusada. Nenhuma '
        "ferramenta é chamada, e a recusa fica registrada.</p>",
        unsafe_allow_html=True,
    )

    st.header("Experimente, do simples ao hostil")
    lede(
        "Cinco cenários em ordem crescente de dificuldade. Cada um roda de "
        "verdade — o resultado é o que a plataforma faz, não uma descrição do "
        "que ela faria."
    )
    for index, scenario in enumerate(demo.SCENARIOS):
        columns = st.columns([6, 2])
        columns[0].markdown(
            f'<span class="ap-eyebrow">{scenario["level"]}</span>',
            unsafe_allow_html=True,
        )
        columns[0].markdown(f"**{scenario['title']}**")
        columns[0].code(scenario["ask"], language="text", wrap_lines=True)
        columns[0].caption(scenario["expect"])
        columns[1].button(
            "Executar",
            key=f"scenario_{index}",
            width="stretch",
            on_click=goto,
            args=("Orquestrador", scenario["ask"]),
        )
        # `watch` is what to look at in the trace once it has run, so it sits
        # one disclosure away rather than competing with the question itself.
        with columns[0].expander("O que observar"):
            st.caption(scenario["watch"])

    with st.expander("As regras de política, na íntegra"):
        st.dataframe(describe_rules(), width="stretch", hide_index=True)

    with st.expander("Quem pode chamar o quê"):
        st.caption(
            "A matriz de capacidades. Uma ferramenta ausente da linha de um "
            "agente não é alcançável por ele, sob nenhuma circunstância."
        )
        st.dataframe(describe_matrix(), width="stretch", hide_index=True)


# ============================================================ 5. Arquitetura


def page_architecture(platform: AgentPlatform) -> None:
    eyebrow("Como o sistema é montado")
    st.title("Arquitetura")
    lede(
        "Um caminho de requisição, uma autoridade, e a infraestrutura que "
        "sustenta os dois."
    )

    st.header("Caminho da requisição")
    flow(
        ["User", "API", "Router", "Agentes", "Policy Engine", "Tools / MCP", "Data / RAG"],
        gate="Policy Engine",
    )

    st.header("Plataforma")
    cards(
        [
            ("Kubernetes", "Deployment com probes, NetworkPolicy default-deny e HPA."),
            ("Redis", "Confirmações, checkpoints e limites compartilhados entre réplicas."),
            ("PostgreSQL", "Requisições, eventos e gasto — um ledger para todas as réplicas."),
            ("Observabilidade", "Logs JSON, métricas Prometheus e correlation IDs."),
            ("Network Policies", "Nada alcança a API além do que foi declarado."),
            ("TLS / Ingress", "Terminação na borda; o Service permanece ClusterIP."),
        ]
    )

    st.header("Os agentes")
    lede("Cinco papéis especializados. Nenhum deles executa uma ferramenta.")
    for role in demo.AGENT_ROLES:
        st.markdown(f"**{role['title']}** — {role['job']}")
        st.caption(role["holds"])
    st.info(
        "O **Policy Engine** não é um agente. Ele é a autoridade que decide o "
        "que qualquer agente pode fazer, e não pode ser persuadido por texto.",
        icon=":material/gavel:",
    )

    # The full declared list. The landing page shows the first four; the two
    # it leaves out -- RAG and MCP -- are named there on one line and get
    # their description here, which is where a reader who wants it will be.
    st.header("Competências demonstradas")
    lede("A lista completa, incluindo as duas que a tela inicial só cita.")
    cards(list(demo.DEMONSTRATED), per_row=2)


# ========================================================= 6. Observabilidade


def _latency_chart(rows: list[dict[str, Any]]) -> None:
    """Per-request latency, with the one number that explains its shape.

    The median is shown beside the chart because a single suspended request
    dominates the axis: a request that stops for human approval keeps counting
    wall-clock time while it waits, so its latency is mostly the time a person
    took to decide. That is worth stating rather than smoothing away -- the
    chart is evidence, and evidence that has been tidied is not evidence.
    """
    latencies = [float(row.get("latency_ms") or 0) for row in reversed(rows)]
    if not any(latencies):
        st.caption(
            "Nenhuma requisição registrou latência ainda, então não há série "
            "para desenhar."
        )
        return

    ordered = sorted(latencies)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )

    st.caption("Latência por requisição (ms), da mais antiga à mais recente")
    st.altair_chart(
        alt.Chart(alt.Data(values=[
            {"n": index, "ms": value} for index, value in enumerate(latencies)
        ]))
        .mark_line(point=True, color="#2F81F7")
        .encode(
            # Explicit domains: see the note above. Both are read from the
            # series being drawn.
            x=alt.X(
                "n:Q",
                title=None,
                scale=alt.Scale(domain=[0, max(1, len(latencies) - 1)]),
            ),
            y=alt.Y(
                "ms:Q",
                title="ms",
                scale=alt.Scale(domain=[0, max(latencies)]),
            ),
        )
        .properties(height=180),
        width="stretch",
    )
    st.caption(
        f"Mediana {median:.0f} ms · máximo {max(latencies):.0f} ms. Um pico "
        "isolado costuma ser uma requisição que parou para confirmação "
        "humana: o relógio continua correndo enquanto ela espera a decisão."
    )


def page_observability(platform: AgentPlatform) -> None:
    eyebrow("Plataforma")
    st.title("Observabilidade")
    lede(
        "Toda requisição tem um identificador, passos cronometrados e uma "
        "decisão registrada."
    )

    try:
        metrics = collect_metrics(platform.repository)
    except Exception:
        metrics = None

    if metrics is None or not metrics.has_data:
        no_data("Nenhuma requisição registrada ainda.")
        return

    columns = st.columns(4)
    columns[0].metric("Requisições", metrics.requests)
    columns[1].metric("Bloqueadas", metrics.blocked)
    columns[2].metric("Chamadas de ferramenta", metrics.tool_calls)
    columns[3].metric("Latência média", f"{metrics.avg_latency_ms:.0f} ms")

    st.header("Requisições recentes")
    rows = platform.repository.recent_requests(limit=12)
    if not rows:
        no_data("Sem requisições recentes.")
        return

    st.dataframe(
        [
            {
                "Request ID": row["request_id"],
                "Status": row["status"],
                "Rota": row.get("route") or "—",
                "ms": round(float(row.get("latency_ms") or 0), 1),
            }
            for row in rows
        ],
        width="stretch",
        hide_index=True,
    )

    _latency_chart(rows)

    with st.expander("Detalhes"):
        st.caption(
            "Contadores por tipo de evento, lidos do stream de eventos "
            "persistido."
        )
        summary = platform.repository.metrics_summary()
        st.dataframe(
            # `metrics_summary` mixes Decimal and int in one column, and
            # pyarrow refuses a column of mixed types. These are display
            # values, so coercing them to text loses nothing and keeps the
            # table from raising on a spend figure.
            [
                {"Métrica": key, "Valor": str(value)}
                for key, value in sorted(summary.items())
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(pricing_notice())


# ================================================================ navigation


#: Six pages. The order is the order a first-time reader should meet them.
DEMO_PAGES = {
    "Visão geral": page_overview,
    "Empresa": page_company,
    "Orquestrador": page_orchestrator,
    "Segurança": page_security,
    "Arquitetura": page_architecture,
}

PLATFORM_PAGES = {
    "Observabilidade": page_observability,
}

PAGES = {**DEMO_PAGES, **PLATFORM_PAGES}


def main() -> None:
    st.markdown(_STYLE, unsafe_allow_html=True)
    platform = get_platform()

    st.sidebar.title(demo.COMPANY_NAME)
    st.sidebar.caption(demo.PRODUCT_NAME)

    info = platform.provider_info
    # st.warning paints an olive box in the dark theme, which reads as a
    # problem. Running without a provider is the default and correct state of
    # this demo, not a fault -- so it gets a neutral badge. Amber stays
    # reserved for warnings and green for success, which is what makes either
    # of them mean anything.
    if info.live:
        st.sidebar.markdown(
            '<span class="ap-pill ap-allow">LIVE</span>', unsafe_allow_html=True
        )
        st.sidebar.code(info.model, language="text", wrap_lines=True)
        st.sidebar.caption("As requisições são atendidas por um provedor real.")
    else:
        st.sidebar.markdown(
            '<span class="ap-pill ap-hold">DEMO / STUB</span>',
            unsafe_allow_html=True,
        )
        st.sidebar.code(info.model, language="text", wrap_lines=True)
        st.sidebar.caption(
            "Nenhum modelo está sendo chamado. As respostas são "
            "determinísticas e geradas localmente."
        )

    options = list(PAGES)
    # The radio is keyed on the same session-state entry `goto()` writes, so
    # the widget *is* the navigation state. Passing `index=` instead made the
    # widget's identity depend on the current page: every navigation destroyed
    # and rebuilt it, which is the identity churn this rewrite exists to
    # remove, and it also made a queued page silently lose to the old index.
    if st.session_state.get("nav") not in options:
        st.session_state["nav"] = options[0]

    def label(page: str) -> str:
        return page if page in DEMO_PAGES else f"⚙ {page}"

    st.sidebar.caption("**DEMONSTRAÇÃO**")
    choice = st.sidebar.radio(
        "Página", options, key="nav", format_func=label, label_visibility="collapsed"
    )
    st.sidebar.caption("⚙ **PLATAFORMA** — os instrumentos operacionais.")
    st.sidebar.divider()
    st.sidebar.caption(
        f"{demo.COMPANY_NAME} é uma empresa simulada. Todas as ferramentas "
        "operam em memória; nenhum sistema externo é contatado."
    )

    PAGES[choice](platform)


main()
