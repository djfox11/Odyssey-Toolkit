from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
import zipfile

import bpy


class BackgroundWindowManager:
    def progress_begin(self, _minimum: int, _maximum: int) -> None:
        pass

    def progress_update(self, _value: int) -> None:
        pass

    def progress_end(self) -> None:
        pass

    def event_timer_add(self, _interval: float, *, window: Any) -> object:
        return object()

    def event_timer_remove(self, _timer: object) -> None:
        pass

    def modal_handler_add(self, _operator: Any) -> None:
        pass


def operator_harness(operator_type: type) -> Any:
    def report(self: Any, levels: set[str], message: str) -> None:
        self.captured_reports.append(
            {"levels": sorted(levels), "message": message}
        )

    methods = {
        name: value
        for name, value in operator_type.__dict__.items()
        if callable(value) and not name.startswith("bl_")
    }
    methods["report"] = report
    harness_type = type("StageImportHarness", (), methods)
    harness = harness_type()
    harness.captured_reports = []
    return harness


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Capture a completed Odyssey Toolkit stage-import baseline."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--romfs", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--scenario", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-parent", type=Path)
    parser.add_argument("--armatures", action="store_true")
    parser.add_argument("--cloth", action="store_true")
    parser.add_argument("--no-custom-normals", action="store_true")
    parser.add_argument("--no-lighting", action="store_true")
    parser.add_argument("--include-audio", action="store_true")
    parser.add_argument("--include-technical", action="store_true")
    parser.add_argument("--include-unclassified", action="store_true")
    parser.add_argument("--reimport-cycle", action="store_true")
    return parser.parse_args(arguments)


def prepare_import_path(source: Path, dependency_root: Path) -> None:
    package = source / "odyssey_toolkit"
    wheels = tuple((package / "wheels").glob("oead-*-cp311-cp311-win_amd64.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected exactly one bundled CPython 3.11 oead wheel.")
    dependency_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheels[0]) as wheel:
        wheel.extractall(dependency_root)
    sys.path.insert(0, str(dependency_root))
    sys.path.insert(0, str(source))


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    try:
        return [json_value(item) for item in value]
    except TypeError:
        return str(value)


def data_block_counts() -> dict[str, int]:
    return {
        "objects": len(bpy.data.objects),
        "collections": len(bpy.data.collections),
        "meshes": len(bpy.data.meshes),
        "materials": len(bpy.data.materials),
        "images": len(bpy.data.images),
        "armatures": len(bpy.data.armatures),
        "actions": len(bpy.data.actions),
        "cameras": len(bpy.data.cameras),
        "lights": len(bpy.data.lights),
        "worlds": len(bpy.data.worlds),
    }


