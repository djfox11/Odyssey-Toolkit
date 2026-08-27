from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .placement_classifier import ClassifiedPlacement
from .stage_data import StageScenario, Vector3


REPORT_SCHEMA_VERSION = 3


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _vector(value: Vector3) -> dict[str, float]:
    return {"x": value.x, "y": value.y, "z": value.z}


def _object_data_path(path: Path | None) -> str | None:
    if path is None:
        return None

    return (Path("ObjectData") / path.name).as_posix()


def _resolution_status(classified: ClassifiedPlacement) -> str:
    if classified.resource.has_model:
        return "MODEL"

    if classified.resource.has_archive:
        return "ARCHIVE_WITHOUT_MODEL"

    return "UNRESOLVED"


def _attempt_data(attempt: Any) -> dict[str, Any]:
    return {
        "source_field": attempt.source_field,
        "requested_name": attempt.requested_name,
        "status": attempt.status,
        "object_data_archive": _object_data_path(attempt.archive_path),
        "bfres_files": list(attempt.bfres_files),
    }


def _suggestion_data(suggestion: Any) -> dict[str, Any]:
    return {
        "object_data_archive": _object_data_path(suggestion.archive_path),
        "score": round(float(suggestion.score), 6),
        "reasons": list(suggestion.reasons),
        "inspected": suggestion.inspected,
        "bfres_files": list(suggestion.bfres_files),
        "init_model_references": list(suggestion.init_model_references),
        "sub_actor_model_names": list(suggestion.sub_actor_model_names),
        "inspection_error": suggestion.inspection_error,
    }


def _component_data(component: Any) -> dict[str, Any]:
    return {
        "source_field": component.source_field,
        "requested_name": component.requested_name,
        "object_data_archive": _object_data_path(component.archive_path),
        "bfres_files": list(component.bfres_files),
    }


def _actor_counts(
    classified_placements: list[ClassifiedPlacement],
    status: str,
) -> list[dict[str, Any]]:
    counts = Counter(
        classified.placement.unit_config_name
        for classified in classified_placements
        if _resolution_status(classified) == status
    )
    return [
        {"unit_config_name": name, "placement_count": count}
        for name, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
    ]


def _suggested_actors(
    classified_placements: list[ClassifiedPlacement],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[ClassifiedPlacement]] = {}

    for classified in classified_placements:
        if classified.resource.suggestions:
            grouped.setdefault(
                classified.placement.unit_config_name,
                [],
            ).append(classified)

    result: list[dict[str, Any]] = []

    for name, placements in grouped.items():
        first = placements[0]
        result.append(
            {
                "unit_config_name": name,
                "placement_count": len(placements),
                "suggested_archives": [
                    _suggestion_data(suggestion)
                    for suggestion in first.resource.suggestions
                ],
            }
        )

    result.sort(
        key=lambda item: (
            -item["placement_count"],
            item["unit_config_name"].casefold(),
        )
    )
    return result


def build_model_resolution_report(
    *,
    display_name: str,
    world_name: str,
    stage_scenario: StageScenario,
    classified_placements: list[ClassifiedPlacement],
) -> dict[str, Any]:
    statuses = [
        _resolution_status(classified)
        for classified in classified_placements
    ]
    model_archives = {
        component.archive_path.name
        for classified in classified_placements
        for component in classified.resource.model_resources
        if component.archive_path is not None
    }
    suggested_archives = {
        suggestion.archive_path.name
        for classified in classified_placements
        for suggestion in classified.resource.suggestions
    }
    placements: list[dict[str, Any]] = []

    for classified, status in zip(
        classified_placements,
        statuses,
        strict=True,
    ):
        placement = classified.placement
        resource = classified.resource
        parameter_name = str(
            placement.unit_config.get("ParameterConfigName") or ""
        ).strip()
        placements.append(
            {
                "id": placement.identifier,
                "unit_config_name": placement.unit_config_name,
                "model_name": placement.model_name,
                "parameter_config_name": parameter_name or None,
                "stage_layer": placement.stage_layer,
                "source_stage_name": placement.source_stage_name,
                "zone_path": list(placement.zone_path),
                "placement_category": placement.category,
                "import_category": classified.category.value,
                "placement_file_name": placement.placement_file_name,
                "layer_config_name": placement.layer_config_name,
                "is_root": placement.is_root,
                "is_link_destination": placement.is_link_destination,
                "transform": {
                    "translate": _vector(placement.translate),
                    "rotate_degrees": _vector(placement.rotate),
                    "scale": _vector(placement.scale),
                },
                "links": {
                    name: list(targets)
                    for name, targets in sorted(placement.links.items())
                },
                "resource": {
                    "status": status,
                    "source_field": resource.source_field,
                    "requested_name": resource.requested_name,
                    "object_data_archive": _object_data_path(
                        resource.archive_path
                    ),
                    "bfres_files": list(resource.bfres_files),
                    "components": [
                        _component_data(component)
                        for component in resource.model_resources
                    ],
                    "attempted_candidates": [
                        _attempt_data(attempt)
                        for attempt in resource.attempts
                    ],
                    "suggested_archives": [
                        _suggestion_data(suggestion)
                        for suggestion in resource.suggestions
                    ],
                },
            }
        )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kingdom": {
            "display_name": display_name,
            "world_name": world_name,
            "stage_name": stage_scenario.stage_name,
        },
        "scenario": stage_scenario.scenario_number,
        "missing_stage_layers": list(stage_scenario.missing_layers),
        "expanded_zones": list(stage_scenario.expanded_zones),
        "summary": {
            "total_placements": len(classified_placements),
            "expanded_zone_count": len(stage_scenario.expanded_zones),
            "by_source_stage": _counts(
                classified.placement.source_stage_name
                for classified in classified_placements
            ),
            "by_stage_layer": _counts(
                classified.placement.stage_layer
                for classified in classified_placements
            ),
            "by_import_category": _counts(
                classified.category.value
                for classified in classified_placements
            ),
            "by_resolution_status": _counts(statuses),
            "by_resolution_source": _counts(
                classified.resource.source_field or "NONE"
                for classified in classified_placements
            ),
            "by_attempt_status": _counts(
                attempt.status
                for classified in classified_placements
                for attempt in classified.resource.attempts
            ),
            "unique_model_archives": len(model_archives),
            "unique_suggested_archives": len(suggested_archives),
            "placements_with_suggestions": sum(
                bool(classified.resource.suggestions)
                for classified in classified_placements
            ),
            "multi_bfres_placements": sum(
                sum(
                    len(component.bfres_files)
                    for component in classified.resource.model_resources
                )
                > 1
                for classified in classified_placements
            ),
            "composite_model_placements": sum(
                len(classified.resource.model_resources) > 1
                for classified in classified_placements
            ),
        },
        "unresolved_actors": _actor_counts(
            classified_placements,
            "UNRESOLVED",
        ),
        "archive_without_model_actors": _actor_counts(
            classified_placements,
            "ARCHIVE_WITHOUT_MODEL",
        ),
        "suggested_actors": _suggested_actors(classified_placements),
        "placements": placements,
    }


def model_resolution_report_json(report: dict[str, Any]) -> str:
    return json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ) + "\n"