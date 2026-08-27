from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import json
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.placement_classifier import classify_stage_scenario
from smo_kingdom_importer.stage_data import read_stage_scenario
from smo_kingdom_importer.static_model_import import (
    SMO_OT_import_static_models,
    _stage_texture_archive_names,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


TARGETS = {
    "ForestWorldHomeTree000": "ForestWorldHomeStageTexture.szs",
    "WaterfallWorldBreakParts004": "WaterfallWorldHomeStageTexture.szs",
    "WaterfallWorldBreakParts006": "WaterfallWorldHomeStageTexture.szs",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def texture_names(path: Path) -> frozenset[str]:
    archive = read_szs(path)
    names: set[str] = set()

    for entry in archive.get_files():
        if Path(entry.name).suffix.casefold() != ".bfres":
            continue

        textures = BntxTextureArchive.from_bfres(bytes(entry.data))

        if textures is not None:
            names.update(textures.names)

    return frozenset(names)


def required_texture_names(path: Path) -> frozenset[str]:
    archive = read_szs(path)
    names: set[str] = set()

    for entry in archive.get_files():
        if Path(entry.name).suffix.casefold() != ".bfres":
            continue

        data = bytes(extract_file(archive, entry.name))

        for model in read_static_bfres(data):
            for mesh in model.meshes:
                names.update(mesh.texture_names)

    return frozenset(names)


def run(romfs_root: Path) -> None:
    object_data = romfs_root / "ObjectData"
    stage = read_stage_scenario(
        romfs_root,
        "ForestWorldWoodsStage",
        1,
    )
    classified = classify_stage_scenario(stage, ObjectDataIndex(romfs_root))
    matching = [
        item
        for item in classified
        if item.resource.archive_path is not None
        and item.resource.archive_path.stem in TARGETS
    ]
    counts = Counter(
        item.resource.archive_path.stem
        for item in matching
        if item.resource.archive_path is not None
    )
    check(
        set(counts) == set(TARGETS),
        f"Deep Woods target placements changed: {dict(counts)}",
    )

    check(
        "ForestWorldHomeStageTexture.szs"
        in _stage_texture_archive_names("ForestWorldWoodsStage"),
        "Forest subarea did not inherit its kingdom HomeStage textures",
    )
    check(
        "WaterfallWorldHomeStageTexture.szs"
        in _stage_texture_archive_names("WaterfallWorldBreakParts004"),
        "Cross-kingdom Waterfall asset did not inherit its own family textures",
    )

    host = SimpleNamespace(
        _stage_name="ForestWorldWoodsStage",
        _object_data_dir=object_data,
    )
    archive_texture_names: dict[Path, frozenset[str]] = {}
    candidates_by_asset: dict[str, list[str]] = {}

    for asset_name, expected_archive in TARGETS.items():
        item = next(
            candidate
            for candidate in matching
            if candidate.resource.archive_path is not None
            and candidate.resource.archive_path.stem == asset_name
        )
        paths = SMO_OT_import_static_models._shared_texture_paths(host, item)
        path_names = [path.name for path in paths]
        candidates_by_asset[asset_name] = path_names
        check(
            expected_archive in path_names,
            f"{asset_name} candidates omit {expected_archive}: {path_names}",
        )

        available: set[str] = set()

        for path in paths:
            if path not in archive_texture_names:
                archive_texture_names[path] = texture_names(path)

            available.update(archive_texture_names[path])

        required = required_texture_names(
            object_data / f"{asset_name}.szs"
        )
        check(required, f"{asset_name} has no FMAT texture references")
        missing = sorted(required - available, key=str.casefold)
        check(
            not missing,
            f"{asset_name} still has unresolved textures: {missing}",
        )

    print(
        "DEEP_WOODS_TEXTURE_REGRESSION: PASS "
        + json.dumps(
            {
                "placements": dict(sorted(counts.items())),
                "candidates": candidates_by_asset,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: blender --python deep_woods_texture_regression.py -- ROMFS"
        )

    run(Path(sys.argv[-1]).resolve())
