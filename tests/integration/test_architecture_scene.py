"""The staged architecture scene: what it must be, and what it must not claim.

The scene is the one thing on the dashboard that shows work it did not do, so
most of this file is about the boundary. It has to be legible (nothing drawn on
top of anything else), it has to be honest (a demonstration label, the five real
agents named, no claim of a live integration), it has to be self-contained (no
network, no provider, no database), and its two languages have to stay the same
diagram.

The geometry tests exist because both of their properties were broken when the
scene was first drawn: a bubble anchored over its speaker covered two agent
cards, and moving it clear of the agent grid put it on the events column
instead. A drawing has no type checker, so the invariants are asserted here.
"""

from __future__ import annotations

import json
import sys
from itertools import pairwise
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

pytest.importorskip("streamlit")

import arch_en  # noqa: E402
import arch_pt  # noqa: E402
import architecture_scene as scene  # noqa: E402

LOCALES = (("pt", arch_pt), ("en", arch_en))


def _boxes():
    return {
        key: (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])
        for key, box in scene.NODES.items()
    }


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# ============================================================ the two languages


@pytest.mark.parametrize("code,module", LOCALES)
def test_every_scenario_names_a_node_that_exists(code, module):
    for scenario in module.ARCH_SCENARIOS:
        trigger = scenario.get("trigger")
        if trigger is not None:
            assert trigger in scene.NODES, f"{code}/{scenario['id']}: {trigger}"
        for step in scenario["steps"]:
            assert step["node"] in scene.NODES, f"{code}/{scenario['id']}"


@pytest.mark.parametrize("code,module", LOCALES)
def test_every_node_is_labelled(code, module):
    for key in scene.NODES:
        label = module.ARCH_NODES.get(key)
        assert label, f"{code}: {key} has no label"
        assert label["name"].strip()
        assert label["role"].strip()


def test_the_two_languages_are_the_same_diagram():
    """Same scenarios, same order, same path through the same nodes.

    Only the prose may differ. A step added to one language and not the other
    would be two different products sharing a URL.
    """
    assert [s["id"] for s in arch_pt.ARCH_SCENARIOS] == [
        s["id"] for s in arch_en.ARCH_SCENARIOS
    ]
    for pt, en in zip(arch_pt.ARCH_SCENARIOS, arch_en.ARCH_SCENARIOS, strict=True):
        assert pt["trigger"] == en["trigger"], pt["id"]
        assert [x["node"] for x in pt["steps"]] == [
            x["node"] for x in en["steps"]
        ], pt["id"]
        assert [x.get("state") for x in pt["steps"]] == [
            x.get("state") for x in en["steps"]
        ], pt["id"]
        assert [x.get("role") for x in pt["steps"]] == [
            x.get("role") for x in en["steps"]
        ], pt["id"]
        assert (pt.get("verdict") or {}).get("state") == (
            en.get("verdict") or {}
        ).get("state"), pt["id"]


def test_the_prose_actually_differs():
    """Mirrored structure is not an excuse for an untranslated string."""
    for pt, en in zip(arch_pt.ARCH_SCENARIOS, arch_en.ARCH_SCENARIOS, strict=True):
        assert pt["title"] != en["title"] or len(pt["title"]) < 12, pt["id"]
        for a, b in zip(pt["steps"], en["steps"], strict=True):
            assert a["note"] != b["note"], f"{pt['id']}: untranslated note"


@pytest.mark.parametrize("code,module", LOCALES)
def test_there_are_ten_scenarios_with_unique_ids(code, module):
    ids = [s["id"] for s in module.ARCH_SCENARIOS]
    assert len(ids) == 10
    assert len(set(ids)) == 10


# =================================================================== geometry


def test_no_two_nodes_overlap():
    boxes = _boxes()
    for first in boxes:
        for second in boxes:
            if first < second:
                assert not _overlap(boxes[first], boxes[second]), (
                    f"{first} overlaps {second}"
                )


def test_no_bubble_lands_on_a_node():
    """The defect this file was written for.

    A bubble is only useful if the thing it is talking about is still visible.
    """
    boxes = _boxes()
    for node in scene.NODES:
        x, y, width, height, _tail = scene._bubble_box(node)
        bubble = (x, y, x + width, y + height)
        for other, box in boxes.items():
            assert not _overlap(bubble, box), (
                f"{node}'s bubble covers {other}"
            )


def test_nothing_is_drawn_outside_the_canvas():
    for key, box in scene.NODES.items():
        assert box["x"] >= 0 and box["y"] >= 0, key
        assert box["x"] + box["w"] <= scene._W, key
        assert box["y"] + box["h"] <= scene._H, key
    for node in scene.NODES:
        x, y, width, height, _ = scene._bubble_box(node)
        assert x >= 0 and x + width <= scene._W, node
        assert y >= 0 and y + height <= scene._H, node


