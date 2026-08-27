from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .actor_registry import ActorRegistry, RegistryActorEvidence, romfs_key
from .model_expectation import ModelExpectation, assess_model_expectation
from .object_data import ObjectArchiveMetadata, ObjectDataIndex
from .resource_rules import report_resource_rule_candidates


SAFE_AUTOMATIC_MAPPING = "SAFE_AUTOMATIC_MAPPING"
DIRECT_OBJECTDATA_MODEL = "DIRECT_OBJECTDATA_MODEL"
AMBIGUOUS_MAPPING = "AMBIGUOUS_MAPPING"
CURATED_RESOURCE_MAPPING = "CURATED_RESOURCE_MAPPING"
COMPOSITE_RESOURCE_MAPPING = "COMPOSITE_RESOURCE_MAPPING"


@dataclass(slots=True, frozen=True)
class RegistryReportRecord:
    group: str
    confidence: str
    unit_config_name: str
    parameter_config_name: str
    candidate_models: tuple[str, ...]
    modelless_occurrence_count: int
    source_stages: tuple[str, ...]
    stage_layers: tuple[str, ...]
    placement_categories: tuple[str, ...]
    object_archive: str
    bfres_files: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class RegistryReportResult:
    path: Path
    summary_path: Path
    record_count: int
    counts: dict[str, int]
    occurrence_counts: dict[str, int]
    actionable_signature_count: int
    actionable_occurrence_count: int


def registry_report_path(romfs_path: Path, cache_directory: Path) -> Path:
    return cache_directory / f"actor_registry_report_{romfs_key(romfs_path)}.csv"


def registry_report_summary_path(
    romfs_path: Path,
    cache_directory: Path,
) -> Path:
    return cache_directory / (
        f"resolution_coverage_{romfs_key(romfs_path)}.json"
    )


def _metadata_for_names(
    object_data: ObjectDataIndex,
    names: tuple[str, ...],
) -> tuple[ObjectArchiveMetadata | None, str, tuple[str, ...]]:
    errors: list[str] = []
    seen: set[str] = set()
    first_metadata: ObjectArchiveMetadata | None = None
    first_metadata_name = ""

    for name in names:
        folded = name.strip().casefold()

        if not folded or folded in seen:
            continue

        seen.add(folded)

        try:
            metadata = object_data.archive_metadata(name)
        except Exception as exc:
            errors.append(f"could not inspect ObjectData {name}: {exc}")
            continue

        if metadata is None:
            continue

        if metadata.bfres_files:
            return metadata, name, tuple(errors)

        if first_metadata is None:
            first_metadata = metadata
            first_metadata_name = name

    return first_metadata, first_metadata_name, tuple(errors)


