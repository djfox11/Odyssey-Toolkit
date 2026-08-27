from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.world_list import extract_file, read_szs


_RIM_MARKERS = ("cloth", "fresnel", "nov", "rim")


def _matches(name: str) -> bool:
    lowered = name.casefold()
    return any(marker in lowered for marker in _RIM_MARKERS)


def _asset_records(romfs_root: Path, asset_name: str) -> list[dict[str, object]]:
    archive = read_szs(romfs_root / "ObjectData" / f"{asset_name}.szs")
    records = []

    for entry in archive.get_files():
        if not entry.name or Path(entry.name).suffix.casefold() != ".bfres":
            continue

        try:
            models = read_static_bfres(bytes(extract_file(archive, entry.name)))
        except (NotImplementedError, ValueError):
            continue

        for model in models:
            for mesh in model.meshes:
                shader = mesh.material_shader
                if shader is None:
                    continue

                parameters = {
                    parameter.name: parameter.value
                    for parameter in shader.parameters
                    if _matches(parameter.name)
                }
                options = {
                    name: value
                    for name, value in shader.shader_options
                    if _matches(name)
                }

                if not parameters and not options:
                    continue

                records.append(
                    {
                        "asset": asset_name,
                        "bfres": entry.name,
                        "model": model.name,
                        "mesh": mesh.name,
                        "material": mesh.material_name,
                        "parameters": parameters,
                        "options": options,
                    }
                )

    return records


def main(arguments: list[str]) -> None:
    if len(arguments) < 2:
        raise SystemExit(
            "Usage: audit_shader_rim.py -- ROMFS ASSET [ASSET ...]"
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
