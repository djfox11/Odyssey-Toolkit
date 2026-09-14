from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2s
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class ImportAssetCollection:
    label: str
    key: str


def _clean_name(value: Any) -> str:
    return str(value or "").strip()


def _archive_name(value: Any) -> str:
    name = _clean_name(value)
    return Path(name).name if name else ""


def _resource_members(resource: Any) -> tuple[Any, ...]:
    components = tuple(getattr(resource, "components", ()) or ())
    return components or (resource,)


def _resource_label(resource: Any) -> str:
    requested_name = _clean_name(getattr(resource, "requested_name", ""))

    if requested_name:
        return requested_name

    archive_name = _archive_name(getattr(resource, "archive_path", None))

    if archive_name:
        return Path(archive_name).stem

    bfres_files = tuple(getattr(resource, "bfres_files", ()) or ())
    return Path(str(bfres_files[0])).stem if bfres_files else ""


def import_asset_collection(
    placement: Any,
    resource: Any,
) -> ImportAssetCollection:
    """Describe the stable collection shared by placements of one asset."""
    label = _resource_label(resource)

    if not label:
        label = _clean_name(getattr(placement, "model_name", ""))

    if not label:
        label = _clean_name(getattr(placement, "unit_config_name", ""))

    if not label:
        label = "Unresolved Asset"

    members = []

    for member in _resource_members(resource):
        members.append(
            {
                "archive": _archive_name(
                    getattr(member, "archive_path", None)
                ).casefold(),
                "bfres": sorted(
                    (
                        _clean_name(name).replace("\\", "/").casefold()
                        for name in (
                            getattr(member, "bfres_files", ()) or ()
                        )
                        if _clean_name(name)
                    )
                ),
                "requested": _clean_name(
                    getattr(member, "requested_name", "")
                ).casefold(),
            }
        )

    members.sort(
        key=lambda member: (
            member["archive"],
            member["requested"],
            member["bfres"],
        )
    )
    identity = {
        "label": label.casefold(),
        "resources": members,
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = blake2s(encoded, digest_size=8).hexdigest()
    return ImportAssetCollection(label=label, key=f"ASSET:{digest}")


def ensure_import_asset_collection(
    parent: Any,
    specification: ImportAssetCollection,
    collections: Any,
) -> Any:
    """Create or reuse a generated asset collection below a category."""
    collection = next(
        (
            child
            for child in parent.children
            if child.get("smo_import_asset_key") == specification.key
        ),
        None,
    )

    if collection is None:
        collection = collections.new(specification.label)
        parent.children.link(collection)
    elif collection.name != specification.label:
        collection.name = specification.label

    collection["smo_import_generated"] = True
    collection["smo_import_asset"] = True
    collection["smo_import_asset_key"] = specification.key
    collection["smo_import_asset_name"] = specification.label
    return collection
