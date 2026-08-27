from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


TAXONOMY = {
    "version": 1,
    "categories": [
        {
            "id": "ENVIRONMENT",
            "label": "Environment",
            "subcategories": [
                ["STAGE_GEOMETRY", "Stage Geometry"],
                ["PROPS_DECORATION", "Props & Decoration"],
                ["NATURE_VEGETATION", "Nature & Vegetation"],
                ["SKY_ATMOSPHERE", "Sky & Atmosphere"],
                ["WATER_FLUIDS", "Water, Fluids & Procedural Surfaces"],
            ],
        },
        {
            "id": "CHARACTERS",
            "label": "Characters",
            "subcategories": [
                ["NPCS", "NPCs"],
                ["FRIENDLY_CHARACTERS", "Friendly Characters"],
                ["AMBIENT_CREATURES", "Ambient Creatures"],
            ],
        },
        {
            "id": "ENEMIES",
            "label": "Enemies",
            "subcategories": [
                ["STANDARD_ENEMIES", "Standard Enemies"],
                ["BOSSES", "Bosses"],
                ["ENEMY_PARTS", "Enemy Projectiles & Parts"],
            ],
        },
        {
            "id": "GAMEPLAY",
            "label": "Gameplay Objects",
            "subcategories": [
                ["INTERACTIVE_OBJECTS", "Interactive Objects"],
                ["PLATFORMS_MECHANISMS", "Platforms & Mechanisms"],
                ["DOORS_PIPES_WARPS", "Doors, Pipes & Warps"],
                ["HAZARDS", "Hazards"],
                ["VEHICLES", "Vehicles"],
            ],
        },
        {
            "id": "COLLECTIBLES",
            "label": "Collectibles",
            "subcategories": [
                ["COINS_CURRENCY", "Coins & Currency"],
                ["MOONS_OBJECTIVES", "Moons & Objectives"],
                ["KEYS", "Keys"],
                ["POWERUPS", "Power-ups"],
            ],
        },
        {
            "id": "EFFECTS",
            "label": "Effects",
            "subcategories": [
                ["PARTICLES_VFX", "Particles & VFX"],
                ["RUNTIME_VISUALS", "Runtime Visual Actors"],
                ["PROJECTED_EFFECTS", "Projected Effects"],
            ],
        },
        {
            "id": "LIGHTING",
            "label": "Lighting",
            "subcategories": [
                ["LOCAL_LIGHTS", "Point, Spot & Line Lights"],
                ["LIGHT_CONTROLLERS", "Light Controllers"],
            ],
        },
        {
            "id": "AUDIO",
            "label": "Audio",
            "subcategories": [
                ["AUDIO_EMITTERS", "Audio Emitters"],
                ["AUDIO_CONTROLLERS", "Audio Controllers"],
            ],
        },
        {
            "id": "TECHNICAL",
            "label": "Technical",
            "subcategories": [
                ["AREAS_VOLUMES", "Areas & Volumes"],
                ["CAMERAS", "Cameras"],
                ["LOGIC_CONTROLLERS", "Logic & Controllers"],
                ["PLAYER_SPAWNS", "Player & Spawn Markers"],
                ["RAILS_PATHS", "Rails & Paths"],
                ["DEBUG_TEST", "Debug & Test"],
            ],
        },
        {
            "id": "UNCLASSIFIED",
            "label": "Unclassified",
            "subcategories": [
                ["EXPECTED_VISUAL", "Expected Visual"],
                ["UNKNOWN_MODELLESS", "Unknown Modelless"],
                ["NEEDS_REVIEW", "Needs Review"],
            ],
        },
    ],
    "representations": [
        ["STATIC_MODEL", "Static Model"],
        ["COMPOSITE_MODEL", "Composite Model"],
        ["RUNTIME_VISUAL", "Runtime Visual"],
        ["NON_VISUAL_CONTROLLER", "Non-visual Controller"],
        ["VOLUME_OR_HELPER", "Volume or Helper"],
        ["UNRESOLVED_VISUAL", "Unresolved Visual"],
        ["UNKNOWN", "Unknown"],
    ],
}


def _folded_names(*names: str) -> str:
    return " ".join(name.casefold() for name in names if name)


