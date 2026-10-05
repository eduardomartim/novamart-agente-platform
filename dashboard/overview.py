"""The landing page, rebuilt.

It lives in its own module for a reason that is structural rather than tidy:
the brief was to rebuild *this* page and leave the other five untouched, and
the surest way to guarantee that is for the new markup and the new stylesheet
to exist somewhere the other five never import. Every class here is namespaced
`ov-`, and nothing in `app.py`'s shared stylesheet is edited.

What the page must contain is not only a design question. Six tests pin the
landing page's substance, and they are right to:

* the first heading is `hero.headline`;
* the product is named, and the fictional company is named;
* the word "simula" appears, and the stub is described in plain language;
* the four declared capabilities appear -- "orquestra", "policy",
  "confirmação", "injection";
* what was *not* built is stated;
* one of the example questions is on the page.

So the rebuild is a rebuild of the layout and the visual language, not a
licence to drop the honest parts. The reference has no "what was not built"
band; this page keeps one, smaller.

`goto` arrives as an argument rather than as an import because `app.py` imports
this module, and importing back would be a cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import architecture_scene
import demo_content as demo
import streamlit as st
from i18n import t

from agent_platform.observability.metrics import collect_metrics
from agent_platform.platform import AgentPlatform

#: The technologies this project actually uses. Checked against the tree rather
#: than remembered: the visual reference also lists Grafana, and this repository
#: has no Grafana in it, so it is not here either.
TECHNOLOGIES: tuple[str, ...] = (
    "LangGraph",
    "Gemini",
    "FastAPI",
    "PostgreSQL",
    "Redis",
    "SQLite",
    "MCP",
    "Kubernetes",
    "Docker",
    "Prometheus",
    "Streamlit",
)

#: Five claims the hero makes, each backed by something on another page.
CHIPS: tuple[tuple[str, str], ...] = (
    ("◇", "hero.chip_autonomy"),
    ("⛨", "hero.chip_governance"),
    ("⚿", "hero.chip_security"),
    ("◎", "hero.chip_observability"),
    ("⊞", "hero.chip_scale"),
)


_CSS = """
<style>
  /* ============================================ the landing page only
     Namespaced `ov-`. Nothing here is reachable from the other five pages,
     which is the point: this rebuild must not move them.

     The handful of rules below that target shared selectors -- `h1`, `h2`, the
     block container, the toolbar -- are safe for the same reason: this style
     block is written by `render()`, so it exists in the document only while
     this page is the one being drawn. Navigate away and it goes with it. */

  /* Measured against the reference: its hero runs 145px, 14% of the viewport,
     and the architecture starts at 17% of the height. This page's ran to 340px
     and started at 60%, which is what made it read as a landing page with a
     diagram below rather than as a control plane. Every number here is spent
     buying that vertical space back. */
  /* The toolbar is empty in viewer mode and was still taking 60px. Streamlit's
     own rule is specific enough to need `!important` to move. It keeps a
     height rather than going to zero: the bar is absolutely positioned, so the
     height costs the page nothing, and at 0 the control that reopens a
     collapsed sidebar -- which its parent centres on that height -- sat at
     y=-14 with only its bottom half clickable. Clicks pass through the bar to
     the content it now overlays; its own buttons still take them. */
  [data-testid="stHeader"] {
    height: 2.6rem !important; min-height: 0 !important; background: transparent;
    pointer-events: none;
  }
  [data-testid="stHeader"] button { pointer-events: auto; }
  /* With the sidebar closed, the control that reopens it sits in the header's
     top-left corner -- 28px square at about (18, 7) -- and the hero's first line
     started at 14px, so the "»" was drawn over "NOVAMART" on every phone and on
     a desktop with the sidebar collapsed. The hero starts below the header
     instead, and only while that control is showing: with the sidebar open
     there is nothing in the corner and the page keeps its tighter top. */
  body:has(section[data-testid="stSidebar"][aria-expanded="false"]) .block-container {
    padding-top: 3.1rem !important;
  }
  /* And the header the control lives in is opaque while it is there. It is
     fixed, so on a transparent bar the page scrolled *under* the "»" and every
     heading that passed the top of a phone screen had the button drawn on it.
     Same colour as the page, so it reads as the top edge, not as a toolbar. */
  body:has(section[data-testid="stSidebar"][aria-expanded="false"])
    [data-testid="stHeader"] {
    background: var(--ap-ground);
  }
  /* 80px of horizontal padding each side is 160px the diagram does not get.
     At 1536 that was the difference between drawing at 0.77 and at 0.89. */
  .block-container {
    padding-top: 0.9rem !important;
    padding-left: 2.4rem !important; padding-right: 2.4rem !important;
  }
  /* One line, not two. */
  [data-testid="stMain"] h1 {
    font-size: 1.55rem !important; line-height: 1.25; margin: 0 0 0.3rem;
    letter-spacing: -0.025em; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis;
  }
  [data-testid="stMain"] .ap-eyebrow {
    margin-bottom: 0.25rem; font-size: 0.66rem;
  }
  /* Two lines, not three: 88ch is about 640px at this size. `margin: 0` was
     for the hero, but the rule reaches every lede on the page -- and the one
     under "O que não foi construído" ended up 5px inside the block below it
     and 8px inside the heading above. The paragraph gets its space back; the
     chips below the hero give the same amount up, so the hero does not grow. */
  [data-testid="stMain"] .ap-lede {
    font-size: 0.88rem; line-height: 1.5; max-width: 118ch;
    margin: 0 0 0.5rem;
  }
  /* The section rules below the architecture close up too: the reference fits
     the capability band into the same screen. */
  [data-testid="stMain"] h2 {
    margin-top: 1.7rem !important; padding-top: 0.75rem !important;
  }
  /* The gap between top-level elements. Was cut to 0.5rem to compact the
     hero, but this caused headings and ledes to visually collide with the
     element below. Back to 1rem prevents the collision. */
  [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    gap: 1rem;
  }

  /* Four invisible blocks sit above the hero -- two stylesheets and the two
     zero-height script frames that set `lang` and reset the scroll. They draw
     nothing, but each still claimed a 16px gap, which put the hero's first
     line 96px down the page instead of 14. Out of flow they cost nothing.
     `position: absolute` rather than `display: none` because the frames have
     to keep running; `scrolling="no"` is what `components.html` emits for them
     and what tells them apart from the architecture frame, which asks for
     "auto". */
  [data-testid="stElementContainer"]:has([data-testid="stMarkdownContainer"] > style),
  [data-testid="stElementContainer"]:has([data-testid="stIFrame"][scrolling="no"]) {
    position: absolute; height: 0; overflow: hidden;
  }

  /* The hero's own block, tight on purpose. The chips must be inside a *direct*
     element container: `:has(> * .ov-chips)` keeps it to the rows that hold
     these cards, so the hero does not grow. Gap increased for clear
     separation from the hero and the architecture scene below. */
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .ov-chips) {
    gap: 1rem;
  }

  /* Equal-height card rows. `height: 100%` on the card alone did nothing: it
     resolves against `stMarkdownContainer`, and every wrapper Streamlit puts
     between the column and the card has automatic height, so there was no
     definite height to be 100% of. The chain has to be unbroken, which is what
     these six selectors are. Measured before: 177 / 197 / 177 / 177.

     `:has(.ov-card)` keeps it to the rows that hold these cards, so the
     Experimente row -- a code block beside a button -- is left alone. */
  [data-testid="stHorizontalBlock"]:has(.ov-card) { align-items: stretch; }
  /* Flex, not `height: 100%`. A percentage height against a parent whose own
     height is automatic computes to `auto`, so the chain broke at the first
     wrapper and the card kept its content height -- measured 177 / 197 / 177 /
     177 either way. Making every wrapper a column flex container and letting
     the card grow into it needs no definite height anywhere. */
  [data-testid="stColumn"]:has(.ov-card),
  [data-testid="stColumn"]:has(.ov-card) > [data-testid="stVerticalBlock"],
  [data-testid="stColumn"]:has(.ov-card) [data-testid="stElementContainer"],
  [data-testid="stColumn"]:has(.ov-card) [data-testid="stMarkdown"],
  [data-testid="stColumn"]:has(.ov-card) [data-testid="stMarkdown"] > div,
  [data-testid="stColumn"]:has(.ov-card) [data-testid="stMarkdownContainer"] {
    display: flex; flex-direction: column; min-height: 0;
  }
  [data-testid="stColumn"]:has(.ov-card) > [data-testid="stVerticalBlock"],
  [data-testid="stColumn"]:has(.ov-card) [data-testid="stElementContainer"],
  [data-testid="stColumn"]:has(.ov-card) [data-testid="stMarkdown"],
  [data-testid="stColumn"]:has(.ov-card) [data-testid="stMarkdown"] > div,
  [data-testid="stColumn"]:has(.ov-card) [data-testid="stMarkdownContainer"],
  .ov-card { flex: 1 1 auto; min-height: 0; }

  /* Space above and below every card row, so a block never comes to rest
     against the one before it. The rows were 8px into each other. */
  [data-testid="stHorizontalBlock"]:has(.ov-card) {
    margin-top: 0.55rem; margin-bottom: 1.1rem;
  }

  /* The architecture block started 1px below the chips -- close enough to read
     as one element. 16px is the same step the card rows use, so the page keeps
     a single spacing rhythm instead of gaining a special case here. */
  [data-testid="stElementContainer"]:has([data-testid="stIFrame"]) {
    margin-top: 1rem;
  }

  .ov-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.35rem 0 0.55rem; }
  .ov-chip {
    display: inline-flex; align-items: center; gap: 0.4rem;
    border: 1px solid var(--ap-line); background: var(--ap-surface);
    border-radius: 6px; padding: 0.3rem 0.6rem;
    font-size: 0.75rem; color: var(--ap-muted);
    transition: border-color 0.16s ease, color 0.16s ease;
  }
  .ov-chip:hover { border-color: #2A3648; color: #CFE3FF; }
  .ov-chip i { font-style: normal; color: var(--ap-blue-2); font-size: 0.8rem; }

  /* The capability band: compact, four across, icon over title. */
  .ov-cap {
    border: 1px solid var(--ap-line); background: var(--ap-surface);
    border-radius: 10px; padding: 0.95rem 1rem;
    display: flex; flex-direction: column; gap: 0.55rem;
    transition: border-color 0.18s ease, background 0.18s ease;
  }
  .ov-cap:hover { border-color: #2A3648; background: #141C28; }
  .ov-cap .ov-ic {
    display: inline-flex; align-items: center; justify-content: center;
    width: 28px; height: 28px; border-radius: 8px; flex: 0 0 28px;
    border: 1px solid #2A3648; background: #141C28;
    color: var(--ap-blue-2); font-size: 0.82rem; font-style: normal;
  }
  .ov-cap h4 {
    margin: 0; font-size: 0.88rem; font-weight: 600; color: var(--ap-text);
    letter-spacing: -0.01em; line-height: 1.3;
  }
  .ov-cap p { margin: 0; font-size: 0.8rem; color: var(--ap-muted); line-height: 1.5; }

  /* A block of its own, like the capability cards above it. */
  .ov-foot {
    border: 1px solid var(--ap-line); background: var(--ap-surface);
    border-radius: 10px; padding: 1rem 1.1rem;
  }
  .ov-foot h4 {
    margin: 0 0 0.55rem !important; font-size: 0.82rem !important;
    font-weight: 650; color: var(--ap-text);
  }
  /* `margin-right` and not a flex `gap`: Streamlit's own heading rules win on
     `display`, so the flex row never formed and the glyph sat against the
     first letter. A margin does not depend on the layout mode. */
  .ov-foot h4 i {
    font-style: normal; color: var(--ap-blue-2); font-size: 0.85rem;
    margin-right: 0.45rem;
  }
  .ov-foot p { margin: 0; font-size: 0.78rem; color: var(--ap-muted); line-height: 1.55; }

  .ov-rownote {
    font-size: 0.74rem; color: var(--ap-dim); line-height: 1.5;
    margin: 1rem 0 0.2rem; max-width: 104ch;
  }

  /* `.ap-guarantees` is capped at 76ch in the shared stylesheet, which put this
     band at 604px beside sections using all 1149. Only this page renders that
     markup, so widening it here reaches nothing else -- and two columns use the
     width instead of leaving half the row empty. */
  .ap-guarantees {
    max-width: none; display: grid; grid-template-columns: repeat(2, 1fr);
    column-gap: 2.2rem; padding: 1rem 1.2rem 0.8rem;
  }
  .ap-guarantee:nth-child(2) { border-top: none; }
  @media (max-width: 900px) {
    .ap-guarantees { grid-template-columns: 1fr; }
    .ap-guarantee:nth-child(2) { border-top: 1px solid var(--ap-line); }
  }

  .ov-tech { display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .ov-tech span {
    font-size: 0.72rem; color: var(--ap-muted); border: 1px solid var(--ap-line);
    background: #0A0D13; border-radius: 4px; padding: 0.16rem 0.45rem;
  }

  /* Facts, one per row, value on the right. Numbers only where a number
     exists; there is no uptime here because there is no uptime. */
  .ov-state { display: flex; flex-direction: column; }
  .ov-state div {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 0.8rem; padding: 0.4rem 0; border-top: 1px solid var(--ap-line);
    font-size: 0.78rem; color: var(--ap-muted);
  }
  .ov-state div:first-child { border-top: none; }
  .ov-state b {
    color: var(--ap-text); font-weight: 600; font-variant-numeric: tabular-nums;
  }
  .ov-state .ov-live { color: var(--ap-green); }
  .ov-state .ov-stub { color: var(--ap-amber); }

  @media (max-width: 1200px) {
    /* `nowrap` is a desktop-only trick; below this the headline wraps rather
       than being cut off by the ellipsis. */
    [data-testid="stMain"] h1 { white-space: normal; font-size: 1.4rem !important; }
    .block-container { padding-left: 1.4rem !important; padding-right: 1.4rem !important; }
  }
  /* Tablets: two cards per row, not four. Four across a 768px screen with the
     sidebar open left each card 115px wide, and the titles broke mid-word.
     Below 640 Streamlit stacks the columns itself. */
  @media (min-width: 640px) and (max-width: 900px) {
    [data-testid="stHorizontalBlock"]:has(.ov-card) { flex-wrap: wrap; }
    [data-testid="stHorizontalBlock"]:has(.ov-card) > [data-testid="stColumn"] {
      flex: 1 1 calc(50% - 0.5rem) !important;
      min-width: calc(50% - 0.5rem) !important;
    }
  }
  @media (max-width: 900px) {
    .ov-chip { font-size: 0.78rem; }
    .ov-cap p { font-size: 0.84rem; }
    [data-testid="stMain"] .ap-lede { font-size: 0.92rem; }
  }
</style>
"""


def _panel_open(glyph: str, title: str) -> str:
    return f'<div class="ov-foot ov-card"><h4><i>{glyph}</i>{title}</h4>'


def render(platform: AgentPlatform, *, goto: Callable[..., None]) -> None:
    """Draw the landing page."""
    st.markdown(_CSS, unsafe_allow_html=True)

    # --- hero ---------------------------------------------------------------
    # In a container of its own so it can be tight without the rest of the page
    # being tight with it. The page-level gap is 1rem, which is what keeps a
    # heading from sitting on the paragraph under it; the hero's four lines want
    # a third of that, and a nested block is how you say so without negative
    # margins fighting the gap.
    with st.container():
        st.markdown(
            f'<p class="ap-eyebrow">{demo.PRODUCT_NAME} &middot; '
            f"{demo.PRODUCT_CATEGORY}</p>",
            unsafe_allow_html=True,
        )
        # `st.title` and not markup: the first `h1` is what
        # `test_landing_leads_with_identity_not_a_technical_warning` reads.
        st.title(t("hero.headline"), anchor=False)
        st.markdown(
            f'<p class="ap-lede">{t("hero.sub")}</p>', unsafe_allow_html=True
        )

        chips = "".join(
            f'<span class="ov-chip"><i>{glyph}</i>{t(key)}</span>'
            for glyph, key in CHIPS
        )
        st.markdown(f'<div class="ov-chips">{chips}</div>', unsafe_allow_html=True)

    # --- the architecture, working -----------------------------------------
    _scene()

    # --- what it demonstrates ----------------------------------------------
    # The slice is `OVERVIEW_CAPABILITIES`, which `test_dashboard_rendering`
    # pins: the same constant decides what Arquitetura shows, so a capability
    # lands on exactly one of the two pages.
    st.header(t("overview.demonstrates"), anchor=False)
    _capabilities(list(demo.DEMONSTRATED[:OVERVIEW_CAPABILITIES]))

    # --- environment, stack, state ------------------------------------------
    _footer(platform)

    # --- the one call to action ---------------------------------------------
    st.header(t("overview.try_it"), anchor=False)
    _try_it(goto)

    # --- and what is not there ----------------------------------------------
    st.header(t("overview.not_built"), anchor=False)
    st.markdown(
        f'<p class="ap-lede">{t("overview.not_built_lede")}</p>',
        unsafe_allow_html=True,
    )
    rows = "".join(
        f'<div class="ap-guarantee"><span class="ap-guarantee-key">{key}</span>'
        f"<span>{value}</span></div>"
        for key, value in demo.NOT_BUILT
    )
    st.markdown(f'<div class="ap-guarantees">{rows}</div>', unsafe_allow_html=True)


#: How many declared capabilities the landing page shows. The rest go to
#: Arquitetura. One constant, so nothing is shown twice and nothing is dropped.
OVERVIEW_CAPABILITIES = 4

#: A glyph per capability, in declaration order.
_CAP_GLYPHS = ("◈", "⛨", "⚠", "⊞", "⌕", "⚿")


def _capabilities(items: list[tuple[str, str]]) -> None:
    columns = st.columns(len(items))
    for index, (column, (title, body)) in enumerate(
        zip(columns, items, strict=False)
    ):
        column.markdown(
            f'<div class="ov-cap ov-card ap-fade"><i class="ov-ic">'
            f"{_CAP_GLYPHS[index % len(_CAP_GLYPHS)]}</i>"
            f"<h4>{title}</h4><p>{body}</p></div>",
            unsafe_allow_html=True,
        )


def _scene() -> None:
    """The staged architecture, handed everything already translated."""
    architecture_scene.render(
        scenarios=list(demo.ARCH_SCENARIOS),
        labels=dict(demo.ARCH_NODES),
        headings=dict(demo.ARCH_HEADINGS),
        chrome={
            "title": t("scene.title"),
            "live": t("scene.live"),
            "trace_title": t("scene.trace_title"),
            "demo_tag": t("scene.demo_tag"),
            "now_label": t("scene.now"),
            "note": t("scene.note"),
            "idle": t("scene.idle"),
            "verdict_idle": t("scene.verdict_idle"),
            "legend": [
                ("#2F81F7", t("scene.leg_running")),
                ("#3FB950", t("scene.leg_success")),
                ("#D29922", t("scene.leg_waiting")),
                ("#F85149", t("scene.leg_blocked")),
            ],
        },
    )


def _footer(platform: AgentPlatform) -> None:
    """Environment, stack and state -- and only what is actually measurable."""
    from agent_platform.guardrails.rules import RULES

    info = platform.provider_info

    # Three separate blocks. They were briefly one bordered band, which put a
    # second border around a row whose cards already overflowed it by 8px and
    # made the whole thing sit on the capabilities above.
    left, middle, right = st.columns([1.15, 1, 1])

    left.markdown(
        _panel_open("▤", t("ov.env_title"))
        + f'<p>{t("company.fictional_body").replace("**", "")}</p></div>',
        unsafe_allow_html=True,
    )

    stack = "".join(f"<span>{name}</span>" for name in TECHNOLOGIES)
    middle.markdown(
        _panel_open("◈", t("ov.tech_title"))
        + f'<div class="ov-tech">{stack}</div></div>',
        unsafe_allow_html=True,
    )

    facts: list[tuple[str, str]] = []
    badge = "ov-live" if info.live else "ov-stub"
    facts.append(
        (t("ov.state_mode"), f'<b class="{badge}">{"LIVE" if info.live else "DEMO / STUB"}</b>')
    )
    try:
        metrics: Any = collect_metrics(platform.repository)
    except Exception:
        metrics = None
    if metrics is not None and metrics.has_data:
        facts.append((t("ov.state_requests"), f"<b>{metrics.requests}</b>"))
    calls = _provider_calls(platform)
    if calls:
        facts.append((t("ov.state_provider"), f"<b>{calls}</b>"))
    facts.append((t("ov.state_rules"), f"<b>{len(RULES)}</b>"))

    body = "".join(f"<div><span>{key}</span>{value}</div>" for key, value in facts)
    right.markdown(
        _panel_open("◎", t("ov.state_title"))
        + f'<div class="ov-state">{body}</div>'
        # The plain-language statement about the stub lives here, where the mode
        # is stated -- `test_stub_mode_is_stated_in_plain_language` reads it.
        + f'<p style="margin-top:0.7rem">'
        f'{t("mode.live_caption") if info.live else t("mode.stub_caption")}</p>'
        "</div>",
        unsafe_allow_html=True,
    )
    # The note explains all three blocks, so it sits under the row rather than
    # inside one of them -- with its own space, not against the block above.
    st.markdown(
        f'<p class="ov-rownote">{t("ov.state_note")}</p>', unsafe_allow_html=True
    )


def _provider_calls(platform: AgentPlatform) -> int | None:
    """Physical provider calls recorded in the ledger, or None if unreadable.

    Evidence rather than a claim: the ledger is incremented once per real call
    and never by the stub. Absent when there is nothing to show, rather than a
    zero dressed up as a fact.
    """
    import sqlite3
    from pathlib import Path

    # Beside the request database, which is where the platform writes it. The
    # first attempt at this guessed a `provider_budget_path` setting that does
    # not exist, and would have silently reported nothing for ever.
    path = Path(platform.settings.database_path).parent / "provider_budget.db"
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = connection.execute("SELECT SUM(calls) FROM provider_budget").fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    total = rows[0] if rows else None
    return int(total) if total else None


def _try_it(goto: Callable[..., None]) -> None:
    """The one call to action, inside a container that actually contains it.

    It used to open a `<div>` in one `st.markdown` and close it in another.
    Streamlit closes the tag as it renders, so the "container" was an empty
    24px box with the content sitting on top of its border -- which is the
    line crossing the text in the screenshot. `st.container(border=True)` is
    a real container.
    """
    starter = demo.READ_ONLY_EXAMPLES[0][0]
    with st.container(border=True):
        st.markdown(
            f'<p class="ap-lede">{t("overview.try_lede")}</p>',
            unsafe_allow_html=True,
        )
        columns = st.columns([6, 2], vertical_alignment="center")
        columns[0].code(starter, language="text", wrap_lines=True)
        columns[1].button(
            t("overview.run_this"),
            key="cta_try",
            type="primary",
            width="stretch",
            on_click=goto,
            args=("orchestrator", starter),
        )
        st.caption(t("overview.ask_any_language"))
        st.caption(t("overview.demo_env"), unsafe_allow_html=True)
