from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
from typing import Any

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingLayout:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events
        self.enabled = True
        self.alert = False
        self.scale_y = 1.0

    def _child(self) -> "RecordingLayout":
        return RecordingLayout(self.events)

    def box(self) -> "RecordingLayout":
        return self._child()

    def row(self, *, align: bool = False) -> "RecordingLayout":
        return self._child()

    def column(self, *, align: bool = False) -> "RecordingLayout":
        return self._child()

    def label(self, *, text: str = "", icon: str = "NONE") -> None:
        self.events.append(("label", text, icon))

    def prop(
        self,
        data: Any,
        property_name: str,
        *,
        text: str | None = None,
        **_kwargs: Any,
    ) -> None:
        self.events.append(("prop", property_name, text))

    def operator(
        self,
        operator_id: str,
        *,
        text: str | None = None,
        icon: str = "NONE",
        **_kwargs: Any,
    ) -> Any:
        self.events.append(("operator", operator_id, text, icon))
        return SimpleNamespace()


def draw_panel(panel_class: type, context: bpy.types.Context) -> list[tuple[Any, ...]]:
    events: list[tuple[Any, ...]] = []
    panel = SimpleNamespace(layout=RecordingLayout(events))
    panel_class.draw(panel, context)
    return events

def draw_preferences(
    preferences: Any,
    context: bpy.types.Context,
) -> list[tuple[Any, ...]]:
    events: list[tuple[Any, ...]] = []
    preferences.layout = RecordingLayout(events)
    addon.SMOAddonPreferences.draw(preferences, context)
    return events