def _record_for_actor(
    registry: ActorRegistry,
    actor: RegistryActorEvidence,
    object_data: ObjectDataIndex,
) -> RegistryReportRecord:
    signature_models = registry.models_for_signature(
        actor.unit_config_name,
        actor.parameter_config_name,
    )
    unit_models = registry.models_for_unit(actor.unit_config_name)
    registry_candidate = registry.candidate(
        actor.unit_config_name,
        actor.parameter_config_name,
    )
    common_arguments: dict[str, Any] = {
        "unit_config_name": actor.unit_config_name,
        "parameter_config_name": actor.parameter_config_name,
        "modelless_occurrence_count": actor.modelless_occurrence_count,
        "source_stages": actor.modelless_source_stages,
        "stage_layers": actor.modelless_stage_layers,
        "placement_categories": actor.modelless_categories,
    }

    def make_record(
        group: str,
        confidence: str,
        candidate_models: tuple[str, ...],
        metadata_items: tuple[ObjectArchiveMetadata, ...],
        evidence: tuple[str, ...],
    ) -> RegistryReportRecord:
        return RegistryReportRecord(
            group=group,
            confidence=confidence,
            candidate_models=candidate_models,
            object_archive=" | ".join(
                metadata.archive_path.name for metadata in metadata_items
            ),
            bfres_files=tuple(
                f"{metadata.archive_path.name}:{bfres_name}"
                for metadata in metadata_items
                for bfres_name in metadata.bfres_files
            ),
            evidence=evidence,
            **common_arguments,
        )

    direct_metadata, direct_name, direct_errors = _metadata_for_names(
        object_data,
        (actor.unit_config_name, actor.parameter_config_name),
    )

    if direct_metadata is not None and direct_metadata.bfres_files:
        return make_record(
            DIRECT_OBJECTDATA_MODEL,
            "HIGH",
            (direct_name,),
            (direct_metadata,),
            (
                "UnitConfigName or ParameterConfigName directly matches an "
                "ObjectData archive containing BFRES",
                *direct_errors,
            ),
        )

    curated_rules = report_resource_rule_candidates(
        actor.unit_config_name,
        actor.modelless_source_stages,
    )
    curated_names = tuple(dict.fromkeys(name for _, name in curated_rules))
    curated_metadata: list[ObjectArchiveMetadata] = []
    curated_errors: list[str] = []

    for name in curated_names:
        metadata, _, errors = _metadata_for_names(object_data, (name,))
        curated_errors.extend(errors)

        if metadata is not None:
            curated_metadata.append(metadata)

    curated_complete = (
        bool(curated_names)
        and len(curated_metadata) == len(curated_names)
        and all(metadata.bfres_files for metadata in curated_metadata)
    )

    if curated_complete:
        is_composite = any(
            source_field == "CompositeActorResource"
            for source_field, _ in curated_rules
        )
        return make_record(
            (
                COMPOSITE_RESOURCE_MAPPING
                if is_composite
                else CURATED_RESOURCE_MAPPING
            ),
            "HIGH",
            curated_names,
            tuple(curated_metadata),
            (
                "validated curated actor resource rule",
                *(
                    ("actor uses multiple ObjectData archives",)
                    if is_composite
                    else ()
                ),
                *curated_errors,
            ),
        )

    registry_models = (
        (registry_candidate[1],)
        if registry_candidate is not None
        else signature_models or unit_models
    )
    registry_metadata, registry_name, registry_errors = _metadata_for_names(
        object_data,
        registry_models,
    )
    registry_archive_matched = (
        registry_candidate is not None
        and registry_name.casefold() == registry_candidate[1].casefold()
    )

    if (
        registry_archive_matched
        and registry_metadata is not None
        and registry_metadata.bfres_files
    ):
        return make_record(
            SAFE_AUTOMATIC_MAPPING,
            "HIGH",
            registry_models,
            (registry_metadata,),
            (
                f"{registry_candidate[0]} selects {registry_candidate[1]}",
                "candidate ObjectData archive contains BFRES",
                *registry_errors,
            ),
        )

    if registry_candidate is None and len(registry_models) > 1:
        return make_record(
            AMBIGUOUS_MAPPING,
            "HIGH",
            registry_models,
            ((registry_metadata,) if registry_metadata is not None else ()),
            (
                "observed ModelName evidence contains multiple candidates",
                *registry_errors,
            ),
        )

    assessment_metadata = (
        direct_metadata
        or (curated_metadata[0] if curated_metadata else None)
        or registry_metadata
    )
    candidate_models = curated_names or registry_models
    assessment = assess_model_expectation(
        unit_config_name=actor.unit_config_name,
        parameter_config_name=actor.parameter_config_name,
        stage_layers=actor.modelless_stage_layers,
        placement_categories=actor.modelless_categories,
        archive_metadata=assessment_metadata,
    )
    evidence = list(assessment.reasons)

    if curated_names:
        evidence.insert(
            0,
            "curated resource rule is incomplete or contains no importable BFRES",
        )

    if registry_candidate is not None:
        evidence.insert(
            0,
            f"observed model candidate {registry_candidate[1]} is not importable",
        )

    if assessment_metadata is not None and not assessment_metadata.bfres_files:
        evidence.append("matching ObjectData archive contains no BFRES")

    evidence.extend(direct_errors)
    evidence.extend(curated_errors)
    evidence.extend(registry_errors)
    return make_record(
        assessment.expectation.value,
        assessment.confidence,
        candidate_models,
        ((assessment_metadata,) if assessment_metadata is not None else ()),
        tuple(dict.fromkeys(evidence)),
    )