def _subcategory(category: str, names: str) -> str:
    if category == "ENVIRONMENT":
        if any(token in names for token in ("sky", "cloud", "atmosphere")):
            return "SKY_ATMOSPHERE"
        if any(
            token in names
            for token in ("ocean", "water", "lava", "poison", "fluid", "ripple")
        ):
            return "WATER_FLUIDS"
        if any(
            token in names
            for token in ("tree", "plant", "flower", "grass", "forest", "leaf")
        ):
            return "NATURE_VEGETATION"
        if any(
            token in names
            for token in ("ground", "stage", "wall", "floor", "road", "building")
        ):
            return "STAGE_GEOMETRY"
        return "PROPS_DECORATION"

    if category == "CHARACTERS":
        if any(token in names for token in ("bird", "fish", "animal", "frog")):
            return "AMBIENT_CREATURES"
        if "npc" in names:
            return "NPCS"
        return "FRIENDLY_CHARACTERS"

    if category == "ENEMIES":
        if "boss" in names:
            return "BOSSES"
        if any(token in names for token in ("bullet", "projectile", "weapon", "parts")):
            return "ENEMY_PARTS"
        return "STANDARD_ENEMIES"

    if category == "GAMEPLAY":
        if any(token in names for token in ("dokan", "door", "warp", "portal")):
            return "DOORS_PIPES_WARPS"
        if any(token in names for token in ("lift", "platform", "move", "rail")):
            return "PLATFORMS_MECHANISMS"
        if any(token in names for token in ("damage", "hazard", "killer", "thorn")):
            return "HAZARDS"
        if any(token in names for token in ("car", "bike", "motor", "vehicle")):
            return "VEHICLES"
        return "INTERACTIVE_OBJECTS"

    if category == "COLLECTIBLES":
        if "coin" in names:
            return "COINS_CURRENCY"
        if any(token in names for token in ("shine", "moon", "goal")):
            return "MOONS_OBJECTIVES"
        if "key" in names:
            return "KEYS"
        return "POWERUPS"

    if category == "EFFECTS":
        if any(token in names for token in ("project", "shadow", "decal")):
            return "PROJECTED_EFFECTS"
        if any(token in names for token in ("effect", "particle", "emitter")):
            return "PARTICLES_VFX"
        return "RUNTIME_VISUALS"

    if category == "LIGHTING":
        return "LOCAL_LIGHTS" if "light" in names else "LIGHT_CONTROLLERS"

    if category == "AUDIO":
        return "AUDIO_EMITTERS" if "seplay" in names else "AUDIO_CONTROLLERS"

    if category == "TECHNICAL":
        if "camera" in names:
            return "CAMERAS"
        if any(token in names for token in ("area", "volume", "range")):
            return "AREAS_VOLUMES"
        if any(token in names for token in ("player", "startpos", "spawn")):
            return "PLAYER_SPAWNS"
        if any(token in names for token in ("rail", "path", "route")):
            return "RAILS_PATHS"
        if any(token in names for token in ("debug", "test")):
            return "DEBUG_TEST"
        return "LOGIC_CONTROLLERS"

    return "NEEDS_REVIEW"