def run(romfs_root: Path) -> None:
    registered = False
    original_get_preferences = addon.get_addon_preferences
    original_running_check = addon.static_import_is_running
    fake_preferences = SimpleNamespace(
        romfs_path="",
        apply_custom_normals=False,
        import_armatures=False,
        experimental_cloth_nov=False,
        use_actor_registry=True,
        use_texture_cache=False,
        texture_cache_parent="",
    )

    try:
        addon.register()
        registered = True
        addon.get_addon_preferences = lambda context=None: fake_preferences
        settings = bpy.context.scene.smo_settings
        check(
            settings.include_environment
            and settings.include_characters
            and settings.include_gameplay
            and settings.include_collectibles
            and settings.include_effects,
            "Visual import categories are not enabled by default",
        )
        check(
            not settings.include_audio
            and not settings.include_technical
            and not settings.include_unclassified,
            "Diagnostic-only categories are enabled by default",
        )
        preference_properties = addon.SMOAddonPreferences.bl_rna.properties
        check(
            preference_properties["apply_custom_normals"].default,
            "BFRES custom normals are not enabled for new installs",
        )
        check(
            not preference_properties["import_armatures"].default
            and not preference_properties["experimental_cloth_nov"].default,
            "Stage-cost or experimental options are enabled by default",
        )

        check(
            addon.SMO_PT_kingdom_importer.bl_label == "Stage Importer",
            "The visible panel was not renamed to Stage Importer",
        )
        check(
            addon.SMO_PT_models_and_animations.bl_label
            == "Assets & Animations",
            "The model and animation tools do not have a dedicated panel",
        )
        check(
            getattr(addon.SMO_PT_models_and_animations, "bl_parent_id", "")
            == "",
            "Models & Animations is not a separate top-level panel",
        )
        check(
            "DEFAULT_CLOSED"
            in addon.SMO_PT_models_and_animations.bl_options,
            "Models & Animations does not start collapsed",
        )
        check(
            addon.SMO_PT_bfres_animations.bl_parent_id
            == addon.SMO_PT_models_and_animations.bl_idname,
            "BFRES Animations is not attached to Models & Animations",
        )
        check(
            "DEFAULT_CLOSED" in addon.SMO_PT_bfres_animations.bl_options,
            "BFRES Animations does not start collapsed",
        )

        check(
            addon.SMO_PT_diagnostics.bl_label == "Diagnostics"
            and getattr(addon.SMO_PT_diagnostics, "bl_parent_id", "") == "",
            "Diagnostics is not a separate top-level Toolkit panel",
        )
        diagnostic_events = draw_panel(addon.SMO_PT_diagnostics, bpy.context)
        check(
            (
                "operator",
                "smo.export_model_report",
                "Export Current Stage Report",
                "FILE_TEXT",
            )
            in diagnostic_events,
            "Diagnostics does not expose the current-stage report",
        )

        settings.romfs_path = str(romfs_root)
        check(settings.worlds_loaded, f"Initial ROMFS load failed: {settings.load_error}")
        check(
            fake_preferences.romfs_path == str(romfs_root),
            "A valid ROMFS path was not remembered",
        )
        stage_count = len(addon._KINGDOM_ENUM_ITEMS)
        check(stage_count > 1, "The valid ROMFS produced no stage catalogue")

        import_preference_events = draw_preferences(
            fake_preferences, bpy.context
        )
        navigation_events = [
            event
            for event in import_preference_events
            if event[0] == "operator"
            and event[1] == "smo.set_preferences_section"
        ]
        check(
            [event[2] for event in navigation_events]
            == ["Import", "Texture Cache", "Actor Registry"],
            "Add-on Preferences does not expose the transient section tabs",
        )
        check(
            ("prop", "apply_custom_normals", None)
            in import_preference_events,
            "Import Preferences does not expose custom normals",
        )
        check(
            ("prop", "import_armatures", None)
            in import_preference_events,
            "Import Preferences does not expose armatures",
        )

        check(
            ("prop", "experimental_cloth_nov", None)
            in import_preference_events,
            "Import Preferences does not expose the experimental cloth gate",
        )

        result = bpy.ops.smo.set_preferences_section(
            section="ACTOR_REGISTRY"
        )
        check(result == {"FINISHED"}, f"Registry tab returned {result}")
        registry_preference_events = draw_preferences(
            fake_preferences, bpy.context
        )
        check(
            any(
                event[0] == "operator"
                and event[1] == "smo.build_actor_registry"
                for event in registry_preference_events
            ),
            "Add-on Preferences does not expose Build Actor Registry",
        )
        check(
            ("prop", "use_actor_registry", None)
            in registry_preference_events,
            "Add-on Preferences does not expose the registry enable switch",
        )

        result = bpy.ops.smo.set_preferences_section(
            section="TEXTURE_CACHE"
        )
        check(result == {"FINISHED"}, f"Texture Cache tab returned {result}")
        cache_preference_events = draw_preferences(
            fake_preferences, bpy.context
        )
        check(
            ("prop", "use_texture_cache", None)
            in cache_preference_events,
            "Add-on Preferences does not expose the texture cache switch",
        )
        check(
            ("prop", "texture_cache_parent", None)
            in cache_preference_events,
            "Add-on Preferences does not expose the texture cache folder",
        )
        check(
            any(
                event[0] == "operator"
                and event[1] == "smo.open_texture_cache"
                for event in cache_preference_events
            ),
            "Add-on Preferences does not expose Open Texture Cache",
        )
        check(
            any(
                event[0] == "operator"
                and event[1] == "smo.refresh_texture_cache_status"
                for event in cache_preference_events
            ),
            "Add-on Preferences does not expose cache statistics refresh",
        )


        settings.kingdom = "CityWorldHomeStage"
        settings.scenario = "2"
        result = bpy.ops.smo.reload_stages()
        check(result == {"FINISHED"}, f"Reload Stages returned {result}")
        check(len(addon._KINGDOM_ENUM_ITEMS) == stage_count, "Reload changed stage count")
        check(settings.kingdom == "CityWorldHomeStage", "Reload lost the selected stage")
        check(settings.scenario == "2", "Reload lost the selected scenario")

        parent_events = draw_panel(addon.SMO_PT_kingdom_importer, bpy.context)
        labels = {event[1] for event in parent_events if event[0] == "label"}
        check(
            {"Source", "Stage", "Scenario", "Import"} <= labels,
            f"The polished panel sections are incomplete: {sorted(labels)}",
        )
        check(
            (
                "operator",
                "smo.import_static_models",
                "Import Stage",
                "MESH_DATA",
            )
            in parent_events,
            "The primary button is not labelled Import Stage",
        )
        check(
            any(
                event[0] == "operator" and event[1] == "smo.reload_stages"
                for event in parent_events
            ),
            "The Source section does not expose Reload Stages",
        )
        check(
            not any(
                event[0] == "operator"
                and event[1] == "smo.export_model_report"
                for event in parent_events
            ),
            "The model-report export still clutters the main panel",
        )

        category_properties = {
            "include_environment",
            "include_characters",
            "include_gameplay",
            "include_collectibles",
            "include_effects",
            "include_audio",
            "include_technical",
            "include_unclassified",
        }
        visible_category_properties = {
            event[1]
            for event in parent_events
            if event[0] == "prop" and event[1].startswith("include_")
        }
        check(
            visible_category_properties == category_properties,
            "The Stage Importer category controls are incomplete",
        )
        check(
            sum(
                event[0] == "operator"
                and event[1] == "smo.set_import_categories"
                for event in parent_events
            )
            == 2,
            "The category controls do not expose All and None",
        )
        result = bpy.ops.smo.set_import_categories(enabled=False)
        check(result == {"FINISHED"}, f"Category None returned {result}")
        check(
            not any(
                getattr(settings, property_name)
                for property_name in category_properties
            ),
            "Category None left one or more categories enabled",
        )
        result = bpy.ops.smo.set_import_categories(enabled=True)
        check(result == {"FINISHED"}, f"Category All returned {result}")
        check(
            all(
                getattr(settings, property_name)
                for property_name in category_properties
            ),
            "Category All left one or more categories disabled",
        )
        settings.romfs_path = ""
        check(
            fake_preferences.romfs_path == str(romfs_root),
            "Clearing the scene path discarded the last valid saved ROMFS",
        )
        animation_events = draw_panel(
            addon.SMO_PT_bfres_animations,
            bpy.context,
        )
        check(
            ("label", "Select an imported Odyssey armature", "INFO")
            in animation_events,
            "BFRES Animations does not explain its armature selection requirement",
        )
        model_tool_events = draw_panel(
            addon.SMO_PT_models_and_animations,
            bpy.context,
        )
        check(
            (
                "operator",
                "smo.import_test_model",
                "Import Standalone Model",
                "FILE_3D",
            )
            in model_tool_events,
            "Standalone import is hidden when no ROMFS is loaded",
        )
        check(
            not any(
                event[0] == "operator"
                and event[1] == "smo.import_smd_animation"
                for event in model_tool_events
            ),
            "Switch Toolbox animation import still appears in the SMO sidebar",
        )
        check(
            not any(
                event[0] == "operator"
                and event[1] == "smo.import_static_models"
                for event in model_tool_events
            ),
            "Stage import controls leaked into Models & Animations",
        )

        settings.romfs_path = str(romfs_root)
        last_valid_path = fake_preferences.romfs_path
        settings.romfs_path = str(romfs_root / "Not_A_ROMFS")
        check(not settings.worlds_loaded, "An invalid ROMFS was marked as loaded")
        check(bool(settings.load_error), "An invalid ROMFS produced no actionable error")
        check(
            fake_preferences.romfs_path == last_valid_path,
            "An invalid ROMFS overwrote the last valid saved preference",
        )

        settings.romfs_path = str(romfs_root)
        addon.static_import_is_running = lambda: True
        running_events = draw_panel(
            addon.SMO_PT_kingdom_importer,
            bpy.context,
        )
        check(
            any(
                event[0] == "operator"
                and event[1] == "smo.cancel_stage_import"
                for event in running_events
            ),
            "The running import panel does not expose Cancel Import",
        )
        check(
            addon.SMO_OT_cancel_stage_import.poll(bpy.context),
            "Cancel Import was disabled while a stage import was running",
        )
        check(
            not addon.SMO_OT_reload_stages.poll(bpy.context),
            "Reload Stages remained enabled during a stage import",
        )
        check(
            not addon.SMO_OT_import_kingdom.poll(bpy.context),
            "Diagnostic import remained enabled during a stage import",
        )
        check(
            not addon.SMO_OT_export_model_report.poll(bpy.context),
            "Model-report export remained enabled during a stage import",
        )
        addon.static_import_is_running = original_running_check

        import smo_kingdom_importer.static_model_import as static_import_module

        static_import_module._STATIC_IMPORT_RUNNING = True
        static_import_module._STATIC_IMPORT_CANCEL_REQUESTED = False

        try:
            result = bpy.ops.smo.cancel_stage_import()
            check(result == {"FINISHED"}, f"Cancel Import returned {result}")
            check(
                static_import_module._STATIC_IMPORT_CANCEL_REQUESTED,
                "Cancel Import did not signal the modal importer",
            )
        finally:
            static_import_module._STATIC_IMPORT_RUNNING = False
            static_import_module._STATIC_IMPORT_CANCEL_REQUESTED = False

        expected_stage = settings.kingdom
        addon.unregister()
        registered = False
        check(
            len(addon._KINGDOM_ENUM_ITEMS) == 1,
            "Unregister did not reset the global stage catalogue",
        )
        addon.register()
        registered = True
        settings = bpy.context.scene.smo_settings
        check(settings.worlds_loaded, f"Re-register did not reload stages: {settings.load_error}")
        check(len(addon._KINGDOM_ENUM_ITEMS) == stage_count, "Re-register left a stale catalogue")
        check(settings.kingdom == expected_stage, "Re-register lost the selected stage")

        print(
            "UI_QOL_REGRESSION: PASS "
            + json.dumps(
                {
                    "stage_count": stage_count,
                    "selected_stage": settings.kingdom,
                    "model_tools_without_romfs": True,
                },
                sort_keys=True,
            )
        )

    finally:
        addon.static_import_is_running = original_running_check
        addon.get_addon_preferences = original_get_preferences

        if registered:
            addon.unregister()


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: ui_qol_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())
