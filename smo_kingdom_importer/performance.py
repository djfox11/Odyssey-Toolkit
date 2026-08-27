from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import wraps
import json
import time
from typing import Any, Callable, TypeVar


F = TypeVar("F", bound=Callable[..., Any])


@dataclass(slots=True)
class PerformanceTimings:
    totals_seconds: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, category: str, seconds: float) -> None:
        self.totals_seconds[category] = (
            self.totals_seconds.get(category, 0.0) + float(seconds)
        )
        self.counts[category] = self.counts.get(category, 0) + 1

    def seconds(self, category: str) -> float:
        return self.totals_seconds.get(category, 0.0)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "totals_seconds": {
                key: round(value, 6)
                for key, value in sorted(self.totals_seconds.items())
            },
            "counts": dict(sorted(self.counts.items())),
        }

    def to_json(self) -> str:
        return json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))


_ACTIVE_TIMINGS: ContextVar[PerformanceTimings | None] = ContextVar(
    "smo_active_performance_timings",
    default=None,
)


def set_active_timings(
    timings: PerformanceTimings,
) -> Token[PerformanceTimings | None]:
    return _ACTIVE_TIMINGS.set(timings)


def reset_active_timings(token: Token[PerformanceTimings | None]) -> None:
    _ACTIVE_TIMINGS.reset(token)


def active_timings() -> PerformanceTimings | None:
    return _ACTIVE_TIMINGS.get()


def record_timing(category: str, seconds: float) -> None:
    timings = active_timings()

    if timings is not None:
        timings.add(category, seconds)


def timed(category: str) -> Callable[[F], F]:
    def decorate(function: F) -> F:
        @wraps(function)
        def measured(*args: Any, **kwargs: Any) -> Any:
            timings = active_timings()

            if timings is None:
                return function(*args, **kwargs)

            started = time.perf_counter()

            try:
                return function(*args, **kwargs)
            finally:
                timings.add(category, time.perf_counter() - started)

        return measured  # type: ignore[return-value]

    return decorate


_SUMMARY_CATEGORIES = (
    ("preparation_total", "Preparation"),
    ("stage_data_byml_parse", "StageData/BYML"),
    ("placement_collection", "Placements"),
    ("zone_expansion", "Zone expansion"),
    ("object_data_resolution", "ObjectData"),
    ("szs_loading", "SZS/Yaz0/SARC"),
    ("bfres_parsing", "BFRES"),
    ("bntx_texture_decoding", "BNTX decode"),
    ("texture_cache_read", "Texture cache reads"),
    ("texture_cache_write", "Texture cache writes"),
    ("blender_image_creation", "Images"),
    ("blender_mesh_creation", "Meshes"),
    ("blender_armature_creation", "Armatures"),
    ("blender_skin_binding", "Skin binding"),
    ("stage_lighting", "Stage lighting"),
    ("import_total", "Total import"),
)


def print_performance_summary(
    timings: PerformanceTimings,
    *,
    prefix: str = "[Odyssey Toolkit]",
) -> None:
    values = []

    for category, label in _SUMMARY_CATEGORIES:
        if category not in timings.totals_seconds:
            continue

        seconds = timings.seconds(category)
        count = timings.counts.get(category, 0)
        suffix = f", n={count}" if count > 1 else ""
        values.append(f"{label}: {seconds:.3f}s{suffix}")

    print(f"{prefix} Performance summary")
    print("  " + " | ".join(values), flush=True)