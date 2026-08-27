from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_animation import (
    BFRESAnimationError,
    _Reader,
    _validate_fres,
    read_bone_visibility_animations,
    read_material_animations,
    read_scene_animations,
    read_skeletal_animations,
)
from smo_kingdom_importer.world_list import read_szs


RESOURCE_LAYOUT = {
    "fska": (190, 64),
    "fmaa": (192, 80),
    "fbvs": (194, 96),
    "fsha": (196, 112),
    "fscn": (198, 128),
}

SUPPORT = {
    "FSKA skeletal": "Supported",
    "FMAA shader parameter (_fts)": "Supported when translated nodes expose bindings",
    "FMAA colour (_fcl)": "Supported when translated nodes expose bindings",
    "FMAA texture pattern (_ftp)": "Not supported",
    "FMAA material visibility (_fvt)": "Not supported",
    "FBVS bone visibility": "Supported",
    "FSHA shape/morph": "Not supported",
    "FSCN/FCAM camera": "Supported except Euler-ZXY cameras",
    "FSCN light": "Not supported",
    "FSCN fog": "Not supported",
}


def _animation_names(reader: _Reader, count: int, dictionary_offset: int) -> list[str]:
    if not count:
        return []
    names = list(reader.dictionary_keys(dictionary_offset))
    if len(names) != count:
        raise BFRESAnimationError(
            f"Declared {count} resources but dictionary contains {len(names)} names."
        )
    return names


def _material_kind(name: str) -> str:
    folded = name.casefold()
    if folded.endswith("_fts"):
        return "shader_parameter"
    if folded.endswith("_fcl"):
        return "color"
    if folded.endswith("_ftp"):
        return "texture_pattern"
    if folded.endswith("_fvt"):
        return "material_visibility"
    return "other"


def _scene_details(reader: _Reader, parsed_scenes: tuple[Any, ...]) -> list[dict[str, Any]]:
    scene_count = reader.u16(198)
    scene_array_offset = reader.u64(120)
    parsed_by_name = {scene.name: scene for scene in parsed_scenes}
    result = []

    if not scene_count or not scene_array_offset:
        return result

    for index in range(scene_count):
        offset = scene_array_offset + index * 104
        name = reader.pointer_string(offset + 16)
        parsed = parsed_by_name.get(name)
        camera_count = reader.u16(offset + 98)
        light_count = reader.u16(offset + 100)
        fog_count = reader.u16(offset + 102)
        camera_names = _animation_names(reader, camera_count, reader.u64(offset + 40))
        light_names = _animation_names(reader, light_count, reader.u64(offset + 56))
        fog_names = _animation_names(reader, fog_count, reader.u64(offset + 72))
        cameras = []

        if parsed is not None:
            for camera in parsed.cameras:
                cameras.append(
                    {
                        "name": camera.name,
                        "frame_count": camera.frame_count,
                        "looping": camera.looping,
                        "projection": "perspective" if camera.perspective else "orthographic",
                        "rotation": "euler_zxy" if camera.euler_zxy else "look_at",
                        "curve_count": len(camera.curves),
                        "base_aspect_ratio": camera.evaluate("aspect_ratio", 0.0),
                        "base_field_of_view": camera.evaluate("field_of_view", 0.0),
                    }
                )

        result.append(
            {
                "name": name,
                "path": reader.pointer_string(offset + 24),
                "camera_count": camera_count,
                "camera_names": camera_names,
                "cameras": cameras,
                "light_count": light_count,
                "light_names": light_names,
                "fog_count": fog_count,
                "fog_names": fog_names,
            }
        )

    return result