def test_every_hop_a_scenario_makes_gets_an_edge():
    """A pulse with no path to travel is a step that renders as nothing."""
    edges = set(scene._edges(list(arch_pt.ARCH_SCENARIOS)))
    for scenario in arch_pt.ARCH_SCENARIOS:
        hops = [s["node"] for s in scenario["steps"]]
        if scenario.get("trigger"):
            hops.insert(0, scenario["trigger"])
        for first, second in pairwise(hops):
            if first != second:
                assert (first, second) in edges, f"{first} -> {second}"


# ==================================================================== honesty


def test_the_scene_declares_itself_a_demonstration():
    html = _render_html()
    assert "DEMO" in html.upper() or "DEMONSTRA" in html.upper()


def test_the_live_indicator_says_demo_in_both_languages():
    """A pulsing green dot beside "live" reads as production traffic.

    The word next to it is the only thing that stops it, so it is pinned rather
    than left to whoever edits the catalogue next.
    """
    import strings_en
    import strings_pt

    for catalogue in (strings_pt.STRINGS, strings_en.STRINGS):
        assert "DEMO" in catalogue["scene.live"].upper(), catalogue["scene.live"]


def test_the_note_says_the_backend_has_five_agents():
    """The specialised grid must not be read as an inventory of components."""
    import strings_en
    import strings_pt

    from agent_platform.models import AgentName

    assert len(list(AgentName)) == 5, (
        "the platform's agent count changed; the scene's note claims five"
    )
    for catalogue in (strings_pt.STRINGS, strings_en.STRINGS):
        note = catalogue["scene.note"]
        assert "cinco" in note or "five" in note, note


@pytest.mark.parametrize("code,module", LOCALES)
def test_integration_points_are_never_called_connected(code, module):
    for key, label in module.ARCH_NODES.items():
        if key.startswith("t_"):
            assert label["role"] == module.INTEGRATION, key


def test_the_scene_reaches_nothing_over_the_network():
    """Self-contained by construction: no src, no fetch, no external origin."""
    html = _render_html()
    for forbidden in ("http://", "https://", "fetch(", "XMLHttpRequest",
                      "WebSocket", "<img", "<link", "@import"):
        if forbidden in ("http://", "https://"):
            # The XHTML namespace on foreignObject children is the one URL that
            # has to be there, and it is a namespace name, not a fetch.
            leftover = html.replace('xmlns="http://www.w3.org/1999/xhtml"', "")
            leftover = leftover.replace(
                'xmlns="http://www.w3.org/2000/svg"', ""
            )
            assert forbidden not in leftover, forbidden
        else:
            assert forbidden not in html, forbidden


def test_the_scene_touches_no_platform_object():
    """It takes prose and geometry. It cannot reach a repository or a provider."""
    import inspect

    source = inspect.getsource(scene)
    for forbidden in ("AgentPlatform", "repository", "provider", "collect_metrics"):
        assert forbidden not in source.split('"""')[2], forbidden


def test_reduced_motion_is_handled_in_both_places():
    """The CSS stops the animations; the script stops the loop.

    Either alone leaves a reader who asked for stillness with a moving page.
    """
    assert "prefers-reduced-motion: reduce" in scene._CSS
    assert "prefers-reduced-motion: reduce" in scene._JS
    assert "if (reduced) return;" in scene._JS


def test_the_loop_wraps_rather_than_stopping():
    assert "% D.scenarios.length" in scene._JS


def test_only_one_timer_exists_at_a_time():
    """Ten scenarios on concurrent intervals would drift and then overlap."""
    assert "setInterval" not in scene._JS
    assert scene._JS.count("timer = setTimeout") >= 1
    assert "clearTimeout(timer)" in scene._JS


def test_a_hidden_tab_stops_animating():
    assert "visibilitychange" in scene._JS


# =================================================================== the states


def test_the_states_are_the_ones_the_real_trace_uses():
    """The demo and the Orquestrador page must not invent separate vocabularies."""
    import execution_view

    known = {state.name.lower() for state in execution_view.State}
    for module in (arch_pt, arch_en):
        for scenario in module.ARCH_SCENARIOS:
            for step in scenario["steps"]:
                assert step["state"] in known, step["state"]
            assert scenario["verdict"]["state"] in known, scenario["id"]


# ==================================================================== plumbing


