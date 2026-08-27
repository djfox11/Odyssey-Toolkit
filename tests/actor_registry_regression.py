from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.actor_registry import (
    ActorRegistryBuilder,
    StageArchiveTask,
    cached_registry_file_status,
    configure_actor_registry,
    configured_actor_registry,
    load_actor_registry,
    registry_file_status,
    save_actor_registry,
)
from smo_kingdom_importer.object_data import (
    ObjectDataIndex,
    clear_object_data_index_cache,
)
from smo_kingdom_importer.registry_report import build_registry_report


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    task = StageArchiveTask(
        archive_path=(
            romfs_root / "StageData" / "CityWorldHomeStageMap.szs"
        ),
        stage_name="CityWorldHomeStage",
        stage_layer="Map",
    )
    check(task.archive_path.is_file(), f"Missing registry fixture: {task.archive_path}")
    builder = ActorRegistryBuilder(romfs_root, tasks=(task,))
    check(builder.total_count == 1, "Focused registry build queued extra archives")
    builder.process_next()
    registry = builder.finish()

    check(not registry.build_errors, f"Registry build errors: {registry.build_errors}")
    check(registry.archives_scanned == 1, "Registry archive count is incorrect")
    check(registry.scenario_count == 15, "Registry did not inspect all 15 scenarios")
    check(registry.placement_count > 20_000, "Registry missed nested StageData records")
    check(
        registry.actor_signature_count > 400,
        "Registry actor coverage is unexpectedly low",
    )
    check(
        registry.candidate("CityWorldChairAFixParts", "FixMapParts")
        == ("ActorRegistryParameterModel", "CityWorldChairA"),
        "Observed Metro actor mapping was not recovered",
    )

    context_actor = next(
        actor
        for actor in registry.actors
        if actor.modelless_occurrence_count
    )
    check(
        context_actor.modelless_source_stages
        and context_actor.modelless_stage_layers
        and context_actor.modelless_categories,
        "Modelless registry evidence did not retain StageData context",
    )

    ambiguous = next(
        actor for actor in registry.actors if len(actor.models) > 1
    )
    check(
        registry.candidate(
            ambiguous.unit_config_name,
            ambiguous.parameter_config_name,
        )
        is None,
        "Ambiguous registry evidence was treated as an automatic mapping",
    )

    with tempfile.TemporaryDirectory() as temporary_directory:
        cache_directory = Path(temporary_directory)
        path = save_actor_registry(registry, romfs_root, cache_directory)
        check(path.is_file(), "Registry JSON was not saved")
        loaded = load_actor_registry(romfs_root, cache_directory)
        check(
            loaded.candidate("CityWorldChairAFixParts", "FixMapParts")
            == ("ActorRegistryParameterModel", "CityWorldChairA"),
            "Registry mapping changed after JSON round trip",
        )

        report = build_registry_report(loaded, romfs_root, cache_directory)
        expected_report_rows = sum(
            bool(actor.modelless_occurrence_count)
            for actor in loaded.actors
        )
        check(
            report.record_count == expected_report_rows,
            "Registry report omitted modelless actor signatures",
        )
        check(
            sum(report.counts.values()) == report.record_count,
            "Registry report group totals do not match its rows",
        )
        check(report.path.is_file(), "Registry report CSV was not written")
        check(
            report.summary_path.is_file(),
            "Resolution coverage summary JSON was not written",
        )
        report_header = report.path.read_text(
            encoding="utf-8-sig"
        ).splitlines()[0]
        check(
            "unit_config_name" in report_header
            and "evidence" in report_header,
            "Registry report CSV is missing diagnostic columns",
        )
        coverage_summary = json.loads(
            report.summary_path.read_text(encoding="utf-8")
        )
        coverage = coverage_summary["coverage"]
        expected_occurrences = sum(
            actor.modelless_occurrence_count for actor in loaded.actors
        )
        check(
            coverage["modelless_occurrences"] == expected_occurrences,
            "Resolution coverage lost modelless placement occurrences",
        )
        check(
            sum(report.occurrence_counts.values()) == expected_occurrences,
            "Registry occurrence totals do not match actor evidence",
        )
        check(
            coverage["actionable_signatures"]
            == report.actionable_signature_count
            and coverage["actionable_occurrences"]
            == report.actionable_occurrence_count,
            "Concise actionable totals disagree with the report result",
        )
        check(
            isinstance(coverage_summary["actionable_queue"], list)
            and isinstance(coverage_summary["top_runtime_visuals"], list)
            and isinstance(coverage_summary["top_unknowns"], list),
            "Resolution coverage summary omitted a diagnostic queue",
        )

        unchecked_status = cached_registry_file_status(
            romfs_root,
            cache_directory,
        )
        check(
            unchecked_status.exists and not unchecked_status.valid,
            "Display status unexpectedly performed full registry validation",
        )
        first_status = registry_file_status(romfs_root, cache_directory)
        second_status = registry_file_status(romfs_root, cache_directory)
        check(first_status.valid, f"Saved registry status is invalid: {first_status.message}")
        check(
            first_status is second_status,
            "Preferences status did not reuse its validated registry cache",
        )
        configure_actor_registry(cache_directory, enabled=True)
        displayed_status = cached_registry_file_status(
            romfs_root,
            cache_directory,
        )
        check(
            displayed_status is first_status,
            "Display status did not reuse the validated registry cache",
        )
        check(
            configured_actor_registry(romfs_root) is first_status.registry,
            "Import resolver did not reuse the Preferences-validated registry",
        )
        clear_object_data_index_cache()

        try:
            placement = SimpleNamespace(
                unit_config_name="CityWorldChairAFixParts",
                model_name=None,
                source_stage_name="CityWorldHomeStage",
                unit_config={"ParameterConfigName": "FixMapParts"},
                raw={},
            )
            resource = ObjectDataIndex(romfs_root).resolve(placement)
            check(resource.has_model, "Registry mapping did not resolve a BFRES")
            check(
                resource.source_field == "ActorRegistryParameterModel",
                f"Unexpected registry resolution source: {resource.source_field}",
            )
            check(
                resource.archive_path is not None
                and resource.archive_path.name == "CityWorldChairA.szs",
                f"Unexpected registry archive: {resource.archive_path}",
            )
        finally:
            configure_actor_registry(None, enabled=False)
            clear_object_data_index_cache()

    print(
        "ACTOR_REGISTRY_REGRESSION: PASS "
        f"scenarios={registry.scenario_count} "
        f"placements={registry.placement_count} "
        f"signatures={registry.actor_signature_count} "
        f"unambiguous={registry.unambiguous_signature_count} "
        f"report_groups={report.counts}"
    )