def inspect_bfres(container: str, member: str, data: bytes) -> dict[str, Any]:
    item: dict[str, Any] = {
        "container": container,
        "member": member,
        "size": len(data),
        "version_major": None,
        "counts": {kind: 0 for kind in RESOURCE_LAYOUT},
        "names": {kind: [] for kind in RESOURCE_LAYOUT},
        "skeletal": [],
        "material": [],
        "visibility": [],
        "shape": [],
        "scene": [],
        "parse_errors": [],
    }

    try:
        reader = _Reader(data)
        _validate_fres(reader)
        item["version_major"] = (reader.u32(8) >> 16) & 0xFF
    except Exception as exc:
        item["parse_errors"].append(f"header: {exc}")
        return item

    for kind, (count_offset, dictionary_pointer_offset) in RESOURCE_LAYOUT.items():
        count = reader.u16(count_offset)
        item["counts"][kind] = count
        try:
            item["names"][kind] = _animation_names(
                reader,
                count,
                reader.u64(dictionary_pointer_offset),
            )
        except Exception as exc:
            item["parse_errors"].append(f"{kind} names: {exc}")

    if item["counts"]["fska"]:
        try:
            item["skeletal"] = [
                {
                    "name": animation.name,
                    "path": animation.path,
                    "frame_count": animation.frame_count,
                    "looping": animation.looping,
                    "rotation": "euler_xyz" if animation.euler_xyz else "quaternion",
                    "bone_count": len(animation.bones),
                    "curve_count": sum(len(bone.curves) for bone in animation.bones),
                }
                for animation in read_skeletal_animations(data)
            ]
        except Exception as exc:
            item["parse_errors"].append(f"fska parse: {exc}")

    if item["counts"]["fmaa"]:
        try:
            item["material"] = [
                {
                    "name": animation.name,
                    "kind": _material_kind(animation.name),
                    "path": animation.path,
                    "frame_count": animation.frame_count,
                    "looping": animation.looping,
                    "target_count": len(animation.targets),
                    "parameter_count": sum(
                        len(target.parameters) for target in animation.targets
                    ),
                }
                for animation in read_material_animations(data)
            ]
        except Exception as exc:
            item["parse_errors"].append(f"fmaa parse: {exc}")

    if item["counts"]["fbvs"]:
        try:
            item["visibility"] = [
                {
                    "name": animation.name,
                    "path": animation.path,
                    "frame_count": animation.frame_count,
                    "looping": animation.looping,
                    "target_count": len(animation.targets),
                }
                for animation in read_bone_visibility_animations(data)
            ]
        except Exception as exc:
            item["parse_errors"].append(f"fbvs parse: {exc}")

    item["shape"] = [
        {"name": name, "supported": False}
        for name in item["names"]["fsha"]
    ]

    parsed_scenes: tuple[Any, ...] = ()
    if item["counts"]["fscn"]:
        try:
            parsed_scenes = read_scene_animations(data)
        except Exception as exc:
            item["parse_errors"].append(f"fscn camera parse: {exc}")
        try:
            item["scene"] = _scene_details(reader, parsed_scenes)
        except Exception as exc:
            item["parse_errors"].append(f"fscn inventory: {exc}")

    return item


def _bfres_members(path: Path) -> tuple[tuple[str, bytes], ...]:
    if path.suffix.casefold() == ".bfres":
        return (("", path.read_bytes()),)

    archive = read_szs(path)
    return tuple(
        (entry.name, bytes(entry.data))
        for entry in archive.get_files()
        if entry.name and Path(entry.name).suffix.casefold() == ".bfres"
    )


def _joined(values: list[str]) -> str:
    return "; ".join(values)