def generated_signature() -> dict[str, Any]:
    entries = sorted(
        (
            obj.name,
            obj.type,
            obj.parent.name if obj.parent is not None else "",
        )
        for obj in bpy.data.objects
        if obj.get("smo_static_model_generated")
    )
    encoded = json.dumps(entries, separators=(",", ":")).encode("utf-8")
    return {
        "count": len(entries),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def import_context() -> SimpleNamespace:
    return SimpleNamespace(
        scene=bpy.context.scene,
        collection=bpy.context.scene.collection,
        view_layer=bpy.context.view_layer,
        selected_objects=tuple(bpy.context.selected_objects),
        preferences=bpy.context.preferences,
        window_manager=BackgroundWindowManager(),
        window=None,
        workspace=None,
        screen=None,
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    dependency_root = args.output.parent / "python-dependencies"
    prepare_import_path(args.source.resolve(), dependency_root)

    import odyssey_toolkit as addon
    from odyssey_toolkit.actor_registry import configure_actor_registry
    from odyssey_toolkit.static_model_import import SMO_OT_import_static_models

    preferences = SimpleNamespace(
        apply_custom_normals=not args.no_custom_normals,
        import_armatures=args.armatures,
        experimental_cloth_nov=args.cloth,
        use_actor_registry=False,
        use_texture_cache=args.cache_parent is not None,
        texture_cache_parent=(
            str(args.cache_parent.resolve())
            if args.cache_parent is not None
            else ""
        ),
    )
    registered = False
    try:
        addon.register()
        registered = True
        addon.get_addon_preferences = lambda context=None: preferences
        configure_actor_registry(None, enabled=False)
        settings = bpy.context.scene.smo_settings
        settings.romfs_path = str(args.romfs.resolve())
        if not settings.worlds_loaded:
            raise RuntimeError(f"World list failed to load: {settings.load_error}")

        kingdom_id = next(
            (
                identifier
                for identifier, world in addon._WORLD_BY_STAGE.items()
                if str(world.get("Name")) == args.stage
            ),
            None,
        )
        if kingdom_id is None:
            raise ValueError(f"Stage is not present in WorldList: {args.stage}")
        settings.kingdom = kingdom_id
        settings.scenario = str(args.scenario)
        settings.import_stage_lighting = not args.no_lighting
        settings.include_environment = True
        settings.include_characters = True
        settings.include_gameplay = True
        settings.include_collectibles = True
        settings.include_effects = True
        settings.include_audio = args.include_audio
        settings.include_technical = args.include_technical
        settings.include_unclassified = args.include_unclassified

        operator = operator_harness(SMO_OT_import_static_models)
        context = import_context()
        result = operator.execute(context)
        if result != {"RUNNING_MODAL"}:
            raise RuntimeError(f"Import preparation returned {sorted(result)}")

        modal_iterations = 0
        while result == {"RUNNING_MODAL"}:
            result = operator.modal(context, SimpleNamespace(type="TIMER"))
            modal_iterations += 1
            if modal_iterations > 1_000_000:
                raise RuntimeError("Import exceeded the modal-iteration limit.")
        if result != {"FINISHED"}:
            raise RuntimeError(f"Import returned {sorted(result)}")

        root = operator._root
        properties = {
            key: json_value(root[key])
            for key in sorted(root.keys())
            if key.startswith("smo_")
        }
        result_data = {
            "baseline_version": "0.41.3",
            "blender_version": bpy.app.version_string,
            "stage": args.stage,
            "scenario": args.scenario,
            "settings": {
                "custom_normals": not args.no_custom_normals,
                "armatures": args.armatures,
                "cloth": args.cloth,
                "stage_lighting": not args.no_lighting,
                "texture_cache": args.cache_parent is not None,
                "include_audio": args.include_audio,
                "include_technical": args.include_technical,
                "include_unclassified": args.include_unclassified,
                "actor_registry": False,
            },
            "modal_iterations": modal_iterations,
            "reports": operator.captured_reports,
            "root_name": root.name,
            "root_properties": properties,
            "data_blocks": data_block_counts(),
            "generated_signature": generated_signature(),
        }
        if args.reimport_cycle:
            first_counts = data_block_counts()
            first_signature = generated_signature()

            reimport = operator_harness(SMO_OT_import_static_models)
            reimport_result = reimport.execute(context)
            if reimport_result != {"RUNNING_MODAL"}:
                raise RuntimeError(
                    f"Re-import preparation returned {sorted(reimport_result)}"
                )
            while reimport_result == {"RUNNING_MODAL"}:
                reimport_result = reimport.modal(
                    context,
                    SimpleNamespace(type="TIMER"),
                )
            if reimport_result != {"FINISHED"}:
                raise RuntimeError(f"Re-import returned {sorted(reimport_result)}")
            successful_counts = data_block_counts()
            successful_signature = generated_signature()
            successful_total_seconds = reimport._root.get(
                "smo_performance_total_seconds"
            )

            failed = operator_harness(SMO_OT_import_static_models)
            failed_result = failed.execute(context)
            if failed_result != {"RUNNING_MODAL"}:
                raise RuntimeError(
                    f"Failure-test preparation returned {sorted(failed_result)}"
                )

            def inject_failure(_classified: Any) -> None:
                raise RuntimeError("synthetic re-import failure")

            failed._process_placement = inject_failure
            failed_result = failed.modal(context, SimpleNamespace(type="TIMER"))
            failed_signature = generated_signature()
            failed_root_status = failed._root.get("smo_import_status")
            failed_attempt_status = failed._root.get(
                "smo_last_reimport_status"
            )

            cancelled = operator_harness(SMO_OT_import_static_models)
            cancelled_result = cancelled.execute(context)
            if cancelled_result != {"RUNNING_MODAL"}:
                raise RuntimeError(
                    "Cancellation-test preparation returned "
                    f"{sorted(cancelled_result)}"
                )
            cancelled_result = cancelled.modal(
                context,
                SimpleNamespace(type="ESC"),
            )
            cancelled_signature = generated_signature()

            result_data["reimport_cycle"] = {
                "first_counts": first_counts,
                "first_signature": first_signature,
                "successful_result": sorted(reimport_result),
                "successful_counts": successful_counts,
                "successful_signature": successful_signature,
                "successful_total_seconds": successful_total_seconds,
                "failed_result": sorted(failed_result),
                "failed_root_status": failed_root_status,
                "failed_attempt_status": failed_attempt_status,
                "failed_signature": failed_signature,
                "cancelled_result": sorted(cancelled_result),
                "cancelled_root_status": cancelled._root.get(
                    "smo_import_status"
                ),
                "cancelled_attempt_status": cancelled._root.get(
                    "smo_last_reimport_status"
                ),
                "cancelled_signature": cancelled_signature,
            }
        return result_data
    finally:
        if registered:
            addon.unregister()


def main() -> int:
    args = parse_args()
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("ODYSSEY_REGRESSION_BASELINE=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
