from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace
import json
import math
import sys

import bpy



ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.model_expectation import assess_model_expectation
from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.placement_classifier import (
    ClassifiedPlacement,
    PlacementCategory,
)
from smo_kingdom_importer.stage_data import StagePlacement, Vector3
from smo_kingdom_importer.static_model_import import (
    MeshRigBinding,
    SMO_OT_import_static_models,
    _bone_matrix_to_blender,
    _create_mesh_data,
    _remove_generated_objects,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _placement(identifier: str, x: float) -> StagePlacement:
    return StagePlacement(
        identifier=identifier,
        unit_config_name="Kuribo",
        model_name=None,
        category="ObjList",
        stage_layer="Map",
        placement_file_name="Map",
        layer_config_name="Map",
        translate=Vector3(x, 200.0, 300.0),
        rotate=Vector3(0.0, 0.0, 0.0),
        scale=Vector3(1.0, 1.0, 1.0),
        rotation_quaternion=(0.0, 0.0, 0.0, 1.0),
        links={},
        unit_config={"ParameterConfigName": ""},
        is_link_destination=False,
        is_root=True,
        raw={},
        source_stage_name="RiggingRegressionStage",
    )


def _vector_difference(left: object, right: object) -> float:
    return max(
        abs(float(a) - float(b))
        for a, b in zip(left, right)
    )


def _test_standalone_rigging(
    romfs_root: Path,
    archive_path: Path,
) -> tuple[int, int]:
    addon.register()
    original_preferences = addon.get_addon_preferences

    try:
        addon.get_addon_preferences = lambda _context=None: SimpleNamespace(
            apply_custom_normals=False,
            import_armatures=True,
            use_texture_cache=False,
            texture_cache_parent="",
            romfs_path=str(romfs_root),
        )
        result = bpy.ops.smo.import_test_model(
            filepath=str(archive_path),
            use_selected_stage_textures=False,
        )
        check(
            result == {"FINISHED"},
            f"Standalone rig import returned {result}",
        )
        collection = bpy.data.collections.get("SMO Test - Kuribo")
        check(collection is not None, "Standalone rig collection is missing")
        root = collection.objects.get("Kuribo Test Root")
        check(root is not None, "Standalone rig root is missing")
        armatures = tuple(
            obj
            for obj in collection.objects
            if obj.type == "ARMATURE"
            and obj.get("smo_armature_generated")
        )
        meshes = tuple(
            obj
            for obj in collection.objects
            if obj.type == "MESH" and obj.get("smo_rigged")
        )
        check(
            len(armatures) == root["smo_armature_object_count"] == 1,
            "Standalone rig armature count is incorrect",
        )
        check(
            len(meshes) == root["smo_rigged_mesh_object_count"] == 12,
            "Standalone rigged-mesh count is incorrect",
        )
        check(
            all(
                obj.parent is armatures[0]
                and obj.modifiers
                and obj.modifiers[0].object is armatures[0]
                for obj in meshes
            ),
            "Standalone rig parenting or modifiers are incomplete",
        )
        check(
            root["smo_armatures_enabled"]
            and not json.loads(root["smo_rig_errors"]),
            "Standalone rig import recorded an unexpected fallback",
        )
        return len(armatures), len(meshes)
    finally:
        addon.get_addon_preferences = original_preferences
        collection = bpy.data.collections.get("SMO Test - Kuribo")

        if collection is not None:
            _remove_generated_objects(
                tuple(
                    obj
                    for obj in collection.objects
                    if obj.get("smo_test_import")
                )
            )
            bpy.data.collections.remove(collection)
        addon.unregister()


def run(romfs_root: Path) -> None:
    archive_path = romfs_root / "ObjectData" / "Kuribo.szs"
    archive = read_szs(archive_path)
    bfres_file = next(
        entry
        for entry in archive.get_files()
        if entry.name and entry.name.casefold().endswith(".bfres")
    )
    models = read_static_bfres(
        bytes(bfres_file.data),
        include_rigging=True,
    )
    check(len(models) == 1, "Kuribo BFRES model count changed")
    model = models[0]
    static_model = read_static_bfres(bytes(bfres_file.data))[0]
    check(
        static_model.skeleton is None
        and all(not mesh.bone_weights for mesh in static_model.meshes),
        "Default BFRES parsing retained opt-in rig payloads",
    )
    check(
        tuple(mesh.vertices for mesh in static_model.meshes)
        == tuple(mesh.vertices for mesh in model.meshes),
        "Opt-in rig parsing changed the static bind-pose geometry",
    )
    check(model.skeleton is not None, "Kuribo FSKL was not exposed")
    check(len(model.skeleton.bones) == 22, "Kuribo bone count changed")
    check(
        all(
            bone.parent_index == -1
            or 0 <= bone.parent_index < len(model.skeleton.bones)
            for bone in model.skeleton.bones
        ),
        "Kuribo skeleton contains an invalid parent index",
    )

    weighted_meshes = tuple(
        mesh for mesh in model.meshes if mesh.bone_weights
    )
    check(weighted_meshes, "Kuribo exposes no weighted meshes")
    check(
        any(mesh.skin_influence_count == 2 for mesh in weighted_meshes),
        "Kuribo no longer covers blended two-weight skinning",
    )

    for mesh in weighted_meshes:
        check(
            len(mesh.bone_weights) == len(mesh.vertices),
            f"{mesh.name} weight count differs from its vertex count",
        )

        for vertex_weights in mesh.bone_weights:
            check(
                1 <= len(vertex_weights) <= 4,
                f"{mesh.name} has an invalid influence count",
            )
            check(
                math.isclose(
                    sum(weight for _bone, weight in vertex_weights),
                    1.0,
                    abs_tol=1e-6,
                ),
                f"{mesh.name} weights are not normalized",
            )

    representative_rigs = {
        "BossKnuckleBody": (41, 4),
        "MofumofuBody": (35, 1),
        "SnowManRacer": (47, 4),
        "CityMan": (35, 4),
        "Wanwan": (19, 3),
    }

    for asset_name, (bone_count, maximum_influences) in (
        representative_rigs.items()
    ):
        asset_archive = read_szs(
            romfs_root / "ObjectData" / f"{asset_name}.szs"
        )
        asset_bfres = next(
            entry
            for entry in asset_archive.get_files()
            if entry.name and entry.name.casefold().endswith(".bfres")
        )
        asset_model = read_static_bfres(
            bytes(asset_bfres.data),
            include_rigging=True,
        )[0]
        check(
            asset_model.skeleton is not None
            and len(asset_model.skeleton.bones) == bone_count,
            f"{asset_name} skeleton coverage changed",
        )
        observed_maximum = max(
            (
                len(weights)
                for mesh in asset_model.meshes
                for weights in mesh.bone_weights
            ),
            default=0,
        )
        check(
            observed_maximum == maximum_influences,
            f"{asset_name} influence coverage changed: "
            f"{observed_maximum} != {maximum_influences}",
        )

    source_mesh = next(
        mesh for mesh in weighted_meshes if mesh.skin_influence_count == 2
    )
    material = bpy.data.materials.new("SMO Rigging Regression Material")
    blender_mesh = _create_mesh_data(
        source_mesh,
        "Kuribo",
        material,
        False,
    )
    parent = bpy.data.collections.new("SMO Rigging Regression")
    bpy.context.scene.collection.children.link(parent)
    root = bpy.data.objects.new("SMO Rigging Regression Root", None)
    parent.objects.link(root)
    resource = ObjectDataIndex(romfs_root).resolve(_placement("Resolve", 0.0))
    check(resource.has_model, "Kuribo no longer resolves to a BFRES")
    binding = MeshRigBinding(
        rig_key="kuribo-rigging-regression",
        model_name=model.name,
        armature_name="Kuribo_Armature",
        source_archive=resource.archive_path,
        source_bfres=resource.bfres_files[0],
        skeleton=model.skeleton,
        bone_weights=source_mesh.bone_weights,
    )
    operator = SimpleNamespace()
    operator._armature_for_binding = MethodType(
        SMO_OT_import_static_models._armature_for_binding,
        operator,
    )
    operator._collection = parent
    operator._root = root
    operator._group_scope = "Rigging Regression"
    operator._import_stage_lighting = False
    operator._mesh_rig_bindings = {blender_mesh.name: binding}
    operator._armature_data_cache = {}
    operator._rig_errors = {}
    operator._mesh_object_count = 0
    operator._armature_object_count = 0
    operator._rigged_mesh_object_count = 0
    operator._model_placement_count = 0
    operator._procedural_ocean_count = 0
    operator._fallback_count = 0
    operator._model_expectation_counts = {}
    operator._asset_errors = {}
    operator._meshes_for_resource = (
        lambda _classified, _resource: (blender_mesh,)
    )

    for identifier, x in (("RigA", 100.0), ("RigB", 500.0)):
        placement = _placement(identifier, x)
        classified = ClassifiedPlacement(
            placement=placement,
            category=PlacementCategory.CHARACTERS,
            resource=resource,
            model_expectation=assess_model_expectation(
                unit_config_name=placement.unit_config_name,
                resource=resource,
            ),
        )
        SMO_OT_import_static_models._process_placement(
            operator,
            classified,
        )

    armatures = tuple(
        obj
        for obj in bpy.data.objects
        if obj.parent is root
        and obj.type == "ARMATURE"
        and obj.get("smo_armature_generated")
    )
    rigged_objects = tuple(
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH"
        and obj.parent in armatures
        and obj.get("smo_rigged")
    )
    check(len(armatures) == 2, "Stage path did not create two armatures")
    check(len(rigged_objects) == 2, "Stage path did not create two rigged meshes")
    check(
        armatures[0].data is armatures[1].data,
        "Repeated placements did not share armature rest data",
    )
    check(
        all(obj.data is blender_mesh for obj in rigged_objects),
        "Repeated placements did not share Blender mesh data",
    )
    check(
        operator._armature_object_count == 2
        and operator._rigged_mesh_object_count == 2
        and operator._model_placement_count == 2,
        "Rigging aggregate counts are incorrect",
    )
    check(
        sorted(
            tuple(round(value, 6) for value in armature.location)
            for armature in armatures
        )
        == [(1.0, -3.0, 2.0), (5.0, -3.0, 2.0)],
        "Stage transform was not applied to the armatures",
    )
    check(
        all(
            obj.get("smo_representation") == "RIGGED_MODEL"
            and obj.modifiers
            and obj.modifiers[0].type == "ARMATURE"
            for obj in rigged_objects
        ),
        "Rigged placement metadata or modifiers are incomplete",
    )

    armature_a = next(
        armature
        for armature in armatures
        if round(armature.location.x, 6) == 1.0
    )
    armature_b = next(
        armature
        for armature in armatures
        if armature is not armature_a
    )
    object_a = next(obj for obj in rigged_objects if obj.parent is armature_a)
    object_b = next(obj for obj in rigged_objects if obj.parent is armature_b)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    base = tuple(blender_mesh.vertices[0].co)
    rest_a = tuple(object_a.evaluated_get(depsgraph).data.vertices[0].co)
    rest_b = tuple(object_b.evaluated_get(depsgraph).data.vertices[0].co)
    check(
        _vector_difference(rest_a, base) < 1e-5
        and _vector_difference(rest_b, base) < 1e-5,
        "Armature rest pose changed the static bind-pose geometry",
    )
    check(
        armature_a.data["smo_rest_matrix_revision"] == 4,
        "Armature rest-matrix revision metadata is missing",
    )
    check(
        armature_a.data["smo_segment_scale_compensate"],
        "Armature lost the FSKL Maya scale mode",
    )
    check(
        all(
            armature.get("smo_source_archive") == str(resource.archive_path)
            and armature.get("smo_source_bfres") == resource.bfres_files[0]
            for armature in armatures
        ),
        "Stage armatures lost their exact archive or BFRES source metadata",
    )

    for source_bone in model.skeleton.bones:
        expected_matrix = _bone_matrix_to_blender(source_bone.model_matrix)
        actual_matrix = armature_a.data.bones[
            source_bone.name
        ].matrix_local
        translation_error = (
            actual_matrix.translation - expected_matrix.translation
        ).length
        rotation_delta = actual_matrix.to_quaternion().rotation_difference(
            expected_matrix.to_quaternion()
        )
        rotation_error = abs(float(rotation_delta.angle)) % (
            2.0 * math.pi
        )
        rotation_error = min(
            rotation_error,
            2.0 * math.pi - rotation_error,
        )
        check(
            translation_error < 1e-5 and rotation_error < 1e-5,
            f"Armature rest matrix changed for {source_bone.name}: "
            f"translation={translation_error}, rotation={rotation_error}",
        )

    bone_index = source_mesh.bone_weights[0][0][0]
    bone_name = model.skeleton.bones[bone_index].name
    pose_bone = armature_a.pose.bones[bone_name]
    pose_bone.rotation_mode = "XYZ"
    pose_bone.rotation_euler.x = 0.35
    depsgraph.update()
    posed_a = tuple(object_a.evaluated_get(depsgraph).data.vertices[0].co)
    unchanged_b = tuple(object_b.evaluated_get(depsgraph).data.vertices[0].co)
    check(
        _vector_difference(posed_a, rest_a) > 1e-4,
        "Posing an imported bone did not deform its mesh",
    )
    check(
        _vector_difference(unchanged_b, rest_b) < 1e-5,
        "Shared armature data leaked pose state between placements",
    )

    invalid_binding = MeshRigBinding(
        rig_key="kuribo-invalid-rigging-regression",
        model_name=model.name,
        armature_name="Kuribo_Invalid_Armature",
        skeleton=model.skeleton,
        bone_weights=source_mesh.bone_weights[:-1],
    )
    operator._mesh_rig_bindings[blender_mesh.name] = invalid_binding
    fallback_placement = _placement("RigFallback", 900.0)
    fallback_classified = ClassifiedPlacement(
        placement=fallback_placement,
        category=PlacementCategory.CHARACTERS,
        resource=resource,
        model_expectation=assess_model_expectation(
            unit_config_name=fallback_placement.unit_config_name,
            resource=resource,
        ),
    )
    SMO_OT_import_static_models._process_placement(
        operator,
        fallback_classified,
    )
    fallback_object = next(
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH"
        and obj.parent is root
        and obj.get("smo_id") == "RigFallback"
    )
    check(
        fallback_object.get("smo_representation") == "STATIC_MODEL"
        and not fallback_object.modifiers,
        "Malformed skin data did not fall back to the static bind pose",
    )
    check(
        invalid_binding.rig_key in operator._rig_errors
        and invalid_binding.rig_key not in operator._armature_data_cache,
        "Malformed rig diagnostics or cache cleanup are incomplete",
    )
    check(
        operator._armature_object_count == 2
        and operator._rigged_mesh_object_count == 2
        and operator._mesh_object_count == 3
        and operator._model_placement_count == 3,
        "Malformed-rig fallback corrupted aggregate counts",
    )

    _remove_generated_objects(
        (*rigged_objects, *armatures, fallback_object)
    )
    bpy.data.objects.remove(root, do_unlink=True)

    for child in tuple(parent.children):
        bpy.data.collections.remove(child)
    bpy.data.collections.remove(parent)

    if material.users == 0:
        bpy.data.materials.remove(material)

    standalone_armatures, standalone_meshes = _test_standalone_rigging(
        romfs_root,
        archive_path,
    )

    print(
        "RIGGING_REGRESSION: PASS "
        f"bones={len(model.skeleton.bones)} "
        f"weighted_meshes={len(weighted_meshes)} "
        f"armatures={operator._armature_object_count} "
        f"standalone={standalone_armatures}/{standalone_meshes} "
        f"coverage={len(representative_rigs) + 1}"
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: rigging_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())
