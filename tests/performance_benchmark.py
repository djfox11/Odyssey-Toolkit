from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.performance import (
    PerformanceTimings,
    reset_active_timings,
    set_active_timings,
)
from smo_kingdom_importer.placement_classifier import classify_stage_scenario
from smo_kingdom_importer.stage_data import read_stage_scenario
from smo_kingdom_importer.static_model_import import (
    _create_mesh_data,
    _rgba8_to_blender_pixels,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


def bfres_data(romfs_root: Path, asset_name: str) -> tuple[str, bytes]:
    archive = read_szs(romfs_root / "ObjectData" / f"{asset_name}.szs")
    name = next(
        entry.name
        for entry in archive.get_files()
        if entry.name and entry.name.casefold().endswith(".bfres")
    )
    return name, extract_file(archive, name)


def run(romfs_root: Path) -> None:
    timings = PerformanceTimings()
    timing_token = set_active_timings(timings)
    result: dict[str, object] = {
        "blender_version": bpy.app.version_string,
    }

    try:
        started = time.perf_counter()
        stage = read_stage_scenario(
            romfs_root,
            "CityWorldHomeStage",
            2,
        )
        result["stage_read_seconds"] = time.perf_counter() - started
        result["stage_placements"] = len(stage.placements)
        result["expanded_zones"] = len(stage.expanded_zones)

        started = time.perf_counter()
        classified = classify_stage_scenario(
            stage,
            ObjectDataIndex(romfs_root),
        )
        result["object_resolution_seconds"] = time.perf_counter() - started
        result["classified_placements"] = len(classified)

        _, car_data = bfres_data(romfs_root, "Car")
        texture_archive = BntxTextureArchive.from_bfres(car_data)

        if texture_archive is None:
            raise AssertionError("Car.bfres has no embedded BNTX")

        started = time.perf_counter()
        decoded = texture_archive.decode("CarBody_nrm")
        result["bc5_snorm_decode_seconds"] = time.perf_counter() - started
        started = time.perf_counter()
        pixels = _rgba8_to_blender_pixels(
            decoded.rgba8,
            decoded.width,
            decoded.height,
            True,
        )
        result["normal_blue_flip_float_seconds"] = (
            time.perf_counter() - started
        )
        result["texture_dimensions"] = [decoded.width, decoded.height]
        result["texture_float_count"] = len(pixels)

        forest_name, forest_data = bfres_data(
            romfs_root,
            "ForestWorldUnderGround000",
        )
        started = time.perf_counter()
        models = read_static_bfres(forest_data)
        result["bfres_parse_seconds"] = time.perf_counter() - started
        sources = tuple(mesh for model in models for mesh in model.meshes)
        material = bpy.data.materials.new("SMO mesh benchmark")
        started = time.perf_counter()
        meshes = tuple(
            _create_mesh_data(
                source,
                Path(forest_name).stem,
                material,
                apply_custom_normals=False,
            )
            for source in sources
        )
        result["mesh_create_seconds"] = time.perf_counter() - started
        result["mesh_count"] = len(meshes)
        result["mesh_vertices"] = sum(len(mesh.vertices) for mesh in meshes)
        result["mesh_triangles"] = sum(len(mesh.polygons) for mesh in meshes)
        result["mesh_loops"] = sum(len(mesh.loops) for mesh in meshes)
        result["colour_sets"] = sum(
            len(mesh.color_attributes) for mesh in meshes
        )
        result["timings"] = timings.payload()
    finally:
        reset_active_timings(timing_token)

    print("SMO_PERFORMANCE_BENCHMARK=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python performance_benchmark.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())