def _suggest_actor(actor: Any) -> tuple[str, str, str]:
    from smo_kingdom_importer.model_expectation import (
        ModelExpectation,
        assess_model_expectation,
    )
    from smo_kingdom_importer.object_data import ObjectResource
    from smo_kingdom_importer.placement_classifier import classify_placement

    names = _folded_names(
        actor.unit_config_name,
        actor.parameter_config_name,
        *(model.model_name for model in actor.models),
    )

    if actor.unit_config_name == "CloudOcean":
        return "ENVIRONMENT", "SKY_ATMOSPHERE", "RUNTIME_VISUAL"
    if actor.unit_config_name == "OceanWave":
        return "ENVIRONMENT", "WATER_FLUIDS", "RUNTIME_VISUAL"

    resource = ObjectResource(
        source_field=None,
        requested_name=None,
        archive_path=None,
        bfres_files=("Observed.bfres",) if actor.models else (),
    )
    placement = SimpleNamespace(
        unit_config_name=actor.unit_config_name,
        model_name=None,
        unit_config={"ParameterConfigName": actor.parameter_config_name},
        stage_layer=(actor.modelless_stage_layers or ("Map",))[0],
        category=(actor.modelless_categories or ("ObjectList",))[0],
    )
    current = classify_placement(placement, resource).value
    category_map = {
        "ENVIRONMENT": "ENVIRONMENT",
        "CHARACTERS": "CHARACTERS",
        "GAMEPLAY": "GAMEPLAY",
        "COLLECTIBLES": "COLLECTIBLES",
        "EFFECTS": "EFFECTS",
        "AUDIO": "AUDIO",
        "AREAS": "TECHNICAL",
        "CAMERAS": "TECHNICAL",
        "HELPERS": "TECHNICAL",
        "DEBUG": "TECHNICAL",
        "UNKNOWN_MODEL": "UNCLASSIFIED",
        "UNKNOWN_MODELLESS": "UNCLASSIFIED",
    }
    category = category_map[current]

    if any(token in names for token in ("enemy", "boss", "kuribo", "killer", "wanwan")):
        category = "ENEMIES"
    elif any(token in names for token in ("prepasspointlight", "prepassspotlight", "prepasslinelight")):
        category = "LIGHTING"

    subcategory = _subcategory(category, names)
    assessment = assess_model_expectation(
        unit_config_name=actor.unit_config_name,
        parameter_config_name=actor.parameter_config_name,
        stage_layers=actor.modelless_stage_layers,
        placement_categories=actor.modelless_categories,
        import_category=current,
        resource=resource,
    )

    if actor.models:
        representation = "STATIC_MODEL"
    elif category == "TECHNICAL" and subcategory in {"AREAS_VOLUMES", "CAMERAS"}:
        representation = "VOLUME_OR_HELPER"
    elif assessment.expectation == ModelExpectation.CONFIRMED_NON_MESH:
        representation = "NON_VISUAL_CONTROLLER"
    elif assessment.expectation == ModelExpectation.RUNTIME_VISUAL:
        representation = "RUNTIME_VISUAL"
    elif assessment.expectation == ModelExpectation.MODEL_EXPECTED:
        representation = "UNRESOLVED_VISUAL"
    else:
        representation = "UNKNOWN"

    return category, subcategory, representation


def _suggest_archive(name: str, has_bfres: bool) -> tuple[str, str, str]:
    names = _folded_names(name)
    category = "UNCLASSIFIED"

    if any(token in names for token in ("sky", "cloud")):
        category = "ENVIRONMENT"
    elif any(token in names for token in ("ground", "building", "tree", "plant", "road")):
        category = "ENVIRONMENT"
    elif any(token in names for token in ("enemy", "boss", "kuribo", "killer", "wanwan")):
        category = "ENEMIES"
    elif any(token in names for token in ("npc", "peach", "pauline", "yoshi")):
        category = "CHARACTERS"
    elif any(token in names for token in ("coin", "shine", "key")):
        category = "COLLECTIBLES"
    elif any(token in names for token in ("dokan", "lift", "switch", "vehicle", "bike", "car")):
        category = "GAMEPLAY"
    elif any(token in names for token in ("effect", "particle", "emitter")):
        category = "EFFECTS"
    elif "light" in names:
        category = "LIGHTING"
    elif any(token in names for token in ("camera", "area", "watcher", "controller", "director")):
        category = "TECHNICAL"

    return (
        category,
        _subcategory(category, names),
        "STATIC_MODEL" if has_bfres else "UNKNOWN",
    )


def _actor_record_id(unit_config_name: str, parameter_config_name: str) -> str:
    signature = unit_config_name + chr(0) + parameter_config_name
    return "actor:" + hashlib.sha1(
        signature.encode("utf-8")
    ).hexdigest()[:16]


