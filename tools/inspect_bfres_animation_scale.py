from __future__ import annotations

from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_animation import read_skeletal_animations
from smo_kingdom_importer.bfres_animation_import import (
    AnimationClip,
    AnimationSource,
    import_bfres_animation,
)
from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.static_model_import import _create_armature_object
from smo_kingdom_importer.world_list import read_szs


def _bfres(path: Path, requested_name: str = "") -> tuple[str, bytes]:
    archive = read_szs(path)
    entries = tuple(
        entry
        for entry in archive.get_files()
        if Path(entry.name).suffix.casefold() == ".bfres"
    )
    if requested_name:
        entries = tuple(entry for entry in entries if entry.name == requested_name)
    if len(entries) != 1:
        raise RuntimeError(
            f"Expected one BFRES in {path.name}, found {[entry.name for entry in entries]}"
        )
    return entries[0].name, bytes(entries[0].data)


def _volume_scale(matrix) -> float:
    return abs(float(matrix.to_3x3().determinant())) ** (1.0 / 3.0)


def run(
    model_archive: Path,
    model_bfres: str,
    animation_archive: Path,
    animation_name: str,
) -> None:
    model_name, model_data = _bfres(model_archive, model_bfres)
    animation_bfres, animation_data = _bfres(animation_archive)
    models = read_static_bfres(model_data, include_rigging=True)
    model = next(model for model in models if model.skeleton is not None)
    skeleton = model.skeleton
    assert skeleton is not None
    animation = next(
        item
        for item in read_skeletal_animations(animation_data)
        if item.name == animation_name
    )

    rest_by_name = {bone.name: bone for bone in skeleton.bones}
    print(
        f"MODEL={model_name}:{model.name} ANIMATION={animation_bfres}:{animation.name} "
        f"FRAMES={animation.frame_count + 1} BONES={len(animation.bones)} "
        f"SKELETON_SSC={skeleton.segment_scale_compensate} "
        f"EULER={animation.euler_xyz}"
    )
    print("ANIMATED SCALE TRACKS")
    for bone_animation in animation.bones:
        scale_curves = tuple(
            curve
            for curve in bone_animation.curves
            if curve.data_name.startswith("scale_")
        )
        if bone_animation.base_scale is None and not scale_curves:
            continue
        rest = rest_by_name.get(bone_animation.name)
        rest_scale = rest.scale if rest is not None else None
        components = []
        for curve in scale_curves:
            values = tuple(curve.evaluate(float(frame)) for frame in range(animation.frame_count + 1))
            components.append(
                f"{curve.data_name}={min(values):.6g}..{max(values):.6g}"
            )
        print(
            f"  {bone_animation.name}: rest={rest_scale} base={bone_animation.base_scale} "
            f"ssc={bone_animation.segment_scale_compensate} {' '.join(components)}"
        )

    collection = bpy.data.collections.new("SMO Animation Scale Diagnostic")
    bpy.context.scene.collection.children.link(collection)
    armature, _ = _create_armature_object(
        collection,
        "SMO_Animation_Scale_Diagnostic_Armature",
        skeleton,
    )
    armature["smo_armature_generated"] = True
    armature["smo_source_archive"] = str(model_archive)
    armature["smo_source_bfres"] = model_name
    armature["smo_source_model"] = model.name
    armature["smo_rig_key"] = "animation-scale-diagnostic"
    clip = AnimationClip(skeletal=animation, visibility=None)
    source = AnimationSource(
        archive_path=animation_archive,
        bfres_name=animation_bfres,
        data=animation_data,
        skeletal_animations=(animation,),
        visibility_animations=(),
        animations=(clip,),
    )
    import_bfres_animation(armature, source, clip)

    root_names = tuple(
        bone.name for bone in armature.pose.bones if bone.parent is None
    )
    watched_names = tuple(
        name
        for name in (
            *root_names,
            "Root",
            "Hip",
            "HipTrans",
            "Spine1",
            "Head",
            "Cap",
        )
        if name in armature.pose.bones
    )
    watched_names = tuple(dict.fromkeys(watched_names))
    extrema = {
        name: [float("inf"), float("-inf"), -1, -1]
        for name in watched_names
    }
    for frame in range(animation.frame_count + 1):
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        for name in watched_names:
            value = _volume_scale(armature.pose.bones[name].matrix)
            item = extrema[name]
            if value < item[0]:
                item[0], item[2] = value, frame
            if value > item[1]:
                item[1], item[3] = value, frame
    print("IMPORTED WORLD VOLUME SCALE")
    for name, (minimum, maximum, minimum_frame, maximum_frame) in extrema.items():
        print(
            f"  {name}: {minimum:.6g}@{minimum_frame}..{maximum:.6g}@{maximum_frame}"
        )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(arguments) != 4:
        raise SystemExit(
            "Usage: inspect_bfres_animation_scale.py -- "
            "MODEL_ARCHIVE MODEL_BFRES ANIMATION_ARCHIVE ANIMATION_NAME"
        )
    run(
        Path(arguments[0]).resolve(),
        arguments[1],
        Path(arguments[2]).resolve(),
        arguments[3],
    )
