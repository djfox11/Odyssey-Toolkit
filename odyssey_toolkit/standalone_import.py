from __future__ import annotations

import json
from pathlib import Path
import re
import time
import traceback
from typing import Any

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from .performance import (
    PerformanceTimings,
    print_performance_summary,
    reset_active_timings,
    set_active_timings,
)
from .resource_rules import texture_archive_rule_names
from .static_model_import import (
    ImageCacheKey,
    MeshRigBinding,
    SMO_OT_import_static_models,
    _apply_skin_binding,
    _create_armature_object,
    _create_mesh_data,
    _identity_digest,
    _model_has_deformable_skeleton,
    _remove_generated_objects,
    _stage_texture_archive_names,
)


class SMO_OT_import_test_model(Operator):
    bl_idname = "smo.import_test_model"
    bl_label = "Import Standalone Model"
    bl_description = (
        "Import one SZS or BFRES at the origin for model and material testing"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(
        default="*.szs;*.bfres",
        options={"HIDDEN"},
    )
    use_selected_stage_textures: BoolProperty(
        name="Search StageTexture Archives",
        description=(
            "Infer matching StageTexture archives from the model name and "
            "use the selected stage as a fallback"
        ),
        default=True,
    )

    _load_texture_archive = SMO_OT_import_static_models._load_texture_archive
    _decode_texture = SMO_OT_import_static_models._decode_texture
    _load_shared_texture_archive = (
        SMO_OT_import_static_models._load_shared_texture_archive
    )
    _material_for_mesh = SMO_OT_import_static_models._material_for_mesh

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def _shared_texture_paths(
        self,
        context: bpy.types.Context,
        model_path: Path,
    ) -> tuple[Path, ...]:
        paths = []
        adjacent = model_path.with_name(f"{model_path.stem}Texture.szs")

        if adjacent.is_file():
            paths.append(adjacent)

        if not self.use_selected_stage_textures:
            return tuple(paths)

        from . import _WORLD_BY_STAGE

        settings = getattr(context.scene, "smo_settings", None)
        world = (
            _WORLD_BY_STAGE.get(str(settings.kingdom))
            if settings is not None
            else None
        )
        object_data_dir = (
            model_path.parent
            if model_path.parent.name.casefold() == "objectdata"
            else None
        )

        if object_data_dir is None and settings is not None:
            try:
                from . import resolve_romfs_root

                object_data_dir = (
                    resolve_romfs_root(settings.romfs_path) / "ObjectData"
                )
            except Exception:
                print(
                    "[Odyssey Toolkit] Could not resolve ObjectData "
                    "for standalone shared textures:"
                )
                traceback.print_exc()

        if object_data_dir is None:
            return tuple(paths)

        model_family_match = re.match(
            r"^([A-Za-z0-9]+World)",
            model_path.stem,
        )
        model_family = (
            model_family_match.group(1)
            if model_family_match is not None
            else ""
        )
        rule_archive_found = False

        for archive_name in texture_archive_rule_names(model_path.stem):
            rule_path = object_data_dir / archive_name

            if rule_path.is_file():
                rule_archive_found = True

                if rule_path not in paths:
                    paths.append(rule_path)

        inferred = (
            None
            if rule_archive_found
            else self._infer_stage_texture(
                object_data_dir,
                model_path.stem,
                model_family,
            )
        )

        if inferred is not None and inferred not in paths:
            print(
                "[Odyssey Toolkit] Inferred shared textures from "
                f"{inferred.name}",
                flush=True,
            )
            paths.append(inferred)

        if world is not None:
            selected_stage_name = str(world["Name"])
            selected_family_matches = (
                not model_family
                or selected_stage_name.startswith(model_family)
            )
            for archive_name in _stage_texture_archive_names(
                selected_stage_name
            ):
                stage_texture = object_data_dir / archive_name

                if (
                    selected_family_matches
                    and stage_texture.is_file()
                    and stage_texture not in paths
                ):
                    paths.append(stage_texture)

        return tuple(paths)

    @staticmethod
    def _infer_stage_texture(
        object_data_dir: Path,
        model_name: str,
        model_family: str,
    ) -> Path | None:
        candidates = tuple(object_data_dir.glob("*Texture.szs"))

        if not candidates:
            return None

        folded_model = model_name.casefold()

        def rank(path: Path) -> tuple[int, int]:
            stage_name = path.stem.removesuffix("Texture").casefold()
            common_length = 0

            for left, right in zip(folded_model, stage_name):
                if left != right:
                    break

                common_length += 1

            is_home = int(stage_name == f"{model_family.casefold()}homestage")
            return common_length, is_home

        ranked = sorted(
            candidates,
            key=lambda path: (rank(path), path.name.casefold()),
            reverse=True,
        )

        if rank(ranked[0])[0] < 4:
            return None

        if len(ranked) > 1 and rank(ranked[0]) == rank(ranked[1]):
            return None

        return ranked[0]

    @staticmethod
    def _bfres_files(model_path: Path) -> tuple[tuple[str, bytes], ...]:
        if model_path.suffix.casefold() == ".bfres":
            data = model_path.read_bytes()

            if not data.startswith(b"FRES"):
                raise ValueError(
                    f"{model_path.name} is not an uncompressed BFRES file."
                )

            return ((model_path.name, data),)

        if model_path.suffix.casefold() != ".szs":
            raise ValueError("Choose an .szs or .bfres file.")

        from .world_list import read_szs

        archive = read_szs(model_path)
        return tuple(
            (entry.name, bytes(entry.data))
            for entry in archive.get_files()
            if entry.name
            and Path(entry.name).suffix.casefold() == ".bfres"
        )

    @staticmethod
    def _test_collection(
        context: bpy.types.Context,
        asset_name: str,
    ) -> bpy.types.Collection:
        collection_name = f"SMO Test - {asset_name}"
        collection = bpy.data.collections.get(collection_name)

        if collection is not None and not collection.get("smo_test_import"):
            collection = next(
                (
                    candidate
                    for candidate in bpy.data.collections
                    if candidate.get("smo_test_import")
                    and (
                        candidate.get("smo_test_asset_name") == asset_name
                        or candidate.name.startswith(f"{collection_name}.")
                    )
                ),
                None,
            )

        if collection is None:
            collection = bpy.data.collections.new(collection_name)
            context.scene.collection.children.link(collection)
        elif context.scene.collection.children.get(collection.name) is None:
            context.scene.collection.children.link(collection)

        collection["smo_test_import"] = True
        collection["smo_test_asset_name"] = asset_name
        collection.color_tag = "COLOR_07"
        return collection

    def execute(self, context: bpy.types.Context) -> set[str]:
        created_objects: list[bpy.types.Object] = []
        timings = PerformanceTimings()
        import_started = time.perf_counter()
        preparation_started = import_started
        timing_token = set_active_timings(timings)

        try:
            model_path = Path(bpy.path.abspath(self.filepath)).resolve()

            if not model_path.is_file():
                raise FileNotFoundError(f"Model file not found: {model_path}")

            bfres_files = self._bfres_files(model_path)

            if not bfres_files:
                raise ValueError(
                    f"{model_path.name} does not contain a BFRES file."
                )

            from . import get_addon_preferences, texture_cache_directory
            from .bfres_mesh import read_static_bfres
            from .texture_cache import PersistentTextureCache

            preferences = get_addon_preferences(context)
            apply_custom_normals = bool(
                getattr(preferences, "apply_custom_normals", False)
            )
            import_armatures = bool(
                getattr(preferences, "import_armatures", False)
            )
            self._experimental_cloth_nov = bool(
                getattr(preferences, "experimental_cloth_nov", False)
            )
            self._texture_cache_directory = texture_cache_directory(
                preferences
            )
            self._persistent_texture_cache = (
                PersistentTextureCache(self._texture_cache_directory)
                if bool(getattr(preferences, "use_texture_cache", False))
                else None
            )
            shared_texture_paths = self._shared_texture_paths(
                context,
                model_path,
            )
            self._archive_cache: dict[Path, Any] = {}
            self._texture_archive_cache: dict[tuple[Path, str], Any] = {}
            self._decoded_texture_cache: dict[
                tuple[tuple[Path, str], str],
                Any | None,
            ] = {}
            self._image_cache: dict[ImageCacheKey, bpy.types.Image | None] = {}
            self._material_cache: dict[Any, bpy.types.Material] = {}
            self._missing_albedo_textures: set[str] = set()
            self._fallback_display_textures: set[str] = set()
            self._texture_errors: dict[str, str] = {}

            collection = self._test_collection(context, model_path.stem)
            previous_generated = {
                obj
                for obj in collection.objects
                if obj.get("smo_test_import")
            }
            root_name = f"{model_path.stem} Test Root"
            root = bpy.data.objects.new(root_name, None)
            root.empty_display_type = "PLAIN_AXES"
            root["smo_test_import"] = True
            root["smo_source_file"] = str(model_path)
            root["smo_shared_texture_archives"] = json.dumps(
                [str(path) for path in shared_texture_paths]
            )
            collection.objects.link(root)
            created_objects.append(root)
            object_count = 0
            model_count = 0
            armature_count = 0
            rigged_mesh_count = 0
            rig_errors: dict[str, str] = {}
            timings.add(
                "preparation_total",
                time.perf_counter() - preparation_started,
            )

            for bfres_name, bfres_data in bfres_files:
                asset_key = (model_path, bfres_name)
                asset_name = Path(bfres_name).stem
                display_asset_name = (
                    model_path.stem
                    if model_path.suffix.casefold() == ".szs"
                    else asset_name
                )
                models = read_static_bfres(
                    bfres_data,
                    include_rigging=import_armatures,
                )
                model_count += len(models)

                for model_index, model in enumerate(models):
                    rig_key = _identity_digest(
                        model_path,
                        bfres_name,
                        model_index,
                        model.name,
                    )
                    armature_object = None
                    bone_names: tuple[str, ...] = ()
                    model_rigged_count = 0

                    if (
                        import_armatures
                        and _model_has_deformable_skeleton(model)
                    ):
                        try:
                            armature_name = (
                                f"{display_asset_name}_{model.name}"
                                "_Armature"
                            )
                            armature_object, bone_names = (
                                _create_armature_object(
                                    collection,
                                    armature_name,
                                    model.skeleton,
                                )
                            )
                            armature_object.parent = root
                            armature_object["smo_test_import"] = True
                            armature_object["smo_armature_generated"] = True
                            armature_object["smo_source_archive"] = str(
                                model_path
                            )
                            armature_object["smo_source_bfres"] = bfres_name
                            armature_object["smo_source_model"] = (
                                model.name
                            )
                            armature_object["smo_rig_key"] = rig_key
                            armature_object["smo_bone_count"] = len(
                                bone_names
                            )
                            created_objects.append(armature_object)
                            armature_count += 1
                        except Exception as exc:
                            rig_errors[model.name] = str(exc)
                            armature_object = None
                            print(
                                "[Odyssey Toolkit Standalone] "
                                "Armature creation failed; using static "
                                f"bind pose for {model.name}: {exc}"
                            )

                    for source_mesh in model.meshes:
                        material = self._material_for_mesh(
                            source_mesh,
                            asset_key,
                            bfres_data,
                            shared_texture_paths,
                            asset_name,
                        )
                        mesh = _create_mesh_data(
                            source_mesh,
                            display_asset_name,
                            material,
                            apply_custom_normals,
                        )
                        obj = bpy.data.objects.new(
                            str(mesh.get("smo_display_name", mesh.name)),
                            mesh,
                        )
                        obj["smo_test_import"] = True
                        obj["smo_source_bfres"] = bfres_name
                        obj["smo_source_model"] = model.name
                        collection.objects.link(obj)
                        created_objects.append(obj)

                        if (
                            armature_object is not None
                            and source_mesh.bone_weights
                        ):
                            try:
                                _apply_skin_binding(
                                    obj,
                                    armature_object,
                                    MeshRigBinding(
                                        rig_key=rig_key,
                                        model_name=model.name,
                                        armature_name=(
                                            armature_object.name
                                        ),
                                        source_archive=model_path,
                                        source_bfres=bfres_name,
                                        skeleton=model.skeleton,
                                        bone_weights=(
                                            source_mesh.bone_weights
                                        ),
                                    ),
                                    bone_names,
                                )
                                model_rigged_count += 1
                                rigged_mesh_count += 1
                            except Exception as exc:
                                rig_errors[
                                    f"{model.name}/{source_mesh.name}"
                                ] = str(exc)

                                for modifier in tuple(obj.modifiers):
                                    obj.modifiers.remove(modifier)

                                for group in tuple(obj.vertex_groups):
                                    obj.vertex_groups.remove(group)

                                for key in (
                                    "smo_rigged",
                                    "smo_armature",
                                ):
                                    if key in obj:
                                        del obj[key]

                                obj.parent = root
                                print(
                                    "[Odyssey Toolkit Standalone] "
                                    "Skin binding failed; using static "
                                    f"bind pose for {source_mesh.name}: "
                                    f"{exc}"
                                )
                        else:
                            obj.parent = root

                        object_count += 1

                    if (
                        armature_object is not None
                        and model_rigged_count == 0
                    ):
                        created_objects.remove(armature_object)
                        armature_data = armature_object.data
                        bpy.data.objects.remove(
                            armature_object,
                            do_unlink=True,
                        )

                        if armature_data.users == 0:
                            bpy.data.armatures.remove(armature_data)

                        armature_count -= 1

            if object_count == 0:
                raise ValueError(
                    f"No supported static meshes were found in {model_path.name}."
                )

            root["smo_model_count"] = model_count
            root["smo_mesh_object_count"] = object_count
            root["smo_custom_normals_enabled"] = apply_custom_normals
            root["smo_cloth_nov_approximation_enabled"] = (
                self._experimental_cloth_nov
            )
            root["smo_armatures_enabled"] = import_armatures
            root["smo_armature_object_count"] = armature_count
            root["smo_rigged_mesh_object_count"] = rigged_mesh_count
            root["smo_rig_errors"] = json.dumps(
                rig_errors,
                sort_keys=True,
            )
            root["smo_missing_textures"] = json.dumps(
                sorted(self._missing_albedo_textures)
            )
            root["smo_texture_errors"] = json.dumps(
                {
                    name: message
                    for name, message in sorted(self._texture_errors.items())
                },
                sort_keys=True,
            )
            if self._persistent_texture_cache is None:
                from .texture_cache import disabled_cache_payload

                cache_payload = disabled_cache_payload(
                    self._texture_cache_directory
                )
            else:
                cache_payload = self._persistent_texture_cache.payload()

            root["smo_texture_cache"] = json.dumps(
                cache_payload,
                sort_keys=True,
                separators=(",", ":"),
            )
            timings.add("import_total", time.perf_counter() - import_started)
            root["smo_performance_timings"] = timings.to_json()
            root["smo_performance_total_seconds"] = timings.seconds(
                "import_total"
            )
            print_performance_summary(
                timings,
                prefix="[Odyssey Toolkit Standalone]",
            )

            _remove_generated_objects(previous_generated)
            root.name = root_name

            for obj in context.selected_objects:
                obj.select_set(False)

            root.select_set(True)
            context.view_layer.objects.active = root

            if self._texture_errors:
                self.report(
                    {"WARNING"},
                    (
                        f"Imported {object_count} meshes with "
                        f"{len(self._texture_errors)} texture errors; "
                        "see the console."
                    ),
                )
            else:
                self.report(
                    {"INFO"},
                    f"Imported {object_count} test meshes from {model_path.name}.",
                )

            return {"FINISHED"}

        except Exception as exc:
            _remove_generated_objects(
                obj
                for obj in reversed(created_objects)
                if obj.name in bpy.data.objects
            )

            print("[Odyssey Toolkit] Standalone test import failed:")
            traceback.print_exc()
            self.report({"ERROR"}, f"Could not import test model: {exc}")
            return {"CANCELLED"}
        finally:
            reset_active_timings(timing_token)


def draw_import_menu(
    self: bpy.types.Menu,
    context: bpy.types.Context,
) -> None:
    self.layout.operator(
        SMO_OT_import_test_model.bl_idname,
        text="Super Mario Odyssey Test Model (.szs/.bfres)",
    )