def build_dataset(romfs_root: Path, registry_path: Path) -> dict[str, Any]:
    from smo_kingdom_importer.actor_registry import actor_registry_from_data
    from smo_kingdom_importer.object_data import ObjectDataIndex

    registry = actor_registry_from_data(
        json.loads(registry_path.read_text(encoding="utf-8"))
    )
    object_data = ObjectDataIndex(romfs_root)
    contexts: list[tuple[Any, Any, int, str]] = []

    for actor in registry.actors:
        parameter_name = actor.parameter_config_name
        fallback_layer = (
            actor.modelless_stage_layers[0]
            if actor.modelless_stage_layers
            else "Map"
        )
        fallback_category = (
            actor.modelless_categories[0]
            if actor.modelless_categories
            else "ObjectList"
        )

        for model in actor.models:
            source_stages = model.source_stages or ("",)

            for source_stage in source_stages:
                placement = SimpleNamespace(
                    unit_config_name=actor.unit_config_name,
                    model_name=model.model_name,
                    unit_config={"ParameterConfigName": parameter_name},
                    source_stage_name=source_stage,
                    raw={},
                    stage_layer=fallback_layer,
                    category=fallback_category,
                )
                contexts.append(
                    (
                        placement,
                        actor,
                        model.occurrence_count,
                        f"model:{model.model_name}",
                    )
                )

        if actor.modelless_occurrence_count:
            source_stages = actor.modelless_source_stages or ("",)

            for source_stage in source_stages:
                placement = SimpleNamespace(
                    unit_config_name=actor.unit_config_name,
                    model_name=None,
                    unit_config={"ParameterConfigName": parameter_name},
                    source_stage_name=source_stage,
                    raw={},
                    stage_layer=fallback_layer,
                    category=fallback_category,
                )
                contexts.append(
                    (
                        placement,
                        actor,
                        actor.modelless_occurrence_count,
                        "modelless",
                    )
                )

    object_data.learn_stage_placements(
        placement for placement, _, _, _ in contexts
    )
    resolved: dict[str, dict[str, Any]] = {}
    resolved_context_count = 0
    unresolved_context_count = 0

    for index, (
        placement,
        actor,
        occurrence_count,
        contribution_key,
    ) in enumerate(contexts, start=1):
        if index == 1 or index % 500 == 0 or index == len(contexts):
            print(
                "Resolving observed placement contexts "
                f"{index:,}/{len(contexts):,}"
            )

        resource = object_data.resolve(placement)

        if not resource.has_model or resource.archive_path is None:
            unresolved_context_count += 1
            continue

        resolved_context_count += 1
        archive_name = resource.archive_path.stem
        archive_key = archive_name.casefold()
        metadata = object_data.archive_metadata(archive_name)

        if metadata is None:
            unresolved_context_count += 1
            resolved_context_count -= 1
            continue

        entry = resolved.setdefault(
            archive_key,
            {
                "archive_name": archive_name,
                "archive_filename": resource.archive_path.name,
                "bfres_files": tuple(metadata.bfres_files),
                "byml_files": tuple(metadata.byml_files),
                "init_model_references": tuple(
                    metadata.init_model_references
                ),
                "sub_actor_model_names": tuple(
                    metadata.sub_actor_model_names
                ),
                "actors": {},
                "source_fields": set(),
                "requested_names": set(),
                "source_stages": set(),
                "model_names": set(),
            },
        )
        signature_key = (
            actor.unit_config_name,
            actor.parameter_config_name,
        )
        evidence = entry["actors"].setdefault(
            signature_key,
            {
                "unit_config_name": actor.unit_config_name,
                "parameter_config_name": actor.parameter_config_name,
                "model_names": set(),
                "source_stages": set(),
                "source_fields": set(),
                "contributions": {},
            },
        )

        if placement.model_name:
            evidence["model_names"].add(placement.model_name)
            entry["model_names"].add(placement.model_name)

        if placement.source_stage_name:
            evidence["source_stages"].add(placement.source_stage_name)
            entry["source_stages"].add(placement.source_stage_name)

        if resource.source_field:
            evidence["source_fields"].add(resource.source_field)
            entry["source_fields"].add(resource.source_field)

        if resource.requested_name:
            entry["requested_names"].add(resource.requested_name)

        previous_count = evidence["contributions"].get(
            contribution_key,
            0,
        )
        evidence["contributions"][contribution_key] = max(
            previous_count,
            occurrence_count,
        )

    records: list[dict[str, Any]] = []

    for archive_key, entry in sorted(
        resolved.items(),
        key=lambda item: item[1]["archive_name"].casefold(),
    ):
        actor_signatures: list[dict[str, Any]] = []
        occurrence_count = 0

        for _, evidence in sorted(
            entry["actors"].items(),
            key=lambda item: (
                item[0][0].casefold(),
                item[0][1].casefold(),
            ),
        ):
            actor_occurrences = sum(evidence["contributions"].values())
            occurrence_count += actor_occurrences
            actor_signatures.append(
                {
                    "record_id": _actor_record_id(
                        evidence["unit_config_name"],
                        evidence["parameter_config_name"],
                    ),
                    "unit_config_name": evidence["unit_config_name"],
                    "parameter_config_name": evidence[
                        "parameter_config_name"
                    ],
                    "model_names": sorted(
                        evidence["model_names"],
                        key=str.casefold,
                    ),
                    "source_stages": sorted(
                        evidence["source_stages"],
                        key=str.casefold,
                    ),
                    "source_fields": sorted(evidence["source_fields"]),
                    "occurrence_count": actor_occurrences,
                }
            )

        category, subcategory, representation = _suggest_archive(
            entry["archive_name"],
            True,
        )
        records.append(
            {
                "record_id": "object:" + archive_key,
                "record_type": "RESOLVED_MODEL",
                "name": entry["archive_name"],
                "archive_name": entry["archive_filename"],
                "bfres_files": list(entry["bfres_files"]),
                "byml_files": list(entry["byml_files"]),
                "init_model_references": list(
                    entry["init_model_references"]
                ),
                "sub_actor_model_names": list(
                    entry["sub_actor_model_names"]
                ),
                "actor_signatures": actor_signatures,
                "actor_record_ids": [
                    actor["record_id"] for actor in actor_signatures
                ],
                "linked_actor_count": len(actor_signatures),
                "occurrence_count": occurrence_count,
                "model_names": sorted(
                    entry["model_names"],
                    key=str.casefold,
                ),
                "source_stages": sorted(
                    entry["source_stages"],
                    key=str.casefold,
                ),
                "source_fields": sorted(entry["source_fields"]),
                "requested_names": sorted(
                    entry["requested_names"],
                    key=str.casefold,
                ),
                "has_bfres": True,
                "suggested_category": category,
                "suggested_subcategory": subcategory,
                "suggested_representation": representation,
            }
        )

    dataset_id = hashlib.sha256(
        (
            registry.romfs_key
            + registry.archive_inventory_digest
            + str(TAXONOMY["version"])
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema_version": 2,
        "dataset_id": dataset_id,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "taxonomy": TAXONOMY,
        "source": {
            "romfs_key": registry.romfs_key,
            "registry_created_utc": registry.created_utc,
            "archive_inventory_digest": registry.archive_inventory_digest,
            "registry_summary": {
                "actor_signature_count": registry.actor_signature_count,
                "placement_count": registry.placement_count,
                "scenario_count": registry.scenario_count,
            },
            "resolution_summary": {
                "placement_context_count": len(contexts),
                "resolved_context_count": resolved_context_count,
                "unresolved_context_count": unresolved_context_count,
                "resolved_model_count": len(records),
            },
        },
        "records": records,
    }

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the offline SMO actor/ObjectData classification dataset."
    )
    parser.add_argument("--romfs", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json-output", type=Path)
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    return parser.parse_args(arguments)


def main() -> None:
    arguments = parse_arguments()
    romfs_root = arguments.romfs.resolve()
    registry_path = arguments.registry.resolve()
    output_path = arguments.output.resolve()
    dataset = build_dataset(romfs_root, registry_path)
    compact = json.dumps(
        dataset,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "window.SMO_CLASSIFIER_DATA = " + compact + ";\n",
        encoding="utf-8",
    )

    if arguments.json_output is not None:
        json_path = arguments.json_output.resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(
        "CLASSIFICATION_DATASET: PASS "
        f"records={len(dataset['records']):,} "
        f"contexts={dataset['source']['resolution_summary']['placement_context_count']:,} "
        f"resolved_models={dataset['source']['resolution_summary']['resolved_model_count']:,} "
        f"output={output_path}"
    )


if __name__ == "__main__":
    main()
