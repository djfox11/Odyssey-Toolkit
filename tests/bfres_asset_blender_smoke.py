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


def run(romfs_root: Path, asset_name: str) -> None:
    archive_path = romfs_root / "ObjectData" / f"{asset_name}.szs"
    archive = read_szs(archive_path)
    bfres_files = tuple(
        entry.name
        for entry in archive.get_files()
        if Path(entry.name).suffix.casefold() == ".bfres"
    )

    if not bfres_files:
        raise ValueError(f"{archive_path.name} contains no BFRES files")

    material = bpy.data.materials.new("SMO BFRES smoke test")
    mesh_count = 0
    colour_set_count = 0

    for bfres_name in bfres_files:
        models = read_static_bfres(bytes(extract_file(archive, bfres_name)))

        for model in models:
            for source in model.meshes:
                mesh = _create_mesh_data(
                    source,
                    Path(bfres_name).stem,
                    material,
                    apply_custom_normals=True,
                )

                if source.normals is not None and not mesh.has_custom_normals:
                    raise AssertionError(
                        f"Custom normals were not applied to {mesh.name}: "
                        f"{mesh.get('smo_custom_normals')}"
                    )

                mesh_count += 1
                colour_set_count += len(mesh.color_attributes)

    print(
        "BFRES_ASSET_SMOKE: PASS "
        f"asset={asset_name} bfres={len(bfres_files)} "
        f"meshes={mesh_count} colour_sets={colour_set_count}"
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 2:
        raise SystemExit(
            "Usage: blender --background --python "
            "bfres_asset_blender_smoke.py -- ROMFS ASSET_NAME"
        )

    run(Path(arguments[0]).resolve(), arguments[1])