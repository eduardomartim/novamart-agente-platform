"""Prometheus exposition, written by hand and on purpose.

There is a perfectly good client library. It is not used here for two reasons.

The first is the dependency: the image already carries what it needs, and a
metrics endpoint is a few hundred bytes of text formatting.

The second matters more. ``/metrics`` is a **new egress path** -- a URL that
returns internal state to whoever can reach it -- and every earlier phase of
this project has treated a new egress path as something to control rather than
adopt. Writing the exposition means every byte that leaves is one this module
chose. Nothing is auto-collected, nothing is reflected from a label a caller
supplied, and there is no registry that some other library can quietly add to.

What is deliberately absent
---------------------------
No label ever carries a value from outside the platform: no argument, no
request id, no user text, no tool output. Labels come only from closed sets the
platform owns -- a status, a decision, a tool name from the registry. That keeps
cardinality bounded *and* keeps the endpoint from becoming a way to read back
data that the output-security layer exists to mask.
"""

from __future__ import annotations

import threading
from typing import Any

#: Buckets for request latency, in seconds. Chosen against measured behaviour
#: rather than copied from a template: stub requests land in tens of
#: milliseconds, live ones around 4s, and the tail worth seeing is the retry
#: path out past 30s.
LATENCY_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0,
)


def _escape(value: str) -> str:
    """Escape a label value per the exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Counters:
    """Monotonic counters, keyed by metric name and a closed label set.

    Values only ever increase within a process, which is what a Prometheus
    counter promises. A restart resets them, and that is expected: Prometheus
    detects the reset from the drop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def increment(self, name: str, *, amount: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def snapshot(self) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        with self._lock:
            return dict(self._values)


class Histogram:
    """A single latency histogram with fixed buckets."""

    def __init__(self, buckets: tuple[float, ...] = LATENCY_BUCKETS) -> None:
        self._lock = threading.Lock()
        self._buckets = buckets
        self._counts = [0] * len(buckets)
        self._sum = 0.0
        self._total = 0

    def observe(self, seconds: float) -> None:
        with self._lock:
            self._sum += seconds
            self._total += 1
            for index, edge in enumerate(self._buckets):
                if seconds <= edge:
                    self._counts[index] += 1

    def render(self, name: str) -> list[str]:
        with self._lock:
            counts, total, total_sum = list(self._counts), self._total, self._sum
        lines = [
            f"# HELP {name} Request duration in seconds.",
            f"# TYPE {name} histogram",
        ]
        cumulative = 0
        for edge, count in zip(self._buckets, counts, strict=True):
            cumulative += count
            lines.append(f'{name}_bucket{{le="{edge}"}} {cumulative}')
        lines.append(f'{name}_bucket{{le="+Inf"}} {total}')
        lines.append(f"{name}_sum {total_sum}")
        lines.append(f"{name}_count {total}")
        return lines


#: Help text for every counter this module will emit. A metric with no entry
#: here is not rendered -- which makes the set of things that can leave the
#: process an explicit list rather than whatever happened to get incremented.
COUNTER_HELP: dict[str, str] = {
    "agent_requests_total": "Requests handled, by outcome.",
    "agent_policy_decisions_total": "Policy verdicts, by decision.",
    "agent_tool_calls_total": "Tool invocations, by tool and outcome.",
    "agent_grant_rejections_total": "Execution grants refused.",
    "agent_shared_state_failures_total": "Shared-state operations that failed.",
    "agent_llm_calls_total": "Model calls, by provider.",
    "agent_auth_refusals_total": "Requests refused by authentication or scope, by reason.",
}

#: Gauges read from the repository at scrape time. These are per-replica: the
#: repository is SQLite in a pod-local volume, so each pod reports what it
#: handled. That is the correct model for Prometheus, which aggregates across
#: instances -- but it is worth stating, because it means no single pod's
#: numbers are the whole picture.
SUMMARY_GAUGES: dict[str, tuple[str, str]] = {
    "agent_requests_recorded": ("requests", "Requests recorded by this replica."),
    "agent_requests_blocked": ("blocked", "Requests blocked by this replica."),
    "agent_tool_calls_recorded": ("tool_calls", "Tool calls recorded by this replica."),
    "agent_tool_failures_recorded": ("tool_failures", "Failed tool calls on this replica."),
    "agent_policy_denials_recorded": ("policy_denials", "Policy denials on this replica."),
    "agent_confirmations_recorded": ("confirmations", "Confirmations raised on this replica."),
    "agent_injection_flags_recorded": ("injection_flags", "Injection signals on this replica."),
    "agent_output_redactions_recorded": ("output_redactions", "Output redactions on this replica."),
    "agent_llm_calls_recorded": ("llm_calls", "Model calls recorded by this replica."),
}


def render(
    counters: Counters,
    duration: Histogram,
    *,
    summary: dict[str, Any] | None = None,
    build: dict[str, str] | None = None,
) -> str:
    """Produce the exposition body.

    ``summary`` comes from ``Repository.metrics_summary()``. Only the keys named
    in :data:`SUMMARY_GAUGES` are emitted; anything else the repository grows
    later stays internal until someone decides it should not.
    """
    lines: list[str] = []

    if build:
        labels = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(build.items()))
        lines += [
            "# HELP agent_build_info Build and runtime identity.",
            "# TYPE agent_build_info gauge",
            f"agent_build_info{{{labels}}} 1",
        ]

    grouped: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = {}
    for (name, labels_tuple), value in counters.snapshot().items():
        grouped.setdefault(name, []).append((labels_tuple, value))

    for name in sorted(grouped):
        if name not in COUNTER_HELP:
            continue
        lines += [f"# HELP {name} {COUNTER_HELP[name]}", f"# TYPE {name} counter"]
        for labels_tuple, value in sorted(grouped[name]):
            rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in labels_tuple)
            suffix = f"{{{rendered}}}" if rendered else ""
            lines.append(f"{name}{suffix} {value}")

    lines += duration.render("agent_request_duration_seconds")

    if summary:
        for metric, (key, help_text) in sorted(SUMMARY_GAUGES.items()):
            raw = summary.get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                # A summary key that is not a number is skipped rather than
                # rendered: a malformed metric breaks a whole scrape.
                continue
            lines += [
                f"# HELP {metric} {help_text}",
                f"# TYPE {metric} gauge",
                f"{metric} {value}",
            ]

    return "\n".join(lines) + "\n"


__all__ = [
    "COUNTER_HELP",
    "LATENCY_BUCKETS",
    "SUMMARY_GAUGES",
    "Counters",
    "Histogram",
    "render",
]
