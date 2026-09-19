from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2s
import json
from typing import Any


@dataclass(slots=True, frozen=True)
class ImportPlacementCollection:
    label: str
    key: str


def _clean_name(value: Any) -> str:
    return str(value or "").strip()


def import_placement_collection(
    placement: Any,
) -> ImportPlacementCollection:
    """Describe the stable collection for one placed stage object."""
    identifier = _clean_name(getattr(placement, "identifier", ""))
    object_name = _clean_name(
        getattr(placement, "unit_config_name", "")
    )

    if not object_name:
        object_name = _clean_name(getattr(placement, "model_name", ""))

    if not object_name:
        object_name = "Unresolved Object"

    label = f"[{identifier}] {object_name}" if identifier else object_name
    identity = {
        "identifier": identifier,
        "source_stage": _clean_name(
            getattr(placement, "source_stage_name", "")
        ),
        "stage_layer": _clean_name(
            getattr(placement, "stage_layer", "")
        ),
        "zone_path": tuple(getattr(placement, "zone_path", ()) or ()),
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = blake2s(encoded, digest_size=8).hexdigest()
    return ImportPlacementCollection(
        label=label,
        key=f"PLACEMENT:{digest}",
    )


def ensure_import_placement_collection(
    parent: Any,
    specification: ImportPlacementCollection,
    collections: Any,
) -> Any:
    """Create or reuse a generated placement collection below a category."""
    collection = next(
        (
            child
            for child in parent.children
            if child.get("smo_import_placement_key") == specification.key
        ),
        None,
    )

    if collection is None:
        collection = collections.new(specification.label)
        parent.children.link(collection)
    elif collection.name != specification.label:
        collection.name = specification.label

    collection["smo_import_generated"] = True
    collection["smo_import_placement"] = True
    collection["smo_import_placement_key"] = specification.key
    collection["smo_import_placement_name"] = specification.label
    return collection
