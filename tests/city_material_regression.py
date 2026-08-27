from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.static_model_import import (
    SMO_OT_import_static_models,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


CASES = (
    ("CityWorldHomeBuilding000", "ConcreteWallMain00"),
    ("CityWorldHomeFrame000", "SteelFrameSquare12m00"),
    ("CityWorldHomeGround000", "BaseAsphaltRoad01"),
    ("CityWorldHomeMiddleView000", "CityMiddleViewBuildings00"),
)


def bfres_data(path: Path) -> tuple[str, bytes]:
    sarc = read_szs(path)
    bfres_name = next(
        entry.name
        for entry in sarc.get_files()
        if entry.name and entry.name.casefold().endswith(".bfres")
    )
    return bfres_name, bytes(extract_file(sarc, bfres_name))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    object_data = romfs_root / "ObjectData"
    stage_path = object_data / "CityWorldHomeStageTexture.szs"
    stage_bfres_name, stage_data = bfres_data(stage_path)
    stage_archive = BntxTextureArchive.from_bfres(stage_data)
    check(stage_archive is not None, "City StageTexture BFRES has no BNTX")
    result = {}

    for asset_name, material_name in CASES:
        asset_path = object_data / f"{asset_name}.szs"
        asset_bfres_name, asset_data = bfres_data(asset_path)
        meshes = tuple(
            mesh
            for model in read_static_bfres(asset_data)
            for mesh in model.meshes
        )
        source = next(
            mesh for mesh in meshes if mesh.material_name == material_name
        )
        harness = SimpleNamespace(
            _decoded_texture_cache={},
            _image_cache={},
            _image_transparency={},
            _material_cache={},
            _missing_albedo_textures=set(),
            _fallback_display_textures=set(),
            _texture_errors={},
            _load_texture_archive=lambda asset_key, data: (
                BntxTextureArchive.from_bfres(data)
            ),
            _load_shared_texture_archive=lambda archive_path: (
                (stage_path, stage_bfres_name),
                stage_archive,
            ),
        )
        material = SMO_OT_import_static_models._material_for_mesh(
            harness,
            source,
            (asset_path, asset_bfres_name),
            asset_data,
            (stage_path,),
            asset_name,
        )
        connected = json.loads(material["smo_connected_textures"])
        check(
            connected.get("ALBEDO", "").casefold().endswith("_alb"),
            f"{asset_name} did not connect an albedo: {connected}",
        )
        image_stats = {}

        for node in material.node_tree.nodes:
            role = node.get("smo_texture_role")

            if node.type != "TEX_IMAGE" or role not in {"NORMAL", "ROUGHNESS"}:
                continue

            pixels = np.empty(len(node.image.pixels), dtype=np.float32)
            node.image.pixels.foreach_get(pixels)
            rgba = pixels.reshape(-1, 4)
            channel_count = 2 if role == "NORMAL" else 1
            channels = rgba[:, :channel_count]
            image_stats[node.get("smo_texture_name")] = {
                "role": role,
                "min": round(float(channels.min()), 6),
                "max": round(float(channels.max()), 6),
                "mean": round(float(channels.mean()), 6),
            }
            check(
                float(channels.max()) > 0.1,
                f"{asset_name} {role.lower()} image is black",
            )

        check(
            any(value["role"] == "NORMAL" for value in image_stats.values()),
            f"{asset_name} did not create a normal image node",
        )
        result[asset_name] = {
            "connected": connected,
            "image_stats": image_stats,
            "texture_errors": harness._texture_errors,
        }

    print("SMO_CITY_MATERIAL_REGRESSION=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python "
            "city_material_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())