def _summary_record(record: RegistryReportRecord) -> dict[str, Any]:
    return {
        "group": record.group,
        "confidence": record.confidence,
        "unit_config_name": record.unit_config_name,
        "parameter_config_name": record.parameter_config_name,
        "modelless_occurrences": record.modelless_occurrence_count,
        "candidate_models": list(record.candidate_models),
        "source_stages": list(record.source_stages),
        "placement_categories": list(record.placement_categories),
        "object_archive": record.object_archive,
        "bfres_files": list(record.bfres_files),
        "evidence": list(record.evidence),
    }


def _write_registry_report(
    records: tuple[RegistryReportRecord, ...],
    romfs_path: Path,
    cache_directory: Path,
) -> RegistryReportResult:
    group_order = {
        ModelExpectation.MODEL_EXPECTED.value: 0,
        AMBIGUOUS_MAPPING: 1,
        COMPOSITE_RESOURCE_MAPPING: 2,
        CURATED_RESOURCE_MAPPING: 3,
        ModelExpectation.RUNTIME_VISUAL.value: 4,
        ModelExpectation.UNKNOWN.value: 5,
        ModelExpectation.CONFIRMED_NON_MESH.value: 6,
        SAFE_AUTOMATIC_MAPPING: 7,
        DIRECT_OBJECTDATA_MODEL: 8,
    }
    records = tuple(
        sorted(
            records,
            key=lambda record: (
                group_order.get(record.group, 99),
                -record.modelless_occurrence_count,
                record.unit_config_name.casefold(),
                record.parameter_config_name.casefold(),
            ),
        )
    )
    counts = Counter(record.group for record in records)
    occurrence_counts = Counter()

    for record in records:
        occurrence_counts[record.group] += record.modelless_occurrence_count

    cache_directory.mkdir(parents=True, exist_ok=True)
    path = registry_report_path(romfs_path, cache_directory)
    temporary_path = path.with_name(path.name + ".tmp")

    try:
        with temporary_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    "group",
                    "confidence",
                    "unit_config_name",
                    "parameter_config_name",
                    "candidate_models",
                    "modelless_occurrences",
                    "source_stages",
                    "stage_layers",
                    "placement_categories",
                    "object_archive",
                    "bfres_files",
                    "evidence",
                )
            )

            for record in records:
                writer.writerow(
                    (
                        record.group,
                        record.confidence,
                        record.unit_config_name,
                        record.parameter_config_name,
                        " | ".join(record.candidate_models),
                        record.modelless_occurrence_count,
                        " | ".join(record.source_stages),
                        " | ".join(record.stage_layers),
                        " | ".join(record.placement_categories),
                        record.object_archive,
                        " | ".join(record.bfres_files),
                        " | ".join(record.evidence),
                    )
                )

        temporary_path.replace(path)
    finally:
        if temporary_path.is_file():
            temporary_path.unlink()

    actionable_groups = frozenset(
        {
            ModelExpectation.MODEL_EXPECTED.value,
            AMBIGUOUS_MAPPING,
        }
    )
    resolved_groups = frozenset(
        {
            COMPOSITE_RESOURCE_MAPPING,
            CURATED_RESOURCE_MAPPING,
            DIRECT_OBJECTDATA_MODEL,
            SAFE_AUTOMATIC_MAPPING,
        }
    )
    actionable = tuple(
        record for record in records if record.group in actionable_groups
    )
    resolved = tuple(
        record for record in records if record.group in resolved_groups
    )
    runtime = tuple(
        record
        for record in records
        if record.group == ModelExpectation.RUNTIME_VISUAL.value
    )
    unknown = tuple(
        record
        for record in records
        if record.group == ModelExpectation.UNKNOWN.value
    )
    non_mesh = tuple(
        record
        for record in records
        if record.group == ModelExpectation.CONFIRMED_NON_MESH.value
    )
    summary = {
        "schema_version": 1,
        "romfs_key": romfs_key(romfs_path),
        "coverage": {
            "modelless_signatures": len(records),
            "modelless_occurrences": sum(
                record.modelless_occurrence_count for record in records
            ),
            "resolved_signatures": len(resolved),
            "resolved_occurrences": sum(
                record.modelless_occurrence_count for record in resolved
            ),
            "actionable_signatures": len(actionable),
            "actionable_occurrences": sum(
                record.modelless_occurrence_count for record in actionable
            ),
            "runtime_visual_signatures": len(runtime),
            "runtime_visual_occurrences": sum(
                record.modelless_occurrence_count for record in runtime
            ),
            "confirmed_non_mesh_signatures": len(non_mesh),
            "confirmed_non_mesh_occurrences": sum(
                record.modelless_occurrence_count for record in non_mesh
            ),
            "unknown_signatures": len(unknown),
            "unknown_occurrences": sum(
                record.modelless_occurrence_count for record in unknown
            ),
        },
        "groups": {
            group: {
                "signatures": counts[group],
                "occurrences": occurrence_counts[group],
            }
            for group in sorted(counts)
        },
        "actionable_queue": [
            _summary_record(record) for record in actionable[:100]
        ],
        "top_runtime_visuals": [
            _summary_record(record) for record in runtime[:50]
        ],
        "top_unknowns": [
            _summary_record(record) for record in unknown[:50]
        ],
    }
    summary_path = registry_report_summary_path(
        romfs_path,
        cache_directory,
    )
    temporary_summary_path = summary_path.with_name(
        summary_path.name + ".tmp"
    )

    try:
        temporary_summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_summary_path.replace(summary_path)
    finally:
        if temporary_summary_path.is_file():
            temporary_summary_path.unlink()

    return RegistryReportResult(
        path=path,
        summary_path=summary_path,
        record_count=len(records),
        counts=dict(sorted(counts.items())),
        occurrence_counts=dict(sorted(occurrence_counts.items())),
        actionable_signature_count=len(actionable),
        actionable_occurrence_count=sum(
            record.modelless_occurrence_count for record in actionable
        ),
    )

