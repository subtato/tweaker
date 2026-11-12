from __future__ import annotations

import threading
from collections import defaultdict

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_observations: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, int]] = defaultdict(lambda: (0.0, 0))


def counter(name: str, value: float = 1.0, **labels: str) -> None:
    key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
    with _lock:
        _counters[key] += value


def observe(name: str, value: float, **labels: str) -> None:
    key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
    with _lock:
        total, count = _observations[key]
        _observations[key] = (total + value, count + 1)


def render_prometheus() -> str:
    lines: list[str] = []
    with _lock:
        counters = dict(_counters)
        observations = dict(_observations)
    emitted: set[str] = set()
    for (name, labels), value in sorted(counters.items()):
        if name not in emitted:
            lines.append(f"# TYPE {name} counter")
            emitted.add(name)
        lines.append(f"{name}{_labels(labels)} {value:g}")
    for (name, labels), (total, count) in sorted(observations.items()):
        if name not in emitted:
            lines.append(f"# TYPE {name} summary")
            emitted.add(name)
        lines.append(f"{name}_sum{_labels(labels)} {total:g}")
        lines.append(f"{name}_count{_labels(labels)} {count}")
    return "\n".join(lines) + ("\n" if lines else "")


def _labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{_escape(value)}"' for key, value in labels)
    return "{" + body + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
