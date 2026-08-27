from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.world_list import extract_file, read_szs


def _asset_records(romfs_root: Path, asset_name: str) -> list[dict[str, object]]:
    archive = read_szs(romfs_root / "ObjectData" / f"{asset_name}.szs")
    bfres_names = tuple(
        entry.name
        for entry in archive.get_files()
        if entry.name and entry.name.casefold().endswith(".bfres")
    )
    records = []

    for bfres_name in bfres_names:
        try:
            models = read_static_bfres(
                bytes(extract_file(archive, bfres_name))
            )
        except (NotImplementedError, ValueError):
            continue

        for model in models:
            for mesh in model.meshes:
                shader = mesh.material_shader

                if shader is None:
                    continue

                records.append(
                    {
                        "asset": asset_name,
                        "bfres": bfres_name,
                        "model": model.name,
                        "mesh": mesh.name,
                        "material": mesh.material_name,
                        "uv_sets": [
                            index
                            for index, values in enumerate(mesh.uv_sets)
                            if values is not None
                        ],
                        "attribute_assignments": dict(
                            shader.attribute_assignments
                        ),
                        "uv_options": {
                            name: value
                            for name, value in shader.shader_options
                            if any(
                                marker in name.casefold()
                                for marker in ("fuv", "uv", "mtx")
                            )
                        },
                        "texture_transforms": {
                            parameter.name: {
                                "type": parameter.type_name,
                                "value": parameter.value,
                            }
                            for parameter in shader.parameters
                            if parameter.type_id in {30, 31}
                        },
                    }
                )

    return records


def main(arguments: list[str]) -> None:
    if len(arguments) < 2:
        raise SystemExit(
            "Usage: audit_shader_uvs.py -- ROMFS ASSET [ASSET ...]"
        )

    romfs_root = Path(arguments[0]).resolve()
    records = [
        record
        for asset_name in arguments[1:]
        for record in _asset_records(romfs_root, asset_name)
    ]
    print(json.dumps(records, indent=2, sort_keys=True))


if __name__ == "__main__":
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    main(values)