def _markdown_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_csv(path: Path, files: list[dict[str, Any]]) -> None:
    fields = (
        "container",
        "member",
        "size",
        "version_major",
        "has_animations",
        "fska_count",
        "fmaa_count",
        "fbvs_count",
        "fsha_count",
        "fscn_count",
        "fcam_count",
        "flight_count",
        "ffog_count",
        "fska_names",
        "fmaa_names",
        "fbvs_names",
        "fsha_names",
        "fscn_names",
        "fcam_names",
        "flight_names",
        "ffog_names",
        "parse_errors",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in files:
            scene = item["scene"]
            counts = item["counts"]
            writer.writerow(
                {
                    "container": item["container"],
                    "member": item["member"],
                    "size": item["size"],
                    "version_major": item["version_major"],
                    "has_animations": any(counts.values()),
                    "fska_count": counts["fska"],
                    "fmaa_count": counts["fmaa"],
                    "fbvs_count": counts["fbvs"],
                    "fsha_count": counts["fsha"],
                    "fscn_count": counts["fscn"],
                    "fcam_count": sum(value["camera_count"] for value in scene),
                    "flight_count": sum(value["light_count"] for value in scene),
                    "ffog_count": sum(value["fog_count"] for value in scene),
                    "fska_names": _joined(item["names"]["fska"]),
                    "fmaa_names": _joined(item["names"]["fmaa"]),
                    "fbvs_names": _joined(item["names"]["fbvs"]),
                    "fsha_names": _joined(item["names"]["fsha"]),
                    "fscn_names": _joined(item["names"]["fscn"]),
                    "fcam_names": _joined(
                        [name for value in scene for name in value["camera_names"]]
                    ),
                    "flight_names": _joined(
                        [name for value in scene for name in value["light_names"]]
                    ),
                    "ffog_names": _joined(
                        [name for value in scene for name in value["fog_names"]]
                    ),
                    "parse_errors": _joined(item["parse_errors"]),
                }
            )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    files = report["files"]
    animated = [item for item in files if any(item["counts"].values())]
    lines = [
        "# Super Mario Odyssey BFRES Animation Inventory",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"ROMFS: `{report['romfs']}`",
        "",
        "The CSV and JSON companions contain every discovered BFRES file. This readable",
        "summary lists animation-bearing BFRES files and expands all FSCN scene resources.",
        "",
        "## Import support",
        "",
        "| Resource | Current support |",
        "|---|---|",
    ]
    lines.extend(
        f"| {_markdown_escape(kind)} | {_markdown_escape(status)} |"
        for kind, status in SUPPORT.items()
    )
    totals = report["totals"]
    lines.extend(
        (
            "",
            "## Summary",
            "",
            f"- Container files scanned: {report['container_files_scanned']}",
            f"- BFRES files found: {len(files)}",
            f"- Animation-bearing BFRES files: {len(animated)}",
            f"- FSKA: {totals['fska']}",
            f"- FMAA: {totals['fmaa']}",
            f"- FBVS: {totals['fbvs']}",
            f"- FSHA: {totals['fsha']}",
            f"- FSCN: {totals['fscn']}",
            f"- FCAM camera tracks: {totals['fcam']}",
            f"- FSCN light tracks: {totals['flight']}",
            f"- FSCN fog tracks: {totals['ffog']}",
            f"- BFRES parse issues: {totals['bfres_with_errors']}",
            f"- Container read issues: {len(report['container_errors'])}",
            "",
            "## Animation-bearing BFRES files",
            "",
            "| Container | BFRES member | FSKA | FMAA | FBVS | FSHA | FSCN | FCAM | Light | Fog |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        )
    )
    for item in animated:
        scene = item["scene"]
        counts = item["counts"]
        lines.append(
            "| "
            + " | ".join(
                _markdown_escape(value)
                for value in (
                    item["container"],
                    item["member"] or "(direct BFRES)",
                    counts["fska"],
                    counts["fmaa"],
                    counts["fbvs"],
                    counts["fsha"],
                    counts["fscn"],
                    sum(value["camera_count"] for value in scene),
                    sum(value["light_count"] for value in scene),
                    sum(value["fog_count"] for value in scene),
                )
            )
            + " |"
        )

    scene_files = [item for item in animated if item["counts"]["fscn"]]
    lines.extend(("", "## Scene animations", ""))
    if not scene_files:
        lines.append("No FSCN resources were found.")
    for item in scene_files:
        location = f"{item['container']} :: {item['member'] or '(direct BFRES)'}"
        lines.extend((f"### `{location}`", ""))
        for scene in item["scene"]:
            lines.append(
                f"- `{scene['name']}`: cameras={scene['camera_count']}, "
                f"lights={scene['light_count']}, fogs={scene['fog_count']}"
            )
            for camera in scene["cameras"]:
                lines.append(
                    "  - Camera "
                    f"`{camera['name']}`: frames=0..{camera['frame_count']}, "
                    f"curves={camera['curve_count']}, {camera['projection']}, "
                    f"{camera['rotation']}, aspect={camera['base_aspect_ratio']:.6g}"
                )
            if scene["light_names"]:
                lines.append("  - Lights: " + ", ".join(f"`{name}`" for name in scene["light_names"]))
            if scene["fog_names"]:
                lines.append("  - Fogs: " + ", ".join(f"`{name}`" for name in scene["fog_names"]))
        lines.append("")

    if report["container_errors"] or totals["bfres_with_errors"]:
        lines.extend(("## Read and parse issues", ""))
        for error in report["container_errors"]:
            lines.append(f"- `{error['container']}`: {error['error']}")
        for item in files:
            for error in item["parse_errors"]:
                lines.append(
                    f"- `{item['container']} :: {item['member']}`: {error}"
                )
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(romfs: Path, output_prefix: Path) -> None:
    candidates = sorted(
        path
        for path in romfs.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".szs", ".bfres"}
    )
    files: list[dict[str, Any]] = []
    container_errors = []

    for index, path in enumerate(candidates, 1):
        relative = path.relative_to(romfs).as_posix()
        try:
            members = _bfres_members(path)
            files.extend(
                inspect_bfres(relative, member, data)
                for member, data in members
            )
        except Exception as exc:
            container_errors.append({"container": relative, "error": str(exc)})

        if index % 100 == 0 or index == len(candidates):
            print(
                f"Scanned {index}/{len(candidates)} containers; "
                f"BFRES={len(files)} errors={len(container_errors)}",
                flush=True,
            )

    totals = {kind: sum(item["counts"][kind] for item in files) for kind in RESOURCE_LAYOUT}
    totals.update(
        {
            "fcam": sum(
                scene["camera_count"] for item in files for scene in item["scene"]
            ),
            "flight": sum(
                scene["light_count"] for item in files for scene in item["scene"]
            ),
            "ffog": sum(
                scene["fog_count"] for item in files for scene in item["scene"]
            ),
            "bfres_with_errors": sum(bool(item["parse_errors"]) for item in files),
        }
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "romfs": str(romfs),
        "support": SUPPORT,
        "container_files_scanned": len(candidates),
        "container_errors": container_errors,
        "totals": totals,
        "files": files,
    }
    json_path = output_prefix.with_suffix(".json")
    csv_path = output_prefix.with_suffix(".csv")
    markdown_path = output_prefix.with_suffix(".md")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(csv_path, files)
    write_markdown(markdown_path, report)
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {markdown_path}")
    print("TOTALS", json.dumps(totals, sort_keys=True))


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(arguments) not in {1, 2}:
        raise SystemExit(
            "Usage: report_bfres_animation_inventory.py -- ROMFS [OUTPUT_PREFIX]"
        )
    romfs_root = Path(arguments[0]).resolve()
    prefix = (
        Path(arguments[1]).resolve()
        if len(arguments) == 2
        else ROOT / "BFRES_ANIMATION_INVENTORY"
    )
    run(romfs_root, prefix)
