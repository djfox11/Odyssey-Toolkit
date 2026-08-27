from __future__ import annotations

from pathlib import Path
from statistics import median
import sys
import time


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon
from smo_kingdom_importer import (
    actor_registry_cache_directory,
    texture_cache_directory,
)
from smo_kingdom_importer import actor_registry, texture_cache


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def timings(callback, *, repetitions: int = 30) -> list[float]:
    values: list[float] = []

    for _ in range(repetitions):
        started = time.perf_counter()
        callback()
        values.append(time.perf_counter() - started)

    return values


def run(romfs_root: Path) -> None:
    texture_root = texture_cache_directory()
    registry_root = actor_registry_cache_directory()
    original_texture_scan = texture_cache._scan_texture_cache_status
    original_registry_load = actor_registry.load_actor_registry

    def unexpected_texture_scan(_root: Path):
        raise AssertionError("Preferences triggered a recursive texture-cache scan")

    def unexpected_registry_load(_romfs: Path, _cache: Path):
        raise AssertionError("Preferences parsed and validated the actor registry")

    try:
        texture_cache._scan_texture_cache_status = unexpected_texture_scan
        actor_registry.load_actor_registry = unexpected_registry_load

        def texture_status() -> None:
            texture_cache._STATUS_CACHE.clear()
            texture_cache.texture_cache_status(texture_root)

        def registry_status() -> None:
            actor_registry._FILE_STATUS_CACHE.clear()
            actor_registry._RUNTIME_REGISTRIES.clear()
            actor_registry.cached_registry_file_status(
                romfs_root,
                registry_root,
            )

        texture_times = timings(texture_status)
        registry_times = timings(registry_status)
        sections = iter(
            (
                "TEXTURE_CACHE",
                "ACTOR_REGISTRY",
                "IMPORT",
            )
            * 30
        )
        navigation_times = timings(
            lambda: addon.set_preferences_section(next(sections))
        )
    finally:
        texture_cache._scan_texture_cache_status = original_texture_scan
        actor_registry.load_actor_registry = original_registry_load

    texture_median = median(texture_times)
    registry_median = median(registry_times)
    navigation_median = median(navigation_times)
    check(
        texture_median < 0.01,
        f"Deferred texture-cache status is too slow: {texture_median:.6f}s",
    )
    check(
        registry_median < 0.01,
        f"Deferred registry status is too slow: {registry_median:.6f}s",
    )
    check(
        navigation_median < 0.001,
        f"Transient Preferences navigation is too slow: {navigation_median:.6f}s",
    )
    check(
        addon._PREFERENCES_SECTION == "IMPORT",
        "Transient Preferences navigation did not select the requested section",
    )
    print(
        "PREFERENCES_PERFORMANCE_REGRESSION: PASS "
        f"texture_median={texture_median:.6f}s "
        f"texture_max={max(texture_times):.6f}s "
        f"registry_median={registry_median:.6f}s "
        f"registry_max={max(registry_times):.6f}s "
        f"navigation_median={navigation_median:.6f}s "
        f"navigation_max={max(navigation_times):.6f}s"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: blender --python preferences_performance_regression.py -- ROMFS"
        )

    run(Path(sys.argv[-1]).resolve())
