from __future__ import annotations

from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.static_model_import import _create_mesh_data
from smo_kingdom_importer.world_list import extract_file, read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    asset_name = "CityWorldHomeBuilding000"
    archive = read_szs(
        romfs_root / "ObjectData" / f"{asset_name}.szs"
    )
    bfres_name = next(
        entry.name
        for entry in archive.get_files()
        if entry.name and entry.name.casefold().endswith(".bfres")
    )
    models = read_static_bfres(extract_file(archive, bfres_name))
    meshes = tuple(mesh for model in models for mesh in model.meshes)
    secondary_meshes = tuple(
        mesh
        for mesh in meshes
        if len(mesh.uv_sets) > 1 and mesh.uv_sets[1] is not None
    )
    check(secondary_meshes, "Representative Metro asset contains no _u1 data")
    source = secondary_meshes[0]
    check(source.uvs is source.uv_sets[0], "Legacy uvs is not the _u0 set")
    check(source.uv_sets[0] is not None, "Representative mesh has no _u0 data")
    check(
        len(source.uv_sets[0]) == len(source.vertices)
        and len(source.uv_sets[1]) == len(source.vertices),
        "Remapped UV-set lengths do not match the vertex count",
    )

    material = bpy.data.materials.new("SMO multiple UV regression")
    imported = _create_mesh_data(source, asset_name, material)
    check(
        tuple(layer.name for layer in imported.uv_layers)
        == ("UVMap", "UVMap.001"),
        f"Unexpected Blender UV layers: {tuple(imported.uv_layers.keys())}",
    )
    check(imported.uv_layers.active_index == 0, "_u0 is not the active UV layer")
    check(imported.uv_layers[0].active_render, "_u0 is not active for rendering")
    check(
        not imported.uv_layers[1].active_render,
        "_u1 incorrectly replaced _u0 as the render layer",
    )
    check(
        imported.get("smo_uv_layers") == "UVMap,UVMap.001",
        "UV-layer provenance metadata is wrong",
    )

    source_vertex = source.triangles[0][0]

    for layer_index, source_uvs in enumerate(source.uv_sets[:2]):
        expected = (
            float(source_uvs[source_vertex][0]),
            1.0 - float(source_uvs[source_vertex][1]),
        )
        actual = tuple(imported.uv_layers[layer_index].data[0].uv)
        check(
            all(abs(left - right) < 1e-6 for left, right in zip(actual, expected)),
            f"UV layer {layer_index} conversion is wrong: {actual} != {expected}",
        )

    print(
        "MULTIPLE_UV_REGRESSION: PASS "
        f"asset={asset_name} mesh={source.name} "
        f"secondary_meshes={len(secondary_meshes)}"
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: multiple_uv_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())