class RegistryReportBuilder:
    def __init__(
        self,
        registry: ActorRegistry,
        romfs_path: Path,
        cache_directory: Path,
    ) -> None:
        self.registry = registry
        self.romfs_path = romfs_path.resolve()
        self.cache_directory = cache_directory.resolve()
        self.actors = tuple(
            actor
            for actor in registry.actors
            if actor.modelless_occurrence_count
        )
        self.object_data = ObjectDataIndex(self.romfs_path)
        self.current_index = 0
        self.records: list[RegistryReportRecord] = []

    @property
    def total_count(self) -> int:
        return len(self.actors)

    @property
    def complete(self) -> bool:
        return self.current_index >= self.total_count

    @property
    def current_label(self) -> str:
        if self.complete:
            return "Finalising registry report"

        return self.actors[self.current_index].unit_config_name

    def process_next(self) -> bool:
        if self.complete:
            return False

        actor = self.actors[self.current_index]
        self.records.append(
            _record_for_actor(self.registry, actor, self.object_data)
        )
        self.current_index += 1
        return not self.complete

    def finish(self) -> RegistryReportResult:
        if not self.complete:
            raise RuntimeError(
                "Registry report has not processed every modelless signature."
            )

        return _write_registry_report(
            tuple(self.records),
            self.romfs_path,
            self.cache_directory,
        )


def build_registry_report(
    registry: ActorRegistry,
    romfs_path: Path,
    cache_directory: Path,
) -> RegistryReportResult:
    builder = RegistryReportBuilder(registry, romfs_path, cache_directory)

    while not builder.complete:
        builder.process_next()

    return builder.finish()
