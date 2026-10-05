"""The architecture, drawn and animated, as a demonstration of the shape.

This is the one piece of the dashboard that is deliberately *not* wired to the
platform, and the distinction matters enough to state at the top of the file.
The Orquestrador page runs real requests through the real graph and renders the
real trace. This scene runs a fixed set of scripted workflows to show what the
architecture is for: events arriving, specialised work being delegated, an
authority deciding before anything executes, a gateway executing, a validator
checking. Nothing here calls a provider, a tool, or an external system, and
nothing here reads the database.

Two consequences are load-bearing:

* The specialised agents drawn in the middle column -- Support, Refund,
  Finance, Marketing and the rest -- are **not** separate components in this
  codebase. The platform has five agents (router, researcher, executor,
  validator, answerer). The grid represents the capability of specialisation,
  which is a real property of the architecture, and the scene labels itself as
  a representation so that nobody reads it as an inventory.
* The systems on the right -- databases, messaging, ERP -- are integration
  *points*. None is connected. They are drawn in a distinct, dimmer style and
  the legend says what they are.

Why it is one HTML document in an iframe rather than Streamlit widgets:
Streamlit does not execute `<script>` inside `unsafe_allow_html`, and a hundred
seconds of animation driven from Python would be a hundred seconds of reruns.
Inside the iframe the whole loop is CSS and one chained timeout, the server is
untouched after first paint, and the DOM belongs to the iframe -- so React
never reconciles it and the `removeChild` class of bug cannot reach it.

**Height.** The frame is ``st.iframe(..., height="content")``: Streamlit
measures the document and sizes the frame to it. The scene used to be a
``components.html`` frame fixed at 612px, which no single number could make
right -- a phone needs about twice the height of a desktop -- and whose content
also changed height mid-animation, so on a phone it intermittently overflowed
into a scroll trap. Two rules keep the measured height honest:

* every piece of text that changes during the loop (the scenario title, what a
  node is saying, the verdict) is rendered *all at once*, stacked in one grid
  cell with only the current one visible, so the space reserved is the space
  the longest one needs and nothing moves when the scenario changes; and
* the trace list has a fixed height and shows its newest rows.

**Three layouts**, chosen by the frame's own width, because a 900-unit diagram
scaled into a phone is a diagram nobody can read:

* wide (>= 1280px): the diagram beside the trace;
* medium (820-1279px): the diagram at full width, the trace below it;
* narrow (< 820px): the same architecture as a vertical flow -- events, then
  agents, then the control plane, then integrations -- in HTML at readable
  sizes, with the connections drawn as animated links between the layers.

The two drawings share one script and one set of states: a node is lit in both,
so whichever is on screen is showing the same moment of the same scenario.
"""

from __future__ import annotations

import json
from itertools import pairwise
from typing import Any, Final

import streamlit as st

# ============================================================ scene geometry
#
# One design space, scaled by the SVG viewBox. Nothing computes a layout at
# runtime: the diagram is the same shape at every width where it is shown, and
# the browser does the scaling for free.

_W: Final[int] = 900
_H: Final[int] = 530

#: Node boxes, in design-space units. `kind` drives styling.
NODES: Final[dict[str, dict[str, Any]]] = {
    # --- events, left rail --------------------------------------------------
    "ev_ticket": {"x": 8, "y": 36, "w": 126, "h": 52, "kind": "event"},
    "ev_order": {"x": 8, "y": 94, "w": 126, "h": 52, "kind": "event"},
    "ev_stock": {"x": 8, "y": 152, "w": 126, "h": 52, "kind": "event"},
    "ev_refund": {"x": 8, "y": 210, "w": 126, "h": 52, "kind": "event"},
    "ev_campaign": {"x": 8, "y": 268, "w": 126, "h": 52, "kind": "event"},
    # --- the specialised grid -----------------------------------------------
    "ag_support": {"x": 144, "y": 36, "w": 96, "h": 80, "kind": "agent"},
    "ag_order": {"x": 240, "y": 36, "w": 96, "h": 80, "kind": "agent"},
    "ag_inventory": {"x": 336, "y": 36, "w": 96, "h": 80, "kind": "agent"},
    "ag_refund": {"x": 144, "y": 124, "w": 96, "h": 80, "kind": "agent"},
    "ag_finance": {"x": 240, "y": 124, "w": 96, "h": 80, "kind": "agent"},
    "ag_comm": {"x": 336, "y": 124, "w": 96, "h": 80, "kind": "agent"},
    "ag_marketing": {"x": 144, "y": 212, "w": 96, "h": 80, "kind": "agent"},
    "ag_content": {"x": 240, "y": 212, "w": 96, "h": 80, "kind": "agent"},
    "ag_analytics": {"x": 336, "y": 212, "w": 96, "h": 80, "kind": "agent"},
    "ag_research": {"x": 144, "y": 300, "w": 96, "h": 80, "kind": "agent"},
    # --- the control plane, on the width the gutter gave back ---------------
    "policy": {"x": 468, "y": 36, "w": 176, "h": 60, "kind": "policy"},
    "human": {"x": 468, "y": 104, "w": 176, "h": 56, "kind": "gate"},
    "orchestrator": {"x": 458, "y": 180, "w": 196, "h": 196, "kind": "core"},
    "gateway": {"x": 468, "y": 396, "w": 176, "h": 58, "kind": "gate"},
    "validator": {"x": 468, "y": 462, "w": 176, "h": 58, "kind": "check"},
    # --- integration points (none connected) --------------------------------
    "t_db": {"x": 682, "y": 36, "w": 210, "h": 52, "kind": "tool"},
    "t_mail": {"x": 682, "y": 96, "w": 210, "h": 52, "kind": "tool"},
    "t_chat": {"x": 682, "y": 156, "w": 210, "h": 52, "kind": "tool"},
    "t_erp": {"x": 682, "y": 216, "w": 210, "h": 52, "kind": "tool"},
    "t_api": {"x": 682, "y": 276, "w": 210, "h": 52, "kind": "tool"},
    "t_social": {"x": 682, "y": 336, "w": 210, "h": 52, "kind": "tool"},
    "t_store": {"x": 682, "y": 396, "w": 210, "h": 52, "kind": "tool"},
}