def _render_html() -> str:
    labels = dict(arch_pt.ARCH_NODES)
    scenarios = list(arch_pt.ARCH_SCENARIOS)
    return scene._document(
        {"scenarios": scenarios, "labels": labels, "nodes": {k: True for k in scene.NODES}},
        scene._svg(labels, dict(arch_pt.ARCH_HEADINGS), scenarios),
        {
            "title": "Arquitetura em ação",
            "live": "Ao vivo (demo)",
            "trace_title": "Execução em tempo real",
            "demo_tag": "Demonstração",
            "now_label": "Cenário",
            "note": "nota",
            "legend": [("#2F81F7", "Executando")],
        },
    )


def test_the_payload_is_valid_json_and_cannot_close_the_script_tag():
    """`</script>` inside the data would end the block early and break the page."""
    html = _render_html()
    start = html.index("window.__SCENE__ = ") + len("window.__SCENE__ = ")
    end = html.index("};", start) + 1
    payload = json.loads(html[start:end].replace("<\\/", "</"))
    assert len(payload["scenarios"]) == 10
    assert "</script>" not in html[start:end]


# ============================================================ the narrow layout
#
# The scene used to be one 900-unit SVG at every width, in a frame fixed at
# 612px. On a phone the SVG drew at about 0.33 -- 3 to 9px text -- and the frame
# overflowed whenever the verdict box opened. These pin the replacement: a flow
# layout for narrow frames, a frame that takes its content's height, and text
# that changes without changing the height. The browser-level proof is
# tests/visual/check_layout.cjs; these are the structural guarantees it relies on.


def _built() -> str:
    return scene.build(
        scenarios=list(arch_pt.ARCH_SCENARIOS),
        labels=dict(arch_pt.ARCH_NODES),
        headings=dict(arch_pt.ARCH_HEADINGS),
        chrome={
            "title": "t", "live": "l", "trace_title": "tt", "demo_tag": "d",
            "now_label": "n", "note": "x", "idle": "i", "verdict_idle": "v",
            "legend": [("#2F81F7", "Executando")],
        },
    )


def test_every_node_is_drawn_in_the_flow_as_well():
    """Both drawings light the same node ids; a node missing from one is dark."""
    html = _built()
    for node in scene.NODES:
        assert f'id="n-{node}"' in html, node
        assert f'id="m-{node}"' in html, node


def test_every_node_belongs_to_exactly_one_layer():
    stages = {scene.stage_of(node) for node in scene.NODES}
    assert stages == set(range(len(scene.STAGES)))


def test_there_is_a_link_between_each_pair_of_layers():
    html = _built()
    for index in range(len(scene.STAGES) - 1):
        assert f'id="ml-{index}"' in html


def test_the_frame_takes_its_contents_height(monkeypatch):
    """No pixel height: the one number that was wrong at every phone width."""
    calls = []
    monkeypatch.setattr(scene.st, "iframe", lambda *a, **k: calls.append(k))
    scene.render(
        scenarios=list(arch_pt.ARCH_SCENARIOS),
        labels=dict(arch_pt.ARCH_NODES),
        headings=dict(arch_pt.ARCH_HEADINGS),
        chrome={"title": "", "live": "", "trace_title": "", "demo_tag": "",
                "now_label": "", "note": "", "legend": []},
    )
    assert calls == [{"height": "content"}]


def test_the_scene_no_longer_uses_the_deprecated_components_api():
    import inspect

    assert "components.v1" not in inspect.getsource(scene).split('"""', 2)[2]


def test_changing_text_is_stacked_so_the_height_cannot_move():
    """Title, speech and verdict: every alternative present, one visible."""
    assert ".stack > * { grid-area: 1 / 1; visibility: hidden; }" in scene._CSS
    assert "grid-area: 1 / 1; visibility: hidden;" in scene._CSS.split(".verdictbox {")[1]
    for host in ("nowName", "talkStack", "verdicts"):
        assert f"$('{host}')" in scene._JS, host


def test_one_breakpoint_per_layout():
    """Three competing max-width:640px blocks were how the old layout broke."""
    css = scene._CSS
    assert css.count("@media (max-width: 1279px)") == 1
    assert css.count("@media (max-width: 819px)") == 1
    assert "max-width: 640px" not in css and "max-width: 720px" not in css
    assert "scale(0.89)" not in css


def test_the_document_never_traps_a_scroll():
    """`overflow: hidden` on the frame's root stopped the page scrolling over it."""
    import re

    without_comments = re.sub(r"/\*.*?\*/", "", scene._CSS, flags=re.S)
    root_rules = without_comments.split(":root")[0]
    assert "overflow" not in root_rules


def test_the_animation_class_does_not_collide_with_the_live_badge():
    assert "classList.add('live')" not in scene._JS
    assert ".link.fire" in scene._CSS and ".pulse.fire" in scene._CSS
