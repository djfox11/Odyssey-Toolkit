from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_animation import read_skeletal_animations
from smo_kingdom_importer.bfres_mesh import _Reader, read_static_bfres
from smo_kingdom_importer.world_list import read_szs


def _ranges(values: tuple[float, ...]) -> str:
    return ", ".join(f"{value:.6g}" for value in values)


def run(archive_path: Path, animation_name: str) -> None:
    archive = read_szs(archive_path)
    entries = tuple(
        entry
        for entry in archive.get_files()
        if entry.name and Path(entry.name).suffix.casefold() == ".bfres"
    )

    for entry in entries:
        data = bytes(entry.data)
        animations = read_skeletal_animations(data)
        animation = next(
            (item for item in animations if item.name == animation_name),
            None,
        )

        if animation is None:
            continue

        models = read_static_bfres(data, include_rigging=True)
        reader = _Reader(data)
        fskl_offset = data.find(b"FSKL")
        skeleton_flags = reader.u32(fskl_offset + 72) if fskl_offset >= 0 else 0
        print(
            f"BFRES={entry.name} animation={animation.name} "
            f"frames={animation.frame_count + 1} "
            f"fskl_flags=0x{skeleton_flags:08X} "
            f"scale_mode=0x{skeleton_flags & 0x300:03X}"
        )

        for model in models:
            if model.skeleton is None:
                continue

            bones_by_name = {
                bone.name: (index, bone)
                for index, bone in enumerate(model.skeleton.bones)
            }
            print(f"MODEL={model.name} bones={len(bones_by_name)}")

            for track in animation.bones:
                scale_curves = tuple(
                    track.curve(f"scale_{component}") for component in "xyz"
                )
                index_and_bone = bones_by_name.get(track.name)

                if index_and_bone is None:
                    continue

                index, bone = index_and_bone
                parent_name = (
                    model.skeleton.bones[bone.parent_index].name
                    if 0 <= bone.parent_index < len(model.skeleton.bones)
                    else "<root>"
                )
                has_scale_curve = any(curve is not None for curve in scale_curves)
                nonunit_rest = any(abs(value - 1.0) > 1e-6 for value in bone.scale)
                nonunit_base = track.base_scale is not None and any(
                    abs(value - 1.0) > 1e-6 for value in track.base_scale
                )

                if not (
                    has_scale_curve
                    or nonunit_rest
                    or nonunit_base
                    or track.segment_scale_compensate
                ):
                    continue

                component_ranges = []

                for component, curve in zip("xyz", scale_curves):
                    if curve is None:
                        component_ranges.append(f"{component}=<base>")
                        continue

                    values = tuple(
                        curve.evaluate(float(frame))
                        for frame in range(animation.frame_count + 1)
                    )
                    component_ranges.append(
                        f"{component}={min(values):.6g}..{max(values):.6g}"
                    )

                print(
                    f"  [{index:03d}] {track.name} parent={parent_name} "
                    f"bone_flags=0x{bone.flags:08X} "
                    f"ssc={int(track.segment_scale_compensate)} "
                    f"rest=({_ranges(bone.scale)}) "
                    f"base={track.base_scale} "
                    + " ".join(component_ranges)
                )

        return

    raise SystemExit(
        f"Animation {animation_name!r} was not found in {archive_path.name}."
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 2:
        raise SystemExit(
            "Usage: audit_bfres_scale.py -- ARCHIVE.szs ANIMATION_NAME"
        )

    run(Path(arguments[0]).resolve(), arguments[1])