#: Column headings: key, x, y, and the width they are allowed to occupy.
#:
#: The width is the point. As `<text>` these ran on for as long as the string
#: was -- "Orquestração e governança" is 188px at this size and the integration
#: column starts 162px away, so one heading printed straight over the other.
#: SVG text does not wrap and does not respect a box, so the headings are drawn
#: as `foreignObject` like every other label here and wrap inside their column.
HEADINGS: Final[tuple[tuple[str, int, int, int], ...]] = (
    ("events", 8, 8, 126),
    ("agents", 144, 8, 288),
    ("core", 458, 6, 196),
    ("tools", 682, 8, 210),
)

#: The narrow layout's layers, top to bottom, and the node kinds in each. The
#: same four columns as the diagram, turned on their side.
STAGES: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("events", frozenset({"event"})),
    ("agents", frozenset({"agent"})),
    ("core", frozenset({"core", "policy", "gate", "check"})),
    ("tools", frozenset({"tool"})),
)

#: The control plane in the narrow layout: the orchestrator across the top,
#: then the four authorities that surround it in the diagram.
_CORE_ORDER: Final[tuple[str, ...]] = (
    "orchestrator", "policy", "human", "gateway", "validator",
)

#: A glyph per node. Plain characters: an icon font is one more request, and
#: this project does not make requests it did not declare.
GLYPHS: Final[dict[str, str]] = {
    "ev_ticket": "✉",
    "ev_order": "▤",
    "ev_stock": "▣",
    "ev_refund": "↺",
    "ev_campaign": "◷",
    "ag_support": "☏",
    "ag_order": "▤",
    "ag_inventory": "▣",
    "ag_refund": "↺",
    "ag_finance": "$",
    "ag_comm": "✉",
    "ag_marketing": "◈",
    "ag_content": "✎",
    "ag_analytics": "◫",
    "ag_research": "⌕",
    "policy": "⛨",
    "human": "☺",
    "gateway": "⚿",
    "validator": "✓",
    "t_db": "⌸",
    "t_mail": "✉",
    "t_chat": "◌",
    "t_erp": "▦",
    "t_api": "◈",
    "t_social": "◍",
    "t_store": "⬚",
}

#: Where a bubble appears, and it is no longer a column of its own.
#:
#: A reserved gutter guaranteed that a bubble never covered a node, and it cost
#: 16% of the diagram's width to guarantee it -- for something on screen in two
#: moments of each scenario. These two rectangles are areas that are empty
#: anyway: under the agent grid, which ends at 380, and under the integration
#: column, which ends at 448. The guarantee is unchanged and the width is back.
_ASK_SLOT: Final[tuple[float, float, float, float]] = (150.0, 400.0, 276.0, 76.0)
_REPLY_SLOT: Final[tuple[float, float, float, float]] = (682.0, 460.0, 210.0, 66.0)

#: Which nodes speak from the reply slot: the control plane answering.
_CORE_KINDS: Final[frozenset[str]] = frozenset({"core", "policy", "gate", "check"})


def _anchor(node: str, side: str) -> tuple[float, float]:
    box = NODES[node]
    if side == "right":
        return box["x"] + box["w"], box["y"] + box["h"] / 2
    if side == "left":
        return box["x"], box["y"] + box["h"] / 2
    if side == "top":
        return box["x"] + box["w"] / 2, box["y"]
    return box["x"] + box["w"] / 2, box["y"] + box["h"]


def _edge_path(a: str, b: str) -> str:
    """A curve between two boxes, leaving and entering on sensible sides.

    Mostly left-to-right, so the default is right edge to left edge with a
    horizontal control point. Vertical neighbours in the same column -- policy
    above the orchestrator, validator below the gateway -- are joined
    top-to-bottom instead, because a horizontal curve between them would loop
    out into the diagram and read as a different connection entirely.
    """
    box_a, box_b = NODES[a], NODES[b]
    same_column = abs(
        (box_a["x"] + box_a["w"] / 2) - (box_b["x"] + box_b["w"] / 2)
    ) < 90

    if same_column:
        going_down = box_b["y"] > box_a["y"]
        x1, y1 = _anchor(a, "bottom" if going_down else "top")
        x2, y2 = _anchor(b, "top" if going_down else "bottom")
        bend = abs(y2 - y1) * 0.45
        c1 = y1 + bend if going_down else y1 - bend
        c2 = y2 - bend if going_down else y2 + bend
        return f"M{x1:.1f},{y1:.1f} C{x1:.1f},{c1:.1f} {x2:.1f},{c2:.1f} {x2:.1f},{y2:.1f}"

    forward = (box_b["x"] + box_b["w"] / 2) > (box_a["x"] + box_a["w"] / 2)
    x1, y1 = _anchor(a, "right" if forward else "left")
    x2, y2 = _anchor(b, "left" if forward else "right")
    bend = max(46.0, abs(x2 - x1) * 0.42)
    c1 = x1 + bend if forward else x1 - bend
    c2 = x2 - bend if forward else x2 + bend
    return f"M{x1:.1f},{y1:.1f} C{c1:.1f},{y1:.1f} {c2:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"


def _bubble_box(node: str) -> tuple[float, float, float, float, str]:
    """Which slot this node speaks from, as x, y, width, height and direction."""
    if NODES[node]["kind"] in _CORE_KINDS:
        x, y, w, h = _REPLY_SLOT
        return x, y, w, h, "right"
    x, y, w, h = _ASK_SLOT
    return x, y, w, h, "left"