def run_full_benchmark(romfs_root: Path) -> None:
    builder = ActorRegistryBuilder(romfs_root)
    started = time.perf_counter()

    while not builder.complete:
        builder.process_next()

    registry = builder.finish()
    build_seconds = time.perf_counter() - started
    check(not registry.build_errors, f"Full registry errors: {registry.build_errors}")

    with tempfile.TemporaryDirectory() as temporary_directory:
        cache_directory = Path(temporary_directory)
        save_started = time.perf_counter()
        registry_path = save_actor_registry(
            registry,
            romfs_root,
            cache_directory,
        )
        save_seconds = time.perf_counter() - save_started
        registry_size = registry_path.stat().st_size
        load_started = time.perf_counter()
        loaded = load_actor_registry(romfs_root, cache_directory)
        load_seconds = time.perf_counter() - load_started
        check(
            loaded.actor_signature_count == registry.actor_signature_count,
            "Full registry changed after persistence",
        )
        report_started = time.perf_counter()
        report = build_registry_report(
            registry,
            romfs_root,
            cache_directory,
        )
        report_seconds = time.perf_counter() - report_started

    print(
        "ACTOR_REGISTRY_FULL_BENCHMARK: PASS "
        f"build={build_seconds:.3f}s "
        f"archives={registry.archives_scanned} "
        f"scenarios={registry.scenario_count} "
        f"placements={registry.placement_count} "
        f"signatures={registry.actor_signature_count} "
        f"modelless_rows={report.record_count} "
        f"registry_bytes={registry_size} "
        f"save={save_seconds:.3f}s "
        f"load={load_seconds:.3f}s "
        f"report={report_seconds:.3f}s "
        f"groups={report.counts}"
    )
if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) not in {1, 2} or (
        len(arguments) == 2 and arguments[1] != "--full"
    ):
        raise SystemExit(
            "Usage: actor_registry_regression.py -- ROMFS [--full]"
        )

    romfs_root = Path(arguments[0]).resolve()

    if len(arguments) == 2:
        run_full_benchmark(romfs_root)
    else:
        run(romfs_root)
