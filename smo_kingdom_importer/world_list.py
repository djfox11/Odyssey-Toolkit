from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import oead

try:
    from .performance import timed
except ImportError:
    from performance import timed


WORLD_LIST_FILENAME = "WorldList.byml"

WORLD_DISPLAY_NAMES = {
    "Cap": "Cap Kingdom",
    "Waterfall": "Cascade Kingdom",
    "Sand": "Sand Kingdom",
    "Forest": "Wooded Kingdom",
    "Lake": "Lake Kingdom",
    "Cloud": "Cloud Kingdom",
    "Clash": "Lost Kingdom",
    "City": "Metro Kingdom",
    "Sea": "Seaside Kingdom",
    "Snow": "Snow Kingdom",
    "Lava": "Luncheon Kingdom",
    "Attack": "Ruined Kingdom",
    "Sky": "Bowser's Kingdom",
    "Moon": "Moon Kingdom",
    "Peach": "Mushroom Kingdom",
    "Special1": "Dark Side",
    "Special2": "Darker Side",
}


@timed("szs_loading")
def read_szs(path: Path) -> oead.Sarc:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"Could not read {path}: {exc}") from exc

    if data.startswith(b"Yaz0"):
        try:
            data = oead.yaz0.decompress(data)
        except Exception as exc:
            raise RuntimeError(f"Failed to decompress Yaz0 archive: {exc}") from exc

    if not data.startswith(b"SARC"):
        raise ValueError(
            f"{path.name} did not contain a SARC archive after decompression. "
            f"Found magic {data[:4]!r} instead."
        )

    try:
        return oead.Sarc(data)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse SARC archive: {exc}") from exc


def extract_file(archive: oead.Sarc, filename: str) -> bytes:
    entry = archive.get_file(filename)

    if entry is None:
        available = sorted(
            file.name
            for file in archive.get_files()
            if getattr(file, "name", None)
        )

        listing = "\n".join(f"  - {name}" for name in available)

        raise FileNotFoundError(
            f"{filename!r} was not found in the archive.\n"
            f"Archive contents:\n{listing}"
        )

    return bytes(entry.data)


def unwrap(value: Any) -> Any:
    if isinstance(value, oead.byml.Array):
        return [unwrap(item) for item in value]

    if isinstance(value, oead.byml.Hash):
        return {str(key): unwrap(item) for key, item in value.items()}

    if isinstance(value, (oead.S32, oead.U32, oead.S64, oead.U64)):
        return int(value)

    if isinstance(value, (oead.F32, oead.F64)):
        return float(value)

    if isinstance(value, oead.Bytes):
        return bytes(value).hex()

    return value


def read_world_list(path: Path) -> list[dict[str, Any]]:
    archive = read_szs(path)
    byml_data = extract_file(archive, WORLD_LIST_FILENAME)

    if byml_data[:2] not in {b"BY", b"YB"}:
        raise ValueError(
            f"{WORLD_LIST_FILENAME} does not appear to be BYML. "
            f"Found magic {byml_data[:2]!r}."
        )

    try:
        document = unwrap(oead.byml.from_binary(byml_data))
    except Exception as exc:
        raise RuntimeError(f"Failed to parse {WORLD_LIST_FILENAME}: {exc}") from exc

    if not isinstance(document, list):
        raise TypeError(
            "Expected WorldList.byml's root node to be an array, "
            f"but got {type(document).__name__}."
        )

    worlds: list[dict[str, Any]] = []

    for index, world in enumerate(document):
        if not isinstance(world, dict):
            raise TypeError(
                f"World entry {index} should be a dictionary, "
                f"but got {type(world).__name__}."
            )

        worlds.append(world)

    return worlds


def get_world_display_name(world: dict[str, Any]) -> str:
    internal_name = str(world.get("WorldName", "Unknown"))
    return WORLD_DISPLAY_NAMES.get(internal_name, internal_name)


def print_world_summary(worlds: list[dict[str, Any]]) -> None:
    columns = (
        ("Index", 5),
        ("Kingdom", 20),
        ("Internal", 10),
        ("Stage", 31),
        ("Scenarios", 9),
        ("Clear", 5),
        ("Ending", 6),
        ("MoonRock", 8),
        ("Balloon", 7),
    )

    header = "  ".join(name.ljust(width) for name, width in columns)
    print(header)
    print("-" * len(header))

    for index, world in enumerate(worlds):
        values = (
            str(index),
            get_world_display_name(world),
            str(world.get("WorldName", "—")),
            str(world.get("Name", "—")),
            str(world.get("ScenarioNum", "—")),
            str(world.get("ClearMainScenario", "—")),
            str(world.get("AfterEndingScenario", "—")),
            str(world.get("MoonRockScenario", "—")),
            str(world.get("BalloonScenario", "—")),
        )

        print(
            "  ".join(
                value.ljust(width)
                for value, (_, width) in zip(values, columns)
            )
        )


def print_world_details(worlds: list[dict[str, Any]], query: str) -> None:
    query_lower = query.casefold()

    matches = [
        world
        for world in worlds
        if str(world.get("WorldName", "")).casefold() == query_lower
        or str(world.get("Name", "")).casefold() == query_lower
    ]

    if not matches:
        raise LookupError(f"No world matched {query!r}.")

    for world in matches:
        print(json.dumps(world, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read Super Mario Odyssey's SystemData/WorldList.szs "
            "and inspect WorldList.byml."
        )
    )

    parser.add_argument("path", type=Path, help="Path to WorldList.szs")
    parser.add_argument(
        "--world",
        metavar="NAME",
        help="Show one complete entry by WorldName or stage Name.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        metavar="OUTPUT",
        help="Write the complete decoded world list as JSON.",
    )

    args = parser.parse_args()

    try:
        worlds = read_world_list(args.path)
        print(f"Read {len(worlds)} world entries from {args.path.name}.\n")

        if args.world:
            print_world_details(worlds, args.world)
        else:
            print_world_summary(worlds)

        if args.json:
            args.json.write_text(
                json.dumps(worlds, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"\nWrote {args.json}")

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