def _edges(scenarios: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Every hop any scenario actually makes, de-duplicated, order preserved.

    Only these are drawn. A complete graph of every possible connection would
    be a hairball, and would also claim connections no workflow makes.
    """
    seen: dict[tuple[str, str], None] = {}
    for scenario in scenarios:
        hops = [step["node"] for step in scenario["steps"]]
        trigger = scenario.get("trigger")
        if trigger:
            hops.insert(0, trigger)
        for first, second in pairwise(hops):
            if first != second:
                seen.setdefault((first, second), None)
    return list(seen)


def stage_of(node: str) -> int:
    """Which layer of the narrow layout a node sits in, top to bottom."""
    kind = NODES[node]["kind"]
    for index, (_key, kinds) in enumerate(STAGES):
        if kind in kinds:
            return index
    raise KeyError(node)


# ================================================================== the markup


def _svg(labels: dict[str, dict[str, str]], headings: dict[str, str],
         scenarios: list[dict[str, Any]]) -> str:
    parts: list[str] = [
        f'<svg class="scene" viewBox="0 0 {_W} {_H}" '
        'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="{headings.get("aria", "")}">',
        # The orchestrator's halo. A filter rather than a stack of rings: one
        # blurred circle behind the node, which is what makes it read as the
        # lit centre of the diagram instead of another box.
        "<defs>"
        '<radialGradient id="halo" cx="50%" cy="50%" r="50%">'
        '<stop offset="0%" stop-color="#2F81F7" stop-opacity="0.34"/>'
        '<stop offset="55%" stop-color="#2F81F7" stop-opacity="0.10"/>'
        '<stop offset="100%" stop-color="#2F81F7" stop-opacity="0"/>'
        "</radialGradient>"
        '<radialGradient id="halo-violet" cx="50%" cy="50%" r="50%">'
        '<stop offset="0%" stop-color="#A371F7" stop-opacity="0.26"/>'
        '<stop offset="100%" stop-color="#A371F7" stop-opacity="0"/>'
        "</radialGradient>"
        "</defs>",
    ]

    core = NODES["orchestrator"]
    parts.append(
        f'<circle class="halo" cx="{core["x"] + core["w"] / 2}" '
        f'cy="{core["y"] + core["h"] / 2}" r="168" fill="url(#halo)"/>'
    )
    gate = NODES["policy"]
    parts.append(
        f'<circle cx="{gate["x"] + gate["w"] / 2}" cy="{gate["y"] + gate["h"] / 2}" '
        'r="100" fill="url(#halo-violet)"/>'
    )

    for key, x, y, width in HEADINGS:
        parts.append(
            f'<foreignObject x="{x}" y="{y}" width="{width}" height="30">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="head">'
            f'{headings.get(key, "")}</div>'
            "</foreignObject>"
        )

    parts.append('<g class="edges">')
    for first, second in _edges(scenarios):
        path = _edge_path(first, second)
        parts.append(f'<path class="edge" d="{path}"/>')
        parts.append(f'<path class="pulse" id="e-{first}-{second}" d="{path}"/>')
    parts.append("</g>")

    for node, box in NODES.items():
        label = labels.get(node, {})
        glyph = GLYPHS.get(node, "")
        kind = box["kind"]
        if kind == "core":
            inner = (
                f'<i class="gl">◉</i><b>{label.get("name", node)}</b>'
                f'<em>{label.get("role", "")}</em>'
            )
        else:
            inner = (
                f'<i class="gl">{glyph}</i>'
                f'<span class="tx"><b>{label.get("name", node)}</b>'
                f'<em>{label.get("role", "")}</em></span>'
            )
        parts.append(
            f'<foreignObject x="{box["x"]}" y="{box["y"]}" '
            f'width="{box["w"]}" height="{box["h"]}">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="node k-{kind}" '
            f'data-node="{node}" id="n-{node}">{inner}</div>'
            "</foreignObject>"
        )

    for node in NODES:
        x, y, width, height, tail = _bubble_box(node)
        parts.append(
            f'<foreignObject class="bwrap" x="{x:.0f}" y="{y:.0f}" '
            f'width="{width:.0f}" height="{height:.0f}">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="bubble t-{tail}" '
            f'id="b-{node}"><span></span></div>'
            "</foreignObject>"
        )

    parts.append("</svg>")
    return "".join(parts)


def _flow(labels: dict[str, dict[str, str]], headings: dict[str, str]) -> str:
    """The narrow layout: the same nodes as four stacked layers.

    Every node keeps its identity -- `data-node` is the key the script lights --
    so the flow and the diagram are two drawings of one state, never two
    states. The connections become links *between layers*: on a phone a curve
    from one box to another across a 900-unit canvas is unreadable, while "the
    work went down from the agents to the control plane" is exactly what a hop
    between those layers means.
    """
    def card(node: str) -> str:
        label = labels.get(node, {})
        kind = NODES[node]["kind"]
        glyph = "◉" if kind == "core" else GLYPHS.get(node, "")
        return (
            f'<div class="node k-{kind}" data-node="{node}" id="m-{node}">'
            f'<i class="gl">{glyph}</i>'
            f'<span class="tx"><b>{label.get("name", node)}</b>'
            f'<em>{label.get("role", "")}</em></span></div>'
        )

    parts: list[str] = [
        f'<div class="flow" role="group" aria-label="{headings.get("aria", "")}">'
    ]
    for index, (key, kinds) in enumerate(STAGES):
        if index:
            parts.append(
                f'<div class="link" id="ml-{index - 1}" aria-hidden="true">'
                "<i></i></div>"
            )
        if key == "core":
            nodes = list(_CORE_ORDER)
        else:
            nodes = [n for n, box in NODES.items() if box["kind"] in kinds]
        parts.append(
            f'<section class="stage st-{key}">'
            f'<h5 class="head">{headings.get(key, "")}</h5>'
            f'<div class="grid g-{key}">{"".join(card(n) for n in nodes)}</div>'
            "</section>"
        )
    parts.append("</div>")
    return "".join(parts)


_CSS: Final[str] = """
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0; background: transparent;
  font-family: "Source Sans Pro", -apple-system, BlinkMacSystemFont, "Segoe UI",
               Roboto, sans-serif;
  color: #E6EDF3;
}
/* The frame is sized to this document by Streamlit, so the document has
   nothing to scroll and a swipe or a wheel over it moves the page. No
   `overflow: hidden` here: measured in Chrome, it stopped the wheel from
   chaining to the page at all, which is the scroll trap this replaced. */
