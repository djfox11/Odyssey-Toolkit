from __future__ import annotations

from dataclasses import replace
import json
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
    _SCALE_INHERITANCE_PROPERTY,
    _rest_transform,
    _sample_local_matrix,
    sync_active_action_scale_inheritance,
    import_bfres_animation,
)
from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.static_model_import import _create_armature_object
from smo_kingdom_importer.world_list import read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _world_volume_scale(armature: bpy.types.Object, bone_name: str) -> float:
    determinant = float(
        armature.pose.bones[bone_name].matrix.to_3x3().determinant()
    )
    return abs(determinant) ** (1.0 / 3.0)


def run(romfs_root: Path) -> None:
    archive_path = romfs_root / "ObjectData" / "KoopaHack.szs"
    archive = read_szs(archive_path)
    entry = next(
        item for item in archive.get_files() if item.name == "KoopaHack.bfres"
    )
    data = bytes(entry.data)
    model = read_static_bfres(data, include_rigging=True)[0]
    check(model.skeleton is not None, "KoopaHack has no parsed skeleton")
    check(
        model.skeleton.segment_scale_compensate,
        "KoopaHack's Maya FSKL scale mode was not preserved",
    )

    animation = next(
        item for item in read_skeletal_animations(data) if item.name == "Jump3"
    )
    clip = AnimationClip(skeletal=animation, visibility=None)
    source = AnimationSource(
        archive_path=archive_path,
        bfres_name=entry.name,
        data=data,
        skeletal_animations=(animation,),
        visibility_animations=(),
        animations=(clip,),
    )
    collection = bpy.data.collections.new("SMO Scale Compensation Regression")
    bpy.context.scene.collection.children.link(collection)
    armature, _bone_names = _create_armature_object(
        collection,
        "KoopaHack_Scale_Regression_Armature",
        model.skeleton,
    )
    armature["smo_armature_generated"] = True
    armature["smo_source_archive"] = str(archive_path)
    armature["smo_source_bfres"] = entry.name
    armature["smo_source_model"] = model.name
    armature["smo_rig_key"] = "koopa-hack-scale-regression"

    check(
        armature.data["smo_rest_matrix_revision"] == 4,
        "Corrected BFRES scale-inheritance revision is missing",
    )
    check(
        armature.data["smo_segment_scale_compensate"],
        "Armature lost the FSKL segment-scale setting",
    )

    action = import_bfres_animation(armature, source, clip)
    check(
        action["smo_segment_scale_compensate"],
        "Action does not record skeleton-wide segment-scale compensation",
    )
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()

    tracked_bones = {bone.name: bone for bone in animation.bones}
    check(
        all(
            tracked_bones[name].segment_scale_compensate
            for name in ("ArmR1", "ArmR2", "BraceR", "WristR", "FingerRA1")
        ),
        "Jump3's right-arm tracks lost their FSKA segment-scale compensation",
    )
    expected_scales = {
        "ArmR1": 1.10,
        "BraceR": 1.20,
        "WristR": 1.35,
        "FingerRA1": 1.35,
    }
    actual_scales = {
        name: _world_volume_scale(armature, name)
        for name in expected_scales
    }

    for name, expected in expected_scales.items():
        check(
            abs(actual_scales[name] - expected) < 2e-3,
            f"{name} did not cancel its parent's animated scale: "
            f"expected {expected}, found {actual_scales[name]}",
        )

    check(
        _SCALE_INHERITANCE_PROPERTY in action,
        "Action does not store its per-bone BFRES scale-inheritance modes",
    )
    check(
        action["smo_scale_inheritance_revision"] == 3,
        "Action does not use helper-bone direct-parent scale compensation",
    )
    inheritance = json.loads(action[_SCALE_INHERITANCE_PROPERTY])
    check(
        inheritance["ArmR1"] == "FULL",
        "Jump3 did not store baked full inheritance for ArmR1",
    )
    opposite = action.copy()
    opposite.name = "Scale inheritance switch regression"
    opposite[_SCALE_INHERITANCE_PROPERTY] = json.dumps({"ArmR1": "NONE"})
    armature.animation_data.action = opposite
    sync_active_action_scale_inheritance()
    check(
        armature.data.bones["ArmR1"].inherit_scale == "NONE",
        "Switching Actions did not apply the stored inheritance mode",
    )
    armature.animation_data.action = action
    sync_active_action_scale_inheritance()
    check(
        armature.data.bones["ArmR1"].inherit_scale == "FULL",
        "Switching back to Jump3 did not restore baked full inheritance",
    )
    bpy.data.actions.remove(opposite)

    bowser_archive_path = romfs_root / "ObjectData" / "KoopaAnimation.szs"
    bowser_archive = read_szs(bowser_archive_path)
    bowser_entry = next(
        item
        for item in bowser_archive.get_files()
        if item.name == "KoopaAnimation.bfres"
    )
    opening = next(
        item
        for item in read_skeletal_animations(bytes(bowser_entry.data))
        if item.name == "DemoOpening01"
    )
    opening_tracks = {bone.name: bone for bone in opening.bones}
    bowser_hand = (
        "ArmR1",
        "ArmR2",
        "WristR",
        "FingerRA1",
        "FingerRA2",
        "FingerRA3",
    )
    check(
        all(opening_tracks[name].segment_scale_compensate for name in bowser_hand),
        "DemoOpening01's punch hierarchy lost segment-scale compensation",
    )

    bowser_model_archive_path = romfs_root / "ObjectData" / "Koopa.szs"
    bowser_model_archive = read_szs(bowser_model_archive_path)
    bowser_model_entry = next(
        item
        for item in bowser_model_archive.get_files()
        if item.name == "Koopa.bfres"
    )
    bowser_model = read_static_bfres(
        bytes(bowser_model_entry.data),
        include_rigging=True,
    )[0]
    check(bowser_model.skeleton is not None, "Koopa has no parsed skeleton")
    bowser_armature, _bowser_bone_names = _create_armature_object(
        collection,
        "Koopa_Opening_Scale_Regression_Armature",
        bowser_model.skeleton,
    )
    bowser_armature["smo_armature_generated"] = True
    bowser_armature["smo_source_archive"] = str(bowser_model_archive_path)
    bowser_armature["smo_source_bfres"] = bowser_model_entry.name
    bowser_armature["smo_source_model"] = bowser_model.name
    bowser_armature["smo_rig_key"] = "koopa-opening-scale-regression"
    opening_sample = replace(opening, frame_count=120)
    opening_clip = AnimationClip(
        skeletal=opening_sample,
        visibility=None,
    )
    opening_source = AnimationSource(
        archive_path=bowser_archive_path,
        bfres_name=bowser_entry.name,
        data=bytes(bowser_entry.data),
        skeletal_animations=(opening_sample,),
        visibility_animations=(),
        animations=(opening_clip,),
    )
    import_bfres_animation(bowser_armature, opening_source, opening_clip)
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    opening_scales = {
        name: _world_volume_scale(bowser_armature, name)
        for name in ("Root", "Center", "Spine", "Face")
    }

    for name, actual in opening_scales.items():
        check(
            abs(actual - 0.5) < 2e-3,
            f"DemoOpening01 {name} lost the whole-character Root scale: "
            f"expected 0.5, found {actual}",
        )

    bpy.context.scene.frame_set(54)
    bpy.context.view_layer.update()
    punch_scales = {
        name: _world_volume_scale(bowser_armature, name)
        for name in ("WristR", "FingerRA1", "FingerRA2", "FingerRA3")
    }

    for name, actual in punch_scales.items():
        check(
            abs(actual - 1.25) < 3e-3,
            f"DemoOpening01 {name} lost Bowser's Root=0.5 scale during "
            f"the punch: expected 1.25, found {actual}",
        )

    bowser_rest = {
        pose_bone.name: _rest_transform(pose_bone)
        for pose_bone in bowser_armature.pose.bones
        if "smo_bone_index" in pose_bone.bone
    }
    worst_uniform_spread = 0.0
    worst_uniform_sample = None

    for frame in range(opening_sample.frame_count + 1):
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()

        for name, track in opening_tracks.items():
            if name not in bowser_rest or name not in bowser_armature.pose.bones:
                continue

            _local, source_scale = _sample_local_matrix(
                track,
                float(frame),
                bowser_rest[name],
                opening_sample.euler_xyz,
            )

            if max(source_scale) - min(source_scale) > 1e-6:
                continue

            pose_scale = tuple(
                float(value) for value in bowser_armature.pose.bones[name].scale
            )
            spread = max(pose_scale) - min(pose_scale)

            if spread > worst_uniform_spread:
                worst_uniform_spread = spread
                worst_uniform_sample = (frame, name, source_scale, pose_scale)

    check(
        worst_uniform_spread < 2e-6,
        "DemoOpening01 turned an authored uniform scale into axis stretch: "
        f"{worst_uniform_sample}",
    )

    print(
        "BFRES_SCALE_COMPENSATION_REGRESSION: PASS "
        + " ".join(
            f"{name}={actual_scales[name]:.4f}" for name in expected_scales
        )
        + f" uniform_spread={worst_uniform_spread:.9f}"
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: bfres_scale_compensation_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())