:root {
  /* Mirrors the app's tokens. Literals because an iframe cannot inherit the
     parent document's custom properties. */
  --line:    #1E2733;
  --line-2:  #2A3648;
  --muted:   #8B98A9;
  --dim:     #7A8696;
  --blue:    #2F81F7;
  --blue-2:  #5AA0FF;
  --green:   #3FB950;
  --red:     #F85149;
  --amber:   #D29922;
  --violet:  #A371F7;
  --surface: #0E141D;
  --raised:  #141C28;
  --panel:   #0C121A;
}

/* ===================================================== the two panels
   Drawn rather than structural: the trace is a caption on the frame the
   diagram is showing, so they must share a clock, and two iframes cannot. */
.wrap { display: flex; gap: 14px; align-items: stretch; }
.card {
  border: 1px solid var(--line); border-radius: 10px; background: var(--panel);
  padding: 14px 16px; display: flex; flex-direction: column; min-width: 0;
}
.left  { flex: 1 1 73%; }
.right { flex: 0 0 26%; min-width: 244px; }

.bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-bottom: 10px; margin-bottom: 6px;
}
.bar h3 { margin: 0; font-size: 0.96rem; font-weight: 650; letter-spacing: -0.02em; }
.live {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 0.7rem; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--green);
}
.live i {
  width: 6px; height: 6px; border-radius: 50%; background: var(--green);
  box-shadow: 0 0 0 3px rgba(63,185,80,0.16);
}
@media (prefers-reduced-motion: no-preference) {
  .live i { animation: beat 2.4s ease-in-out infinite; }
}
@keyframes beat { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
.tag {
  font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase;
  border: 1px solid var(--line-2); border-radius: 4px; padding: 2px 6px;
  color: var(--dim); white-space: nowrap;
}
.now { margin-left: auto; font-size: 0.72rem; color: var(--dim); }
.now b { color: #CFE3FF; font-weight: 600; }

/* ============================ text that changes, without moving anything
   Every alternative is in the document at once, stacked in one grid cell, and
   only the current one is visible. The cell is therefore as large as the
   largest alternative at this width -- measured by the browser, not guessed --
   and swapping one for another changes nothing around it. That is what lets
   Streamlit size the frame once and have it stay right for the whole loop. */
.stack { display: inline-grid; vertical-align: bottom; }
.stack > * { grid-area: 1 / 1; visibility: hidden; }
.stack > .on { visibility: visible; }

/* ========================================================= the diagram */
.scene { width: 100%; height: auto; display: block; }
.head {
  color: var(--dim); font-size: 10.5px; letter-spacing: 0.11em;
  text-transform: uppercase; font-weight: 700; line-height: 1.35; margin: 0;
}

.node {
  width: 100%; height: 100%; border: 1px solid var(--line-2); border-radius: 9px;
  background: var(--surface); padding: 7px 9px;
  display: flex; align-items: center; gap: 8px; position: relative;
  transition: border-color .3s ease, background .3s ease, box-shadow .3s ease;
}
.node b {
  display: block; font-size: 13px; font-weight: 600; line-height: 1.15;
  color: #E6EDF3;
}
/* Truncation was hiding half the vocabulary -- "Support A...", "Inventory...".
   The names are the diagram's labels, so they wrap rather than clip. */
.k-agent b, .k-event b, .k-tool b { white-space: normal; }
.k-agent b { font-size: 11.8px; }
.k-tool b { font-size: 12px; }
.node em {
  display: block; font-size: 10.5px; font-style: normal; color: var(--dim);
  line-height: 1.28; white-space: normal;
}
/* An agent card carries a wrapped name; a second line of role under it would
   overflow. The role lives in the trace panel, where there is room for it. */
.scene .k-agent em, .scene .k-event em { display: none; }
.node .tx { min-width: 0; }
.node .gl {
  display: flex; align-items: center; justify-content: center; flex: 0 0 26px;
  width: 26px; height: 26px; border-radius: 7px; font-style: normal;
  font-size: 12px; color: var(--blue-2);
  border: 1px solid var(--line-2); background: var(--raised);
}

.k-event .gl { color: var(--muted); }
.scene .k-agent { flex-direction: column; align-items: flex-start; gap: 5px; padding: 8px 9px; }
.scene .k-agent .tx { width: 100%; }

.scene .k-core {
  border-radius: 50%; flex-direction: column; align-items: center; gap: 3px;
  justify-content: center; text-align: center;
}
.k-core {
  border-color: #3B72B8; border-width: 1.5px;
  background: radial-gradient(circle at 50% 34%, #1B2E48, #0C1320 74%);
  box-shadow: 0 0 0 6px rgba(47,129,247,.07),
              0 0 0 1px rgba(90,160,255,.30) inset,
              0 0 58px -4px rgba(47,129,247,.55);
}
.scene .k-core .gl {
  border: none; background: none; font-size: 26px; width: auto; height: auto;
  flex: none; filter: drop-shadow(0 0 10px rgba(90,160,255,0.7));
}
.k-core .gl { color: var(--blue-2); }
.k-core b { font-size: 16px; letter-spacing: -0.01em; }
.k-core em { font-size: 11px; color: var(--blue-2); opacity: .9; }

.k-policy {
  border-color: #4A3570;
  background: linear-gradient(180deg, #191331, var(--surface) 78%);
  box-shadow: 0 0 30px -10px rgba(163,113,247,.55);
}
.k-policy .gl { color: var(--violet); border-color: #4A3570; }
.k-check .gl { color: var(--green); }
.k-tool  { background: transparent; border-style: dashed; opacity: .8; }
.k-tool b { font-weight: 500; color: #C4CFDC; }

/* States. The names and the colours are the ones `execution_view.State`
   already uses on the Orquestrador page, so the demo and the real trace do
   not describe the same thing two different ways. */
.node.is-running {
  border-color: var(--blue); background: #10203A; opacity: 1;
  box-shadow: 0 0 0 1px rgba(47,129,247,.3), 0 0 22px -4px rgba(47,129,247,.55);
}
.node.is-success {
  border-color: var(--green); background: #0E1C13; opacity: 1;
  box-shadow: 0 0 20px -6px rgba(63,185,80,.45);
}
.node.is-blocked {
  border-color: var(--red); background: #1D1114; opacity: 1;
  box-shadow: 0 0 20px -6px rgba(248,81,73,.45);
}
.node.is-waiting {
  border-color: var(--amber); background: #1C1609; opacity: 1;
  box-shadow: 0 0 20px -6px rgba(210,153,34,.45);
}

.edge  { fill: none; stroke: #263346; stroke-width: 1.15; }
.pulse {
  fill: none; stroke: var(--blue); stroke-width: 2; opacity: 0;
  stroke-linecap: round;
  filter: drop-shadow(0 0 4px rgba(47,129,247,0.85));
}
/* `fire`, not `live`: `.live` is the "Ao vivo" badge, and a connection that
   borrowed its class borrowed its inline-flex box and its pulsing green dot. */
.pulse.fire {
  opacity: 1;
  stroke-dasharray: 26 2000; stroke-dashoffset: 2026;
  animation: travel var(--dur, 900ms) cubic-bezier(.4,0,.3,1) forwards;
}
@keyframes travel {
  0%   { stroke-dashoffset: 2026; opacity: 0; }
  14%  { opacity: 1; }
  86%  { opacity: 1; }
  100% { stroke-dashoffset: 1690; opacity: 0; }
}

.bubble {
  border: 1px solid #2F4A6E; border-radius: 9px; background: #0F1A29;
  padding: 7px 10px; font-size: 11px; line-height: 1.38; color: #D7E3F1;
  opacity: 0; transform: translateY(5px); pointer-events: none;
  transition: opacity .32s ease, transform .32s ease;
  box-shadow: 0 10px 28px rgba(0,0,0,.55);
}
.bubble.on { opacity: 1; transform: translateY(0); }
.bubble.verdict { border-color: #6A2C33; background: #1A1013; color: #F2D6D8; }
.bubble.think span { letter-spacing: 4px; color: var(--muted); }

/* ======================================================= the narrow flow
   Hidden until the frame is narrow; see the layout rules at the end. */
.flow { display: none; }
.stage .head { font-size: 11px; margin: 0 0 7px; }
.stage .grid { display: grid; gap: 6px; }
.g-events, .g-tools { grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); }
.g-agents { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
.g-core { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.flow .node { height: auto; min-height: 36px; padding: 6px 8px; gap: 8px; }
.flow .node .gl {
  flex: 0 0 22px; width: 22px; height: 22px; font-size: 11px; border-radius: 6px;
}
.flow .node b { font-size: 12.5px; line-height: 1.2; }
.flow .node em { font-size: 11.5px; margin-top: 1px; }
/* The name says it: an event is an event, an integration is an integration,
   and an agent's speciality is its name. The heading of each layer already
   carries the category, so a second line of it per card only costs height. */
.flow .k-event em, .flow .k-tool em, .flow .k-agent em { display: none; }
.flow .k-core { grid-column: 1 / -1; border-radius: 12px; min-height: 48px; }
.flow .k-core b { font-size: 15px; }
.flow .k-core .gl { font-size: 14px; }

/* A link between two layers: a short rail with an arrowhead, and a light that
   runs along it -- downwards when the work goes deeper into the stack, back up
   when the control plane answers an agent. */
.link {
  position: relative; height: 26px; margin: 3px auto; width: 2px;
  background: #263346; border-radius: 2px;
}
.link::after {
  content: ""; position: absolute; left: 50%; bottom: -2px;
  transform: translateX(-50%);
  border-left: 5px solid transparent; border-right: 5px solid transparent;
  border-top: 6px solid #2A3A52;
}
.link i {
  position: absolute; left: 50%; top: 0; width: 8px; height: 8px;
  margin-left: -4px; border-radius: 50%; background: var(--blue);
  box-shadow: 0 0 8px rgba(47,129,247,.9); opacity: 0;
}
.link.fire { background: #2C5AA0; }
.link.fire i { animation: drop var(--dur, 900ms) ease-in-out forwards; }
.link.fire.up i { animation-name: rise-up; }
@keyframes drop {
  0% { top: -4px; opacity: 0; } 15% { opacity: 1; }
  85% { opacity: 1; } 100% { top: 22px; opacity: 0; }
}
@keyframes rise-up {
  0% { top: 22px; opacity: 0; } 15% { opacity: 1; }
  85% { opacity: 1; } 100% { top: -4px; opacity: 0; }
}

/* What the current node is saying. In the diagram this is a bubble beside the
   node; in the flow it is one line under the layers, with the speaker named,
   because a bubble floating over a phone-width grid covers the nodes it is
   about. */
.talk { display: none; margin-top: 10px; }
.talk .stack { display: grid; width: 100%; }
.talk .say {
  border: 1px solid #2F4A6E; border-radius: 9px; background: #0F1A29;
  padding: 8px 10px; font-size: 13px; line-height: 1.45; color: #D7E3F1;
}
.talk .say b { display: block; font-size: 11px; color: var(--blue-2); margin-bottom: 2px; }
.talk .say.verdict { border-color: #6A2C33; background: #1A1013; color: #F2D6D8; }
.talk .say.think { color: var(--muted); letter-spacing: 3px; }
.talk .idle { font-size: 12px; color: var(--dim); padding: 8px 2px; }

/* ============================================================== trace */
.trace { flex: 1 1 auto; display: flex; flex-direction: column; min-height: 0; }
.rows  {
  flex: 1 1 0; min-height: 120px; overflow-y: auto; scrollbar-width: thin;
  scrollbar-color: #2A3441 transparent; position: relative; padding-left: 2px;
}
.rows::-webkit-scrollbar { width: 6px; }
.rows::-webkit-scrollbar-thumb { background: #2A3441; border-radius: 3px; }
.row {
  display: grid; grid-template-columns: 18px 1fr; gap: 9px;
  align-items: start; padding: 7px 0 8px; position: relative;
  animation: rise .32s ease-out both;
}
/* The spine. A timeline reads as a sequence; separate rows read as a list. */
.row::before {
  content: ""; position: absolute; left: 8px; top: 18px; bottom: -8px;
  width: 1px; background: #1C2531;
}
.row:last-child::before { display: none; }
@keyframes rise { from { opacity: 0; transform: translateY(-4px); } }
.row .dot {
  width: 17px; height: 17px; border-radius: 50%; margin-top: 2px; z-index: 1;
  display: flex; align-items: center; justify-content: center;
  font-size: 8.5px; font-style: normal;
  border: 1px solid var(--blue); background: #10203A; color: var(--blue-2);
}
.row.s-success .dot { border-color: var(--green); background: #0E1C13; color: var(--green); }
.row.s-blocked .dot { border-color: var(--red);   background: #1D1114; color: var(--red); }
.row.s-waiting .dot { border-color: var(--amber); background: #1C1609; color: var(--amber); }
.row time { font-size: 11px; color: var(--dim); font-variant-numeric: tabular-nums; }
.row .who { font-size: 12.5px; font-weight: 600; color: #E6EDF3; }
.row .what { font-size: 11.5px; color: var(--muted); line-height: 1.42; display: block; }

/* The verdict: one box per scenario, stacked, so the space is reserved before
   the first verdict arrives and does not change when the next one does. */
.verdicts { display: grid; margin-top: 10px; }
.verdictbox {
  grid-area: 1 / 1; visibility: hidden; opacity: 0;
  border: 1px solid var(--line); border-radius: 8px;
  padding: 10px 11px; transition: opacity .35s ease;
  background: var(--surface);
}
.verdictbox.on { visibility: visible; opacity: 1; }
.verdictbox.v-blocked { border-color: #5A2A2E; background: #150E11; }
.verdictbox.v-success { border-color: #24402B; background: #0D1611; }
.verdictbox.v-waiting { border-color: #4A3A16; background: #17130A; }
/* Before the verdict: the reserved space says what will appear in it, rather
   than sitting empty and reading as a gap in the card. */
.verdictbox.v-idle { border-style: dashed; background: transparent; }
.verdictbox.v-idle h4 { display: none; }
.verdictbox.v-idle p { color: var(--dim); }
.verdictbox h4 { margin: 0 0 4px; font-size: 12.5px; font-weight: 650; }
.verdictbox p  { margin: 0; font-size: 11.5px; color: var(--muted); line-height: 1.45; }

.legend {
  display: flex; gap: 14px; flex-wrap: wrap; padding-top: 10px;
  margin: auto 0 0; border-top: 1px solid var(--line);
  font-size: 11px; color: var(--dim);
}
.legend i { font-style: normal; }
.legend .sw {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  margin-right: 5px; vertical-align: middle;
}
.note { font-size: 11px; color: var(--dim); padding-top: 8px; line-height: 1.5; margin: 0; }

/* ============================================================ layouts
   One breakpoint per layout and nothing else competing with them. The widths
   are the frame's own, which is what the drawing actually has to fit. */

/* Medium: the diagram gets the whole width and the trace moves under it.
   Beside the trace the diagram drew at 0.82 in a 1440 desktop, which put its
   column headings at 7.8px; across the whole width it draws at about 1.1.
   From 820 up the narrowest it gets is 0.88. The list has a fixed height so
   the frame's height does not depend on the scenario. */
@media (max-width: 1279px) {
  .wrap { flex-direction: column; gap: 12px; }
  .left, .right { flex: none; width: 100%; min-width: 0; }
  /* Hidden, not scrollable: a list that scrolls inside a page that scrolls
     catches the swipe. The newest rows are the ones shown, and the fade says
     there are older ones above rather than cutting a line in half. */
  .rows {
    flex: none; height: 188px; min-height: 0; overflow: hidden;
    -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 34px);
            mask-image: linear-gradient(to bottom, transparent 0, #000 34px);
  }
}

/* Narrow: the flow replaces the diagram. Readable sizes throughout -- 13px
   names, 11.5px roles, 12-13px trace -- and no horizontal scaling at all. */
@media (max-width: 819px) {
  .scene { display: none; }
  .flow { display: block; }
  .talk { display: block; }
  .card { padding: 12px 12px; }
  .bar { gap: 6px 10px; padding-bottom: 8px; }
  .bar h3 { font-size: 1rem; flex: 1 1 auto; }
  .now { margin-left: 0; flex: 1 1 100%; font-size: 0.8rem; }
  .live { font-size: 0.7rem; }
  .head { font-size: 11px; }
  .rows { height: 236px; }
  .row .who { font-size: 13px; }
  .row .what { font-size: 12.5px; }
  .row time { font-size: 11px; }
  .verdictbox h4 { font-size: 13px; }
  .verdictbox p { font-size: 12.5px; }
  .legend { font-size: 11.5px; gap: 8px 14px; }
  .note { font-size: 11.5px; }
}
@media (max-width: 359px) {
  .g-core { grid-template-columns: minmax(0, 1fr); }
}

@media (prefers-reduced-motion: reduce) {
  .pulse.fire { animation: none; opacity: .5; stroke-dasharray: none;
                stroke-dashoffset: 0; }
  .link.fire i { animation: none; opacity: 0; }
  .row { animation: none; }
  .node, .bubble, .verdictbox { transition: none; }
}
"""


_JS: Final[str] = """
(function () {
  var D = window.__SCENE__;
  var reduced = window.matchMedia &&
                window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var $ = function (id) { return document.getElementById(id); };
  var rows = $('rows'), nowIdx = $('nowIdx');
  var timer = null, scenarioAt = 0, shown = [];

  var STATES = ['is-running', 'is-success', 'is-blocked', 'is-waiting'];
  var MARKS = { running: '\\u25CF', success: '\\u2713', blocked: '\\u2715',
                waiting: '\\u25CB' };

  /* Both drawings of a node: the diagram's and the flow's. */
  function nodes(id) {
    return [ $('n-' + id), $('m-' + id) ].filter(Boolean);
  }

  /* ---- the stacks: every alternative built once, before the first frame,
     so the space they need is known before Streamlit measures the document. */
  function stackOf(host, items, build) {
    var map = {};
    items.forEach(function (item) {
      if (map[item.key]) return;
      var el = build(item);
      host.appendChild(el);
      map[item.key] = el;
    });
    return map;
  }
  function only(map, key) {
    Object.keys(map).forEach(function (k) { map[k].classList.remove('on'); });
    if (key !== null && map[key]) map[key].classList.add('on');
  }

  var titles = stackOf($('nowName'), D.scenarios.map(function (s) {
    return { key: s.id, text: s.title };
  }), function (item) {
    var b = document.createElement('span');
    b.textContent = item.text;
    return b;
  });

  var verdicts = stackOf($('verdicts'), [{ key: '__idle__', v: {
    state: 'idle', label: '', why: D.verdictIdle || '' } }].concat(
    D.scenarios.map(function (s) { return { key: s.id, v: s.verdict || {} }; })
  ), function (item) {
    var box = document.createElement('div');
    box.className = 'verdictbox v-' + (item.v.state || 'success');
    box.innerHTML = '<h4></h4><p></p>';
    box.querySelector('h4').textContent = item.v.label || '';
    box.querySelector('p').textContent = item.v.why || '';
    return box;
  });

  var sayings = [{ key: '__idle__' }];
  D.scenarios.forEach(function (s) {
    s.steps.forEach(function (step) {
      if (step.bubble) {
        sayings.push({ key: step.node + '|' + step.bubble, step: step });
      }
    });
  });
  var talk = stackOf($('talkStack'), sayings, function (item) {
    var el = document.createElement('div');
    if (!item.step) {
      el.className = 'idle';
      el.textContent = D.idle || '';
      return el;
    }
    el.className = 'say' + (item.step.bubbleKind ? ' ' + item.step.bubbleKind : '');
    var who = document.createElement('b');
    who.textContent = item.step.role ||
                      ((D.labels[item.step.node] || {}).name || item.step.node);
    var what = document.createElement('span');
    what.textContent = item.step.bubble;
    el.appendChild(who);
    el.appendChild(what);
    return el;
  });

  function clearAll() {
    shown = [];
    Object.keys(D.nodes).forEach(function (id) {
      nodes(id).forEach(function (el) {
        STATES.forEach(function (c) { el.classList.remove(c); });
        var role = el.querySelector('em');
        if (role && role.getAttribute('data-base') !== null) {
          role.textContent = role.getAttribute('data-base');
        }
      });
      var bub = $('b-' + id);
      if (bub) bub.classList.remove('on', 'verdict', 'think');
    });
    rows.innerHTML = '';
    only(verdicts, '__idle__');
    only(talk, '__idle__');
  }

  function bubble(step) {
    var node = step.node, text = step.bubble, kind = step.bubbleKind || null;
    only(talk, node + '|' + text);
    var el = $('b-' + node);
    if (!el) return;
    /* At most two: the one asking and the control plane answering, which is
       also why there are exactly two slots to put them in. */
    shown = shown.filter(function (id) { return id !== node; });
    shown.push(node);
    while (shown.length > 2) {
      var old = $('b-' + shown.shift());
      if (old) old.classList.remove('on');
    }
    el.querySelector('span').textContent = text;
    el.classList.remove('verdict', 'think');
    if (kind) el.classList.add(kind);
    el.classList.add('on');
  }

  function hideBubbles() {
    shown = [];
    Object.keys(D.nodes).forEach(function (id) {
      var el = $('b-' + id);
      if (el) el.classList.remove('on');
    });
  }

  function restart(el, ms) {
    el.classList.remove('fire');
    void el.getBoundingClientRect();          /* restart the CSS animation */
    el.style.setProperty('--dur', ms + 'ms');
    el.classList.add('fire');
  }

  function firePulse(from, to, ms) {
    var edge = document.getElementById('e-' + from + '-' + to);
    if (edge) restart(edge, ms);
    /* The flow: light every link between the two layers, in the direction
       the work is moving. */
    var a = D.stage[from], b = D.stage[to];
    if (a === undefined || b === undefined || a === b) return;
    var lo = Math.min(a, b), hi = Math.max(a, b);
    for (var i = lo; i < hi; i++) {
      var link = $('ml-' + i);
      if (!link) continue;
      link.classList.toggle('up', b < a);
      restart(link, ms);
    }
  }

  function clock() {
    var d = new Date();
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function addRow(step) {
    var who = (D.labels[step.node] || {}).name || step.node;
    if (step.role) who = step.role;
    var state = step.state || 'running';
    var row = document.createElement('div');
    row.className = 'row s-' + state;
    row.innerHTML =
      '<i class="dot"></i><span><time></time> <span class="who"></span>' +
      '<span class="what"></span></span>';
    row.querySelector('.dot').textContent = MARKS[state] || MARKS.running;
    row.querySelector('time').textContent = clock();
    row.querySelector('.who').textContent = who;
    row.querySelector('.what').textContent = step.note || '';
    rows.appendChild(row);
    while (rows.childNodes.length > D.maxRows) {
      rows.removeChild(rows.firstChild);
    }
    rows.scrollTop = rows.scrollHeight;
  }

  function light(step) {
    nodes(step.node).forEach(function (el) {
      STATES.forEach(function (c) { el.classList.remove(c); });
      el.classList.add('is-' + (step.state || 'running'));
      if (step.role) {
        var role = el.querySelector('em');
        if (role) {
          if (role.getAttribute('data-base') === null) {
            role.setAttribute('data-base', role.textContent);
          }
          role.textContent = step.role;
        }
      }
    });
  }

  function runStep(scenario, i) {
    var steps = scenario.steps;
    if (i >= steps.length) { return finish(scenario); }
    var step = steps[i];
    var prev = i === 0 ? scenario.trigger : steps[i - 1].node;

    if (prev && prev !== step.node) firePulse(prev, step.node, D.pulseMs);
    light(step);
    if (step.bubble) bubble(step);
    addRow(step);

    timer = setTimeout(function () { runStep(scenario, i + 1); }, D.stepMs);
  }

  function finish(scenario) {
    only(verdicts, scenario.id);
    if (reduced) return;
    timer = setTimeout(next, D.holdMs);
  }

  function next() {
    scenarioAt = (scenarioAt + 1) % D.scenarios.length;
    play(scenarioAt);
  }

  function play(index) {
    var scenario = D.scenarios[index];
    clearAll();
    only(titles, scenario.id);
    nowIdx.textContent = (index + 1) + '/' + D.scenarios.length;
    if (scenario.trigger) {
      nodes(scenario.trigger).forEach(function (t) { t.classList.add('is-running'); });
    }
    if (reduced) {
      /* No loop, no pulses: every step's end state at once, the whole trace
         readable, and the scene holds still. */
      scenario.steps.forEach(function (step) {
        light(step);
        if (step.bubble) bubble(step);
        addRow(step);
      });
      finish(scenario);
      return;
    }
    hideBubbles();
    timer = setTimeout(function () { runStep(scenario, 0); }, 280);
  }

  /* A hidden tab should not animate. One timer exists at a time, and it is
     always the one this handler cancels. */
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (timer) { clearTimeout(timer); timer = null; }
    } else if (!timer && !reduced) {
      play(scenarioAt);
    }
  });

  play(0);
})();
"""


def _document(
    payload: dict[str, Any], svg: str, chrome: dict[str, Any], flow: str = ""
) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    legend = "".join(
        f'<i><span class="sw" style="background:{colour}"></span>{text}</i>'
        for colour, text in chrome["legend"]
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{_CSS}</style></head><body>"
        '<div class="wrap">'
        '  <div class="card left">'
        '    <div class="bar">'
        f'      <h3>{chrome["title"]}</h3>'
        f'      <span class="live"><i></i>{chrome["live"]}</span>'
        f'      <span class="now">{chrome["now_label"]} '
        '<b class="stack" id="nowName"></b> <span id="nowIdx"></span></span>'
        "    </div>"
        f"    {svg}"
        f"    {flow}"
        '    <div class="talk" aria-live="polite">'
        '<div class="stack" id="talkStack"></div></div>'
        f'    <div class="legend">{legend}</div>'
        f'    <p class="note">{chrome["note"]}</p>'
        "  </div>"
        '  <div class="card right">'
        '    <div class="bar">'
        f'      <h3>{chrome["trace_title"]}</h3>'
        f'      <span class="tag">{chrome["demo_tag"]}</span>'
        "    </div>"
        '    <div class="trace"><div class="rows" id="rows"></div>'
        '    <div class="verdicts" id="verdicts"></div></div>'
        "  </div>"
        "</div>"
        f"<script>window.__SCENE__ = {data};</script>"
        f"<script>{_JS}</script>"
        "</body></html>"
    )


def build(
    *,
    scenarios: list[dict[str, Any]],
    labels: dict[str, dict[str, str]],
    headings: dict[str, str],
    chrome: dict[str, Any],
) -> str:
    """The whole scene as one self-contained HTML document."""
    payload = {
        "scenarios": scenarios,
        "labels": labels,
        "nodes": {key: True for key in NODES},
        "stage": {key: stage_of(key) for key in NODES},
        "idle": chrome.get("idle", ""),
        "verdictIdle": chrome.get("verdict_idle", ""),
        "stepMs": 1250,
        "pulseMs": 900,
        "holdMs": 2600,
        "maxRows": 9,
    }
    return _document(
        payload,
        _svg(labels, headings, scenarios),
        chrome,
        _flow(labels, headings),
    )


def render(
    *,
    scenarios: list[dict[str, Any]],
    labels: dict[str, dict[str, str]],
    headings: dict[str, str],
    chrome: dict[str, Any],
) -> None:
    """Draw the scene and start its loop.

    All prose arrives already translated -- this module holds geometry and
    behaviour, and knows nothing about which language it is drawing. The frame
    takes the height of its content (see the module docstring for why that,
    and not a number, is the only height that is right at every width).
    """
    st.iframe(
        build(scenarios=scenarios, labels=labels, headings=headings, chrome=chrome),
        height="content",
    )
