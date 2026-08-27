from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

import bpy
import bpy.utils.previews
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    EnumProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup


bl_info = {
    "name": "Odyssey Toolkit",
    "author": "djfox11",
    "version": (0, 40, 0),
    "blender": (4, 5, 0),
    "location": "3D Viewport > Sidebar > Odyssey",
    "description": (
        "Import and inspect Super Mario Odyssey stages, assets, and animations"
    ),
    "category": "Import-Export",
}


ADDON_DIR = Path(__file__).resolve().parent
SCENARIO_PREVIEW_DIR = ADDON_DIR / "previews" / "scenarios"
MISSING_SCENARIO_PREVIEW_PATH = SCENARIO_PREVIEW_DIR / "_scenario.png"
PREVIEW_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

SCENARIO_ICON_FILES = {
    "Main story clear": "main_story_clear",
    "Postgame": "postgame",
    "Moon Rock": "moon_rock",
    "Balloon World": "balloon_world",
}

_WORLD_BY_STAGE: dict[str, dict[str, Any]] = {}
_STAGE_SCENARIO_NUMBERS: dict[str, tuple[int, ...]] = {}
_PREVIEW_COLLECTIONS: dict[str, Any] = {}
_ENUMS_REBUILDING = False
_ACTOR_REGISTRY_BUILD_OPERATOR: Any | None = None
_ACTOR_REGISTRY_CANCEL_REQUESTED = False
_ACTOR_REGISTRY_REPORT_OPERATOR: Any | None = None
_ACTOR_REGISTRY_REPORT_CANCEL_REQUESTED = False
_PREFERENCES_SECTION = "IMPORT"

PREFERENCES_SECTIONS = (
    ("IMPORT", "Import", "IMPORT"),
    ("TEXTURE_CACHE", "Texture Cache", "IMAGE_DATA"),
    (
        "ACTOR_REGISTRY",
        "Actor Registry",
        "OUTLINER_OB_GROUP_INSTANCE",
    ),
)
PREFERENCES_SECTION_IDS = frozenset(
    section for section, _label, _icon in PREFERENCES_SECTIONS
)


def set_preferences_section(section: str) -> bool:
    global _PREFERENCES_SECTION

    if section not in PREFERENCES_SECTION_IDS:
        return False

    _PREFERENCES_SECTION = section
    return True


IMPORT_COLLECTION_GROUPS = {
    "ENVIRONMENT": ("Environment", "COLOR_04", False),
    "CHARACTERS": ("Characters", "COLOR_01", False),
    "GAMEPLAY": ("Gameplay Objects", "COLOR_02", False),
    "COLLECTIBLES": ("Collectibles", "COLOR_03", False),
    "EFFECTS": ("Effects", "COLOR_06", True),
    "AUDIO": ("Audio", "COLOR_05", True),
    "AREAS": ("Areas", "COLOR_05", True),
    "CAMERAS": ("Cameras and Helpers", "COLOR_07", True),
    "HELPERS": ("Cameras and Helpers", "COLOR_07", True),
    "DEBUG": ("Debug", "COLOR_01", True),
    "UNKNOWN_MODEL": ("Unknown Models", "COLOR_08", False),
    "UNKNOWN_MODELLESS": ("Unknown Modelless", "COLOR_08", True),
}

EnumItem = tuple[str, str, str, int, int]

_EMPTY_KINGDOM_ITEM: EnumItem = (
    "__NONE__",
    "No stages loaded",
    "Choose the extracted ROMFS folder first",
    0,
    0,
)
_EMPTY_SCENARIO_ITEM: EnumItem = (
    "__NONE__",
    "No scenarios available",
    "",
    0,
    0,
)
_KINGDOM_ENUM_ITEMS: list[EnumItem] = [_EMPTY_KINGDOM_ITEM]
_SCENARIO_ENUM_ITEMS: list[EnumItem] = [_EMPTY_SCENARIO_ITEM]


def reset_enum_items() -> None:
    _KINGDOM_ENUM_ITEMS.clear()
    _KINGDOM_ENUM_ITEMS.append(_EMPTY_KINGDOM_ITEM)
    _SCENARIO_ENUM_ITEMS.clear()
    _SCENARIO_ENUM_ITEMS.append(_EMPTY_SCENARIO_ITEM)


def kingdom_enum_items(
    self: PropertyGroup,
    context: bpy.types.Context | None,
) -> list[EnumItem]:
    # These strings are retained globally because Blender's dynamic enum
    # callbacks require their returned strings to remain alive.
    return _KINGDOM_ENUM_ITEMS


def scenario_enum_items(
    self: PropertyGroup,
    context: bpy.types.Context | None,
) -> list[EnumItem]:
    return _SCENARIO_ENUM_ITEMS


def get_preview_collection(name: str) -> Any:
    collection = _PREVIEW_COLLECTIONS.get(name)

    if collection is None:
        collection = bpy.utils.previews.new()
        _PREVIEW_COLLECTIONS[name] = collection

    return collection


def clear_preview_collections() -> None:
    for collection in _PREVIEW_COLLECTIONS.values():
        bpy.utils.previews.remove(collection)

    _PREVIEW_COLLECTIONS.clear()


def find_preview_path(directory: Path, stem: str) -> Path | None:
    for suffix in PREVIEW_SUFFIXES:
        candidate = directory / f"{stem}{suffix}"

        if candidate.is_file():
            return candidate

    return None


def load_preview_icon(
    collection_name: str,
    key: str,
    path: Path,
    fallback_path: Path,
) -> int:
    collection = get_preview_collection(collection_name)
    candidates = (path,) if path == fallback_path else (path, fallback_path)

    for candidate in candidates:
        if not candidate.is_file():
            continue

        cache_key = f"{key}::{candidate.name}"

        try:
            if cache_key not in collection:
                collection.load(cache_key, str(candidate), "IMAGE")

            return collection[cache_key].icon_id
        except Exception:
            print(f"[Odyssey Toolkit] Failed to load preview {candidate}:")
            traceback.print_exc()

    return 0


def scenario_preview_path(tag: str | None) -> Path:
    filename = SCENARIO_ICON_FILES.get(tag or "", "scenario")
    return (
        find_preview_path(SCENARIO_PREVIEW_DIR, filename)
        or MISSING_SCENARIO_PREVIEW_PATH
    )


def scenario_preview_icon(tags: list[str]) -> int:
    priority = ("Moon Rock", "Balloon World", "Postgame", "Main story clear")
    selected_tag = next((tag for tag in priority if tag in tags), None)

    return load_preview_icon(
        "scenarios",
        f"scenario::{selected_tag or 'generic'}",
        scenario_preview_path(selected_tag),
        MISSING_SCENARIO_PREVIEW_PATH,
    )


def scenario_tags(world: dict[str, Any], scenario: int) -> list[str]:
    fields = (
        ("ClearMainScenario", "Main story clear"),
        ("AfterEndingScenario", "Postgame"),
        ("MoonRockScenario", "Moon Rock"),
        ("BalloonScenario", "Balloon World"),
    )

    tags: list[str] = []

    for key, label in fields:
        value = world.get(key)

        if value is None:
            continue

        try:
            if int(value) == scenario:
                tags.append(label)
        except (TypeError, ValueError):
            pass

    return tags


def rebuild_scenario_items(
    settings: "SMOProperties",
    preferred_selection: str | None = None,
) -> None:
    previous_selection = (
        getattr(settings, "scenario", "")
        if preferred_selection is None
        else preferred_selection
    )
    _SCENARIO_ENUM_ITEMS.clear()
    settings.scenario_error = ""
    world = _WORLD_BY_STAGE.get(settings.kingdom)

    if world is None:
        _SCENARIO_ENUM_ITEMS.append(
            ("__NONE__", "No scenarios available", "", 0, 0)
        )
        return

    stage_name = str(world["Name"])
    scenario_numbers = tuple(world.get("_scenario_numbers", ()))

    try:
        if not scenario_numbers:
            scenario_numbers = _STAGE_SCENARIO_NUMBERS.get(stage_name, ())

        if not scenario_numbers:
            from .stage_data import read_stage_scenario_numbers

            romfs_root = resolve_romfs_root(settings.romfs_path)
            scenario_numbers = read_stage_scenario_numbers(
                romfs_root,
                stage_name,
            )
            _STAGE_SCENARIO_NUMBERS[stage_name] = scenario_numbers

    except Exception as exc:
        settings.scenario_error = str(exc)
        _SCENARIO_ENUM_ITEMS.append(
            (
                "__NONE__",
                "Could not load scenarios",
                settings.scenario_error,
                0,
                0,
            )
        )
        settings.scenario = "__NONE__"
        print(
            f"[Odyssey Toolkit] Failed to inspect {stage_name} scenarios:"
        )
        traceback.print_exc()
        return

    for item_number, scenario_number in enumerate(scenario_numbers, start=1):
        tags = scenario_tags(world, scenario_number)
        label = f"Scenario {scenario_number}"

        if tags:
            label += f" \u2014 {', '.join(tags)}"

        _SCENARIO_ENUM_ITEMS.append(
            (
                str(scenario_number),
                label,
                f"Import scenario {scenario_number}",
                scenario_preview_icon(tags),
                item_number,
            )
        )

    if not _SCENARIO_ENUM_ITEMS:
        _SCENARIO_ENUM_ITEMS.append(
            ("__NONE__", "No scenarios available", "", 0, 0)
        )
        settings.scenario = "__NONE__"
        return

    valid_identifiers = {item[0] for item in _SCENARIO_ENUM_ITEMS}

    if previous_selection in valid_identifiers:
        settings.scenario = previous_selection
    else:
        settings.scenario = _SCENARIO_ENUM_ITEMS[0][0]

def resolve_romfs_root(romfs_path: str) -> Path:
    selected_path = Path(bpy.path.abspath(romfs_path)).resolve()

    if (selected_path / "SystemData" / "WorldList.szs").is_file():
        return selected_path

    if (
        selected_path.name.casefold() == "systemdata"
        and (selected_path / "WorldList.szs").is_file()
    ):
        return selected_path.parent

    raise FileNotFoundError(
        "Could not find SystemData/WorldList.szs inside the selected folder."
    )


def resolve_world_list_path(romfs_path: str) -> Path:
    return resolve_romfs_root(romfs_path) / "SystemData" / "WorldList.szs"


def load_worlds(settings: "SMOProperties") -> bool:
    global _ENUMS_REBUILDING

    preserve_selection = bool(_WORLD_BY_STAGE) and bool(settings.worlds_loaded)
    previous_kingdom = (
        getattr(settings, "kingdom", "") if preserve_selection else ""
    )
    previous_scenario = (
        getattr(settings, "scenario", "") if preserve_selection else ""
    )
    settings.load_error = ""
    settings.scenario_error = ""
    _WORLD_BY_STAGE.clear()
    _STAGE_SCENARIO_NUMBERS.clear()
    _KINGDOM_ENUM_ITEMS.clear()
    _SCENARIO_ENUM_ITEMS.clear()
    clear_preview_collections()
    _ENUMS_REBUILDING = True

    try:
        if not settings.romfs_path:
            _KINGDOM_ENUM_ITEMS.append(
                (
                    "__NONE__",
                    "No stages loaded",
                    "Choose the ROMFS folder first",
                    0,
                    0,
                )
            )
            _SCENARIO_ENUM_ITEMS.append(
                ("__NONE__", "No scenarios available", "", 0, 0)
            )
            settings.kingdom = "__NONE__"
            settings.scenario = "__NONE__"
            settings.worlds_loaded = False
            return False

        try:
            from .stage_catalog import discover_stage_catalogue
            from .world_list import get_world_display_name, read_world_list

            romfs_root = resolve_romfs_root(settings.romfs_path)
            worlds = read_world_list(
                romfs_root / "SystemData" / "WorldList.szs"
            )
            worlds_by_stage = {}

            for original_world in worlds:
                world = dict(original_world)
                stage_name = str(world.get("Name", "")).strip()

                if not stage_name:
                    continue

                display_name = get_world_display_name(world)
                world["_display_name"] = display_name

                try:
                    scenario_count = int(world.get("ScenarioNum", 0))
                except (TypeError, ValueError):
                    scenario_count = 0

                if scenario_count > 0:
                    world["_scenario_numbers"] = tuple(
                        range(1, scenario_count + 1)
                    )

                worlds_by_stage[stage_name] = world

            catalogue = discover_stage_catalogue(
                romfs_root / "StageData"
            )

            for item_number, entry in enumerate(catalogue, start=1):
                world = dict(worlds_by_stage.get(entry.stage_name, {}))
                world["Name"] = entry.stage_name
                world.setdefault("WorldName", "")
                world["_display_name"] = entry.display_name
                world["_group_name"] = entry.group_name
                world["_selector_name"] = entry.selector_name
                world["_translated"] = entry.translated
                _WORLD_BY_STAGE[entry.stage_name] = world
                description = entry.stage_name
                scenario_numbers = tuple(world.get("_scenario_numbers", ()))

                if scenario_numbers:
                    count = len(scenario_numbers)
                    description += (
                        f"; {count} scenario"
                        f"{'s' if count != 1 else ''}"
                    )
                elif entry.translated:
                    description += "; name supplied by the noclip scene list"
                else:
                    description += "; internal StageData name"

                _KINGDOM_ENUM_ITEMS.append(
                    (
                        entry.stage_name,
                        entry.selector_name,
                        description,
                        0,
                        item_number,
                    )
                )

            if not _KINGDOM_ENUM_ITEMS:
                raise RuntimeError(
                    "StageData contained no importable Map archives."
                )

            valid_identifiers = {item[0] for item in _KINGDOM_ENUM_ITEMS}
            selected_kingdom = (
                previous_kingdom
                if previous_kingdom in valid_identifiers
                else _KINGDOM_ENUM_ITEMS[0][0]
            )

            settings.kingdom = selected_kingdom
            rebuild_scenario_items(
                settings,
                previous_scenario if selected_kingdom == previous_kingdom else "",
            )
            settings.worlds_loaded = True
            return True

        except ModuleNotFoundError as exc:
            if exc.name == "oead":
                settings.load_error = (
                    "oead is not installed in Blender's Python environment."
                )
            else:
                settings.load_error = f"Missing Python module: {exc.name}"

            print("[Odyssey Toolkit] Failed to load stages:")
            traceback.print_exc()

        except Exception as exc:
            settings.load_error = str(exc)
            print("[Odyssey Toolkit] Failed to load stages:")
            traceback.print_exc()

        _KINGDOM_ENUM_ITEMS.append(
            ("__NONE__", "Could not load stages", settings.load_error, 0, 0)
        )
        _SCENARIO_ENUM_ITEMS.append(
            ("__NONE__", "No scenarios available", "", 0, 0)
        )
        settings.kingdom = "__NONE__"
        settings.scenario = "__NONE__"
        settings.worlds_loaded = False
        return False

    finally:
        _ENUMS_REBUILDING = False


def on_romfs_path_changed(
    self: "SMOProperties",
    context: bpy.types.Context | None,
) -> None:
    if not load_worlds(self):
        return

    preferences = get_addon_preferences(context)

    if preferences is not None:
        preferences.romfs_path = self.romfs_path


def on_kingdom_changed(
    self: "SMOProperties",
    context: bpy.types.Context | None,
) -> None:
    if _ENUMS_REBUILDING:
        return

    rebuild_scenario_items(self)


def get_addon_preferences(
    context: bpy.types.Context | None = None,
) -> Any | None:
    active_context = context or bpy.context
    preferences = getattr(active_context, "preferences", None)

    if preferences is None:
        return None

    addon = preferences.addons.get(__package__)
    return getattr(addon, "preferences", None)


def restore_saved_romfs_path(settings: "SMOProperties") -> bool:
    preferences = get_addon_preferences()
    saved_path = (
        str(getattr(preferences, "romfs_path", "")).strip()
        if preferences is not None
        else ""
    )

    if not saved_path:
        return False

    settings.romfs_path = saved_path
    return True


def actor_registry_cache_directory(*, create: bool = False) -> Path:
    cache_path = bpy.utils.user_resource(
        "CONFIG",
        path="smo_kingdom_importer/actor_registry",
        create=create,
    )
    return Path(cache_path)


def texture_cache_directory(
    preferences: Any | None = None,
    *,
    create: bool = False,
) -> Path:
    active_preferences = preferences or get_addon_preferences()
    custom_parent = str(
        getattr(active_preferences, "texture_cache_parent", "")
    ).strip()

    if custom_parent:
        parent = Path(bpy.path.abspath(custom_parent)).expanduser().resolve()
        cache_path = parent / "smo_kingdom_importer" / "texture_cache"
    else:
        cache_path = Path(
            bpy.utils.user_resource(
                "DATAFILES",
                path="smo_kingdom_importer/texture_cache",
                create=False,
            )
        )

    if create:
        from .texture_cache import ensure_texture_cache_root

        ensure_texture_cache_root(cache_path)

    return cache_path


def configure_actor_registry_from_preferences(
    preferences: Any | None = None,
) -> None:
    from .actor_registry import configure_actor_registry

    active_preferences = preferences or get_addon_preferences()
    configure_actor_registry(
        actor_registry_cache_directory(),
        enabled=bool(
            getattr(active_preferences, "use_actor_registry", True)
        ),
    )


def on_use_actor_registry_changed(
    self: "SMOAddonPreferences",
    context: bpy.types.Context | None,
) -> None:
    from .object_data import clear_object_data_index_cache

    configure_actor_registry_from_preferences(self)
    clear_object_data_index_cache()


def actor_registry_build_is_running() -> bool:
    return _ACTOR_REGISTRY_BUILD_OPERATOR is not None


def actor_registry_report_is_running() -> bool:
    return _ACTOR_REGISTRY_REPORT_OPERATOR is not None


class SMO_OT_set_preferences_section(Operator):
    bl_idname = "smo.set_preferences_section"
    bl_label = "Show Preferences Section"
    bl_description = "Show this group of Super Mario Odyssey importer settings"
    bl_options = {"INTERNAL"}

    section: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not set_preferences_section(self.section):
            self.report({"ERROR"}, "Unknown preferences section.")
            return {"CANCELLED"}

        area = getattr(context, "area", None)

        if area is not None:
            area.tag_redraw()

        return {"FINISHED"}


class SMOAddonPreferences(AddonPreferences):
    bl_idname = __package__

    romfs_path: StringProperty(
        name="ROMFS",
        description="Last ROMFS folder selected in the 3D Viewport sidebar",
        subtype="DIR_PATH",
        options={"HIDDEN"},
    )

    apply_custom_normals: BoolProperty(
        name="Apply BFRES custom normals",
        description=(
            "Apply validated BFRES normals directly to Blender meshes; "
            "disable this to let Blender recalculate smooth normals"
        ),
        default=True,
    )

    import_armatures: BoolProperty(
        name="Import armatures and skin weights",
        description=(
            "Create Blender armatures, vertex groups and modifiers for "
            "validated multi-bone BFRES models; required for skeletal and "
            "bone-visibility animation"
        ),
        default=False,
    )

    experimental_cloth_nov: BoolProperty(
        name="Experimental cloth NoV/Fresnel approximation",
        description=(
            "Add an approximate view-dependent cloth colour and emission "
            "shader; parsed BFRES metadata is preserved when disabled"
        ),
        default=False,
    )

    use_actor_registry: BoolProperty(
        name="Use actor registry for model resolution",
        description=(
            "Use only unambiguous model mappings observed while building "
            "the local ROMFS actor registry"
        ),
        default=True,
        update=on_use_actor_registry_changed,
    )

    use_texture_cache: BoolProperty(
        name="Use persistent texture cache",
        description=(
            "Store successfully decoded BNTX textures as lossless PNG files "
            "and reuse them on later imports"
        ),
        default=False,
    )

    texture_cache_parent: StringProperty(
        name="Cache Parent Folder",
        description=(
            "Optional parent folder; the add-on creates its dedicated "
            "smo_kingdom_importer/texture_cache directory inside it"
        ),
        subtype="DIR_PATH",
        default="",
    )

    def _draw_texture_cache(self, layout: Any) -> None:
        from .texture_cache import texture_cache_status

        cache_box = layout.box()
        cache_box.label(text="Persistent Texture Cache", icon="IMAGE_DATA")
        cache_box.prop(self, "use_texture_cache")
        cache_column = cache_box.column()
        cache_column.enabled = self.use_texture_cache
        cache_column.prop(self, "texture_cache_parent")
        cache_path = texture_cache_directory(self)
        status = texture_cache_status(cache_path)
        cache_box.label(text=status.message, icon="DISK_DRIVE")
        cache_box.label(text=str(cache_path), icon="FILE_FOLDER")
        cache_buttons = cache_box.row(align=True)
        cache_buttons.operator(
            "smo.open_texture_cache",
            text="Open Cache Folder",
            icon="FILE_FOLDER",
        )
        cache_buttons.operator(
            "smo.refresh_texture_cache_status",
            text="Refresh Statistics",
            icon="FILE_REFRESH",
        )

        if status.exists:
            cache_buttons.operator(
                "smo.clear_texture_cache",
                text="Clear Cache",
                icon="TRASH",
            )

        if not self.use_texture_cache:
            cache_box.label(
                text="Disabled: imports neither read nor write cache files.",
                icon="INFO",
            )

    def _draw_actor_registry(self, layout: Any) -> None:
        registry_box = layout.box()
        registry_box.label(text="Actor Registry", icon="OUTLINER_OB_GROUP_INSTANCE")
        registry_box.prop(self, "use_actor_registry")
        saved_path = str(self.romfs_path).strip()

        if not saved_path:
            registry_box.label(
                text="Choose a valid ROMFS in the Odyssey sidebar first.",
                icon="INFO",
            )
            return

        try:
            romfs_root = resolve_romfs_root(saved_path)
        except Exception as exc:
            error_row = registry_box.row()
            error_row.alert = True
            error_row.label(text=str(exc), icon="ERROR")
            return

        registry_box.label(text=f"Source: {romfs_root.name}", icon="FILE_FOLDER")

        if actor_registry_report_is_running():
            active = _ACTOR_REGISTRY_REPORT_OPERATOR
            builder = getattr(active, "_builder", None)

            if builder is not None:
                registry_box.label(
                    text=(
                        "Report actor "
                        f"{min(builder.current_index + 1, builder.total_count)} "
                        f"of {builder.total_count}: {builder.current_label}"
                    ),
                    icon="TIME",
                )

            registry_box.operator(
                "smo.cancel_actor_registry_report",
                text="Cancel Registry Report",
                icon="CANCEL",
            )
            return

        if actor_registry_build_is_running():
            active = _ACTOR_REGISTRY_BUILD_OPERATOR
            builder = getattr(active, "_builder", None)
            if builder is not None:
                registry_box.label(
                    text=(
                        "Archive "
                        f"{min(builder.current_index + 1, builder.total_count)} "
                        f"of {builder.total_count}: {builder.current_label}"
                    ),
                    icon="TIME",
                )
            registry_box.operator(
                "smo.cancel_actor_registry_build",
                text="Cancel Registry Build",
                icon="CANCEL",
            )
            return

        from .actor_registry import cached_registry_file_status

        status = cached_registry_file_status(
            romfs_root,
            actor_registry_cache_directory(),
        )
        registry_box.label(
            text=status.message,
            icon="CHECKMARK" if status.valid else "INFO",
        )
        button_row = registry_box.row(align=True)
        button_row.operator(
            "smo.build_actor_registry",
            text="Rebuild Registry" if status.exists else "Build Registry",
            icon="FILE_REFRESH",
        )

        if status.exists:
            button_row.operator(
                "smo.refresh_actor_registry_status",
                text="Refresh Status",
                icon="FILE_TICK",
            )
            button_row.operator(
                "smo.clear_actor_registry",
                text="",
                icon="TRASH",
            )

        if status.valid:
            from .registry_report import registry_report_summary_path

            summary_path = registry_report_summary_path(
                romfs_root,
                actor_registry_cache_directory(),
            )
            registry_box.operator(
                "smo.export_actor_registry_report",
                text=(
                    "Refresh Resolution Coverage"
                    if summary_path.is_file()
                    else "Export Resolution Coverage"
                ),
                icon="TEXT",
            )

            if summary_path.is_file():
                registry_box.label(text=summary_path.name, icon="FILE_TEXT")

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        tabs = layout.row(align=True)
        active_section = _PREFERENCES_SECTION

        for section, label, icon in PREFERENCES_SECTIONS:
            operator = tabs.operator(
                "smo.set_preferences_section",
                text=label,
                icon=icon,
                depress=active_section == section,
            )
            operator.section = section

        if active_section == "TEXTURE_CACHE":
            SMOAddonPreferences._draw_texture_cache(self, layout)
            return

        if active_section == "ACTOR_REGISTRY":
            SMOAddonPreferences._draw_actor_registry(self, layout)
            return

        import_box = layout.box()
        import_box.label(text="Import", icon="IMPORT")
        import_box.label(
            text="The ROMFS path is selected in the Odyssey sidebar.",
            icon="FILE_FOLDER",
        )
        import_box.prop(self, "apply_custom_normals")
        import_box.prop(self, "import_armatures")
        shader_box = layout.box()
        shader_box.label(text="Experimental Shaders", icon="SHADING_RENDERED")
        shader_box.prop(self, "experimental_cloth_nov")


class SMOProperties(PropertyGroup):
    romfs_path: StringProperty(
        name="ROMFS",
        description="Extracted Super Mario Odyssey romfs folder",
        subtype="DIR_PATH",
        update=on_romfs_path_changed,
    )

    kingdom: EnumProperty(
        name="Stage",
        description="StageData stage to import",
        items=kingdom_enum_items,
        update=on_kingdom_changed,
    )

    scenario: EnumProperty(
        name="Scenario",
        description="Scenario to import",
        items=scenario_enum_items,
    )

    import_stage_lighting: BoolProperty(
        name="Stage lighting",
        description=(
            "Make the imported stage's graphics preset the active Blender "
            "World and Sun, replacing the current scene lighting, and "
            "convert supported placed stage lights directly from game values"
        ),
        default=True,
    )

    include_environment: BoolProperty(
        name="Environment",
        description="Import models classified as environment",
        default=True,
    )

    include_characters: BoolProperty(
        name="Characters",
        description="Import characters and enemies",
        default=True,
    )

    include_gameplay: BoolProperty(
        name="Gameplay",
        description="Import gameplay objects and mechanisms",
        default=True,
    )

    include_collectibles: BoolProperty(
        name="Collectibles",
        description="Import coins, moons, keys, and other collectibles",
        default=True,
    )

    include_effects: BoolProperty(
        name="Effects",
        description="Import effect and runtime-visual placements",
        default=True,
    )

    include_audio: BoolProperty(
        name="Audio",
        description="Import audio placements",
        default=False,
    )

    include_technical: BoolProperty(
        name="Technical",
        description=(
            "Import areas, cameras, helpers, debug objects, and placed lights"
        ),
        default=False,
    )

    include_unclassified: BoolProperty(
        name="Unclassified",
        description="Import unresolved or intentionally unclassified objects",
        default=False,
    )
    worlds_loaded: BoolProperty(
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    load_error: StringProperty(
        default="",
        options={"HIDDEN", "SKIP_SAVE"},
    )

    scenario_error: StringProperty(
        default="",
        options={"HIDDEN", "SKIP_SAVE"},
    )


def get_or_create_child_collection(
    parent: bpy.types.Collection,
    name: str,
) -> bpy.types.Collection:
    collection = parent.children.get(name)

    if collection is not None:
        collection["smo_import_generated"] = True
        return collection

    collection = bpy.data.collections.get(name)

    if collection is None:
        collection = bpy.data.collections.new(name)

    collection["smo_import_generated"] = True
    parent.children.link(collection)
    return collection


def get_or_create_scene_collection(
    scene: bpy.types.Scene,
    name: str,
) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)

    if collection is None:
        collection = bpy.data.collections.new(name)

    if scene.collection.children.get(collection.name) is None:
        scene.collection.children.link(collection)

    return collection


def get_or_create_named_import_collection(
    parent: bpy.types.Collection,
    label: str,
    group_key: str,
) -> bpy.types.Collection:
    collection = next(
        (
            child
            for child in parent.children
            if child.get("smo_import_group_key") == group_key
        ),
        None,
    )

    if collection is None:
        legacy_keys = {group_key}

        if group_key == "CAMERAS_HELPERS":
            legacy_keys.update({"CAMERAS", "HELPERS"})

        collection = next(
            (
                child
                for child in parent.children
                if child.get("smo_import_category") in legacy_keys
                or (
                    bool(child.get("smo_import_generated"))
                    and (
                        child.name == label
                        or child.name.startswith(f"{label} - ")
                    )
                )
            ),
            None,
        )

    if collection is None:
        collection = bpy.data.collections.new(label)
        parent.children.link(collection)
    elif collection.name != label:
        collection.name = label

    collection["smo_import_generated"] = True
    collection["smo_import_group"] = True
    collection["smo_import_group_key"] = group_key
    collection["smo_import_category"] = group_key
    return collection


def get_or_create_import_group_collection(
    parent: bpy.types.Collection,
    category: Any,
    scope_name: str,
) -> bpy.types.Collection:
    del scope_name
    category_name = str(category.value)
    label, color_tag, is_extra = IMPORT_COLLECTION_GROUPS[category_name]
    target_parent = parent

    if is_extra:
        target_parent = get_or_create_named_import_collection(
            parent,
            "Extras",
            "EXTRAS",
        )
        target_parent.color_tag = "COLOR_08"

    group_key = (
        "CAMERAS_HELPERS"
        if category_name in {"CAMERAS", "HELPERS"}
        else category_name
    )
    collection = get_or_create_named_import_collection(
        target_parent,
        label,
        group_key,
    )
    collection.color_tag = color_tag
    return collection


def remove_empty_import_collections(
    parent: bpy.types.Collection,
    root_prefix: str,
) -> None:
    for child in tuple(parent.children):
        remove_empty_import_collections(child, root_prefix)

        generated = bool(
            child.get("smo_import_generated")
        ) or child.name.startswith(f"{root_prefix} - ")

        if generated and not child.objects and not child.children:
            parent.children.unlink(child)

            if child.users == 0:
                bpy.data.collections.remove(child)


def iter_collection_tree(
    collection: bpy.types.Collection,
) -> Any:
    yield collection

    for child in collection.children:
        yield from iter_collection_tree(child)


def clear_diagnostic_placements(
    collection: bpy.types.Collection,
    root: bpy.types.Object,
) -> None:
    generated_objects = {
        obj
        for child in iter_collection_tree(collection)
        for obj in child.objects
        if obj.parent == root and obj.get("smo_id")
    }

    for obj in generated_objects:
        bpy.data.objects.remove(obj, do_unlink=True)

    remove_empty_import_collections(collection, collection.name)


def populate_diagnostic_placements(
    collection: bpy.types.Collection,
    root: bpy.types.Object,
    classified_placements: list[Any],
    group_scope: str,
) -> int:
    from .stage_data import game_transform_to_blender

    placement_count = 0

    for classified in classified_placements:
        placement = classified.placement
        stage_layer = placement.stage_layer
        group_collection = get_or_create_import_group_collection(
            collection,
            classified.category,
            group_scope,
        )
        placement_object = bpy.data.objects.new(
            (
                f"{placement.identifier} "
                f"{placement.unit_config_name} [{stage_layer}]"
            ),
            None,
        )
        placement_object.empty_display_type = "CUBE"
        placement_object.empty_display_size = 0.5
        group_collection.objects.link(placement_object)

        transform = game_transform_to_blender(
            placement.translate,
            placement.rotate,
            placement.scale,
            placement.rotation_quaternion,
        )
        placement_object.parent = root
        placement_object.location = transform.location
        placement_object.rotation_mode = "QUATERNION"
        placement_object.rotation_quaternion = transform.rotation_quaternion
        placement_object.scale = transform.scale
        placement_object["smo_id"] = placement.identifier
        placement_object["smo_unit_config_name"] = (
            placement.unit_config_name
        )
        placement_object["smo_model_name"] = placement.model_name or ""
        placement_object["smo_category"] = placement.category
        placement_object["smo_import_category"] = classified.category.value
        placement_object["smo_stage_layer"] = placement.stage_layer
        placement_object["smo_source_stage_name"] = (
            placement.source_stage_name
        )
        placement_object["smo_zone_path"] = json.dumps(
            placement.zone_path
        )
        placement_object["smo_layer_config_name"] = (
            placement.layer_config_name
        )
        placement_object["smo_placement_file_name"] = (
            placement.placement_file_name
        )
        placement_object["smo_parameter_config_name"] = str(
            placement.unit_config.get("ParameterConfigName", "")
        )
        placement_object["smo_translate"] = [
            placement.translate.x,
            placement.translate.y,
            placement.translate.z,
        ]
        placement_object["smo_rotate_degrees"] = [
            placement.rotate.x,
            placement.rotate.y,
            placement.rotate.z,
        ]
        placement_object["smo_scale"] = [
            placement.scale.x,
            placement.scale.y,
            placement.scale.z,
        ]
        placement_object["smo_links"] = json.dumps(
            placement.links,
            sort_keys=True,
        )
        placement_object["smo_is_link_destination"] = (
            placement.is_link_destination
        )
        placement_object["smo_is_root_placement"] = placement.is_root
        placement_object["smo_has_direct_model"] = (
            classified.resource.has_model
        )
        placement_object["smo_resource_source_field"] = (
            classified.resource.source_field or ""
        )
        placement_object["smo_resource_archive"] = (
            str(classified.resource.archive_path or "")
        )
        placement_object["smo_bfres_files"] = json.dumps(
            classified.resource.bfres_files
        )
        placement_count += 1

    return placement_count


def has_valid_selection(settings: Any) -> bool:
    return bool(
        settings
        and settings.worlds_loaded
        and settings.kingdom in _WORLD_BY_STAGE
        and any(
            settings.scenario == item[0] and item[0] != "__NONE__"
            for item in _SCENARIO_ENUM_ITEMS
        )
    )


class SMO_OT_reload_stages(Operator):
    bl_idname = "smo.reload_stages"
    bl_label = "Reload Stages"
    bl_description = "Reload stage and scenario lists from the selected ROMFS"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if static_import_is_running():
            cls.poll_message_set("Wait for the current stage import to finish")
            return False

        if actor_registry_report_is_running():
            cls.poll_message_set("Wait for the actor registry report to finish")
            return False

        settings = getattr(context.scene, "smo_settings", None)

        if settings is None or not str(settings.romfs_path).strip():
            cls.poll_message_set("Choose the extracted ROMFS folder first")
            return False

        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.smo_settings
        from .actor_registry import clear_actor_registry_runtime_cache
        from .object_data import clear_object_data_index_cache

        clear_actor_registry_runtime_cache()
        clear_object_data_index_cache()

        if not load_worlds(settings):
            self.report(
                {"ERROR"},
                settings.load_error
                or "Could not load stages from the selected ROMFS.",
            )
            return {"CANCELLED"}

        preferences = get_addon_preferences(context)

        if preferences is not None:
            preferences.romfs_path = settings.romfs_path

        self.report({"INFO"}, f"Reloaded {len(_KINGDOM_ENUM_ITEMS)} stages.")
        return {"FINISHED"}



def _tag_preferences_redraw(context: bpy.types.Context | None = None) -> None:
    window_manager = getattr(context or bpy.context, "window_manager", None)

    if window_manager is None:
        return

    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == "PREFERENCES":
                area.tag_redraw()


class SMO_OT_open_texture_cache(Operator):
    bl_idname = "smo.open_texture_cache"
    bl_label = "Open Texture Cache"
    bl_description = "Open the persistent decoded-texture cache folder"

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            path = texture_cache_directory(
                get_addon_preferences(context),
                create=True,
            )
            bpy.ops.wm.path_open(filepath=str(path))
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not open texture cache: {exc}")
            return {"CANCELLED"}


class SMO_OT_refresh_texture_cache_status(Operator):
    bl_idname = "smo.refresh_texture_cache_status"
    bl_label = "Refresh Texture Cache Statistics"
    bl_description = "Count cached textures and calculate their disk usage"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from .texture_cache import texture_cache_status

        try:
            path = texture_cache_directory(get_addon_preferences(context))
            status = texture_cache_status(path, refresh=True)
            _tag_preferences_redraw(context)
            self.report({"INFO"}, status.message)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not inspect texture cache: {exc}")
            return {"CANCELLED"}


class SMO_OT_clear_texture_cache(Operator):
    bl_idname = "smo.clear_texture_cache"
    bl_label = "Clear Texture Cache"
    bl_description = "Permanently remove decoded textures from the cache"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if static_import_is_running():
            cls.poll_message_set("Wait for the current stage import to finish")
            return False

        return True

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        from .texture_cache import clear_texture_cache

        try:
            path = texture_cache_directory(get_addon_preferences(context))
            removed = clear_texture_cache(path)
            _tag_preferences_redraw(context)
            self.report(
                {"INFO"},
                "Texture cache cleared."
                if removed
                else "Texture cache is empty.",
            )
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not clear texture cache: {exc}")
            return {"CANCELLED"}


class SMO_OT_refresh_actor_registry_status(Operator):
    bl_idname = "smo.refresh_actor_registry_status"
    bl_label = "Refresh Actor Registry Status"
    bl_description = "Validate the registry against the current ROMFS contents"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from .actor_registry import registry_file_status

        preferences = get_addon_preferences(context)

        try:
            romfs_root = resolve_romfs_root(preferences.romfs_path)
            status = registry_file_status(
                romfs_root,
                actor_registry_cache_directory(),
                refresh=True,
            )
            _tag_preferences_redraw(context)
            self.report(
                {"INFO"} if status.valid else {"WARNING"},
                status.message,
            )
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not validate actor registry: {exc}")
            return {"CANCELLED"}


class SMO_OT_build_actor_registry(Operator):
    bl_idname = "smo.build_actor_registry"
    bl_label = "Build Actor Registry"
    bl_description = (
        "Scan every StageData scenario and build a local registry of observed "
        "UnitConfigName, ParameterConfigName and ModelName relationships"
    )

    _timer: Any | None = None
    _builder: Any | None = None
    _progress_started = False
    _cache_directory: Path | None = None
    _romfs_root: Path | None = None

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if static_import_is_running():
            cls.poll_message_set("Wait for the current stage import to finish")
            return False

        if actor_registry_build_is_running():
            cls.poll_message_set("An actor registry build is already running")
            return False

        if actor_registry_report_is_running():
            cls.poll_message_set("Wait for the actor registry report to finish")
            return False

        preferences = get_addon_preferences(context)
        saved_path = str(getattr(preferences, "romfs_path", "")).strip()

        if not saved_path:
            cls.poll_message_set("Choose a valid ROMFS in the Odyssey sidebar first")
            return False

        try:
            resolve_romfs_root(saved_path)
        except Exception as exc:
            cls.poll_message_set(str(exc))
            return False

        return True

    def _cleanup(self, context: bpy.types.Context) -> None:
        global _ACTOR_REGISTRY_BUILD_OPERATOR

        window_manager = context.window_manager

        if self._timer is not None:
            window_manager.event_timer_remove(self._timer)
            self._timer = None

        if self._progress_started:
            window_manager.progress_end()
            self._progress_started = False

        if _ACTOR_REGISTRY_BUILD_OPERATOR is self:
            _ACTOR_REGISTRY_BUILD_OPERATOR = None

        _tag_preferences_redraw(context)

    def _complete(self, context: bpy.types.Context) -> set[str]:
        from .actor_registry import save_actor_registry
        from .object_data import clear_object_data_index_cache
        from .registry_report import registry_report_path

        try:
            registry = self._builder.finish()
            path = save_actor_registry(
                registry,
                self._romfs_root,
                self._cache_directory,
            )
            stale_report = registry_report_path(
                self._romfs_root,
                self._cache_directory,
            )

            if stale_report.is_file():
                stale_report.unlink()

            stale_text = bpy.data.texts.get(stale_report.name)

            if (
                stale_text is not None
                and stale_text.get("smo_actor_registry_report")
            ):
                bpy.data.texts.remove(stale_text)

            configure_actor_registry_from_preferences(
                get_addon_preferences(context)
            )
            clear_object_data_index_cache()
            error_suffix = (
                f"; {len(registry.build_errors)} archives had errors"
                if registry.build_errors
                else ""
            )
            self.report(
                {"WARNING" if registry.build_errors else "INFO"},
                (
                    f"Built {registry.actor_signature_count} actor signatures "
                    f"from {registry.archives_scanned} archives{error_suffix}. "
                    f"Saved to {path}."
                ),
            )
            return {"FINISHED"}
        except Exception as exc:
            print("[Odyssey Toolkit] Failed to save actor registry:")
            traceback.print_exc()
            self.report({"ERROR"}, f"Could not save actor registry: {exc}")
            return {"CANCELLED"}
        finally:
            self._cleanup(context)

    def _cancel_build(self, context: bpy.types.Context) -> set[str]:
        self._cleanup(context)
        self.report(
            {"INFO"},
            "Actor registry build cancelled; the previous registry was kept.",
        )
        return {"CANCELLED"}

    def _start(self, context: bpy.types.Context) -> set[str]:
        global _ACTOR_REGISTRY_BUILD_OPERATOR
        global _ACTOR_REGISTRY_CANCEL_REQUESTED
        from .actor_registry import ActorRegistryBuilder

        preferences = get_addon_preferences(context)

        try:
            self._romfs_root = resolve_romfs_root(preferences.romfs_path)
            self._cache_directory = actor_registry_cache_directory(create=True)
            self._builder = ActorRegistryBuilder(self._romfs_root)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start actor registry build: {exc}")
            return {"CANCELLED"}

        _ACTOR_REGISTRY_CANCEL_REQUESTED = False
        _ACTOR_REGISTRY_BUILD_OPERATOR = self
        window_manager = context.window_manager
        window_manager.progress_begin(0, max(self._builder.total_count, 1))
        self._progress_started = True

        if bpy.app.background or context.window is None:
            while not self._builder.complete:
                self._builder.process_next()
                window_manager.progress_update(self._builder.current_index)

            return self._complete(context)

        self._timer = window_manager.event_timer_add(
            0.01,
            window=context.window,
        )
        window_manager.modal_handler_add(self)
        _tag_preferences_redraw(context)
        return {"RUNNING_MODAL"}

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        return self._start(context)

    def execute(self, context: bpy.types.Context) -> set[str]:
        return self._start(context)

    def modal(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        if event.type == "ESC" or _ACTOR_REGISTRY_CANCEL_REQUESTED:
            return self._cancel_build(context)

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        self._builder.process_next()
        context.window_manager.progress_update(self._builder.current_index)
        _tag_preferences_redraw(context)

        if self._builder.complete:
            return self._complete(context)

        return {"RUNNING_MODAL"}

    def cancel(self, context: bpy.types.Context) -> None:
        self._cleanup(context)


class SMO_OT_cancel_actor_registry_build(Operator):
    bl_idname = "smo.cancel_actor_registry_build"
    bl_label = "Cancel Actor Registry Build"
    bl_description = (
        "Cancel the active registry scan without replacing its previous file"
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not actor_registry_build_is_running():
            cls.poll_message_set("No actor registry build is currently running")
            return False

        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _ACTOR_REGISTRY_CANCEL_REQUESTED
        _ACTOR_REGISTRY_CANCEL_REQUESTED = True
        self.report({"INFO"}, "Actor registry cancellation requested.")
        return {"FINISHED"}


class SMO_OT_export_actor_registry_report(Operator):
    bl_idname = "smo.export_actor_registry_report"
    bl_label = "Export Resolution Coverage"
    bl_description = (
        "Write a concise occurrence-weighted resolution summary and the "
        "complete actor evidence CSV, then load both in Blender's Text Editor"
    )

    _timer: Any | None = None
    _builder: Any | None = None
    _progress_started = False

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if static_import_is_running():
            cls.poll_message_set("Wait for the current stage import to finish")
            return False

        if actor_registry_build_is_running():
            cls.poll_message_set("Wait for the actor registry build to finish")
            return False

        if actor_registry_report_is_running():
            cls.poll_message_set("An actor registry report is already running")
            return False

        preferences = get_addon_preferences(context)
        saved_path = str(getattr(preferences, "romfs_path", "")).strip()

        if not saved_path:
            cls.poll_message_set("No remembered ROMFS is available")
            return False

        try:
            romfs_root = resolve_romfs_root(saved_path)
        except Exception as exc:
            cls.poll_message_set(str(exc))
            return False

        from .actor_registry import registry_file_status

        status = registry_file_status(
            romfs_root,
            actor_registry_cache_directory(),
        )

        if not status.valid:
            cls.poll_message_set("Build or rebuild a valid actor registry first")
            return False

        return True

    def _cleanup(self, context: bpy.types.Context) -> None:
        global _ACTOR_REGISTRY_REPORT_OPERATOR

        window_manager = context.window_manager

        if self._timer is not None:
            window_manager.event_timer_remove(self._timer)
            self._timer = None

        if self._progress_started:
            window_manager.progress_end()
            self._progress_started = False

        if _ACTOR_REGISTRY_REPORT_OPERATOR is self:
            _ACTOR_REGISTRY_REPORT_OPERATOR = None

        _tag_preferences_redraw(context)

    def _complete(self, context: bpy.types.Context) -> set[str]:
        try:
            result = self._builder.finish()

            for report_path, encoding, marker in (
                (
                    result.summary_path,
                    "utf-8",
                    "smo_resolution_coverage",
                ),
                (
                    result.path,
                    "utf-8-sig",
                    "smo_actor_registry_report",
                ),
            ):
                report_text = report_path.read_text(encoding=encoding)
                text_block = bpy.data.texts.get(report_path.name)

                if text_block is None:
                    text_block = bpy.data.texts.new(report_path.name)
                else:
                    text_block.clear()

                text_block.write(report_text)
                text_block[marker] = True
                text_block["smo_report_path"] = str(report_path)
        except Exception as exc:
            print("[Odyssey Toolkit] Failed to export registry report:")
            traceback.print_exc()
            self.report({"ERROR"}, f"Could not export registry report: {exc}")
            return {"CANCELLED"}
        finally:
            self._cleanup(context)

        print(
            "[Odyssey Toolkit] Resolution coverage: "
            f"{result.actionable_signature_count} actionable signatures, "
            f"{result.actionable_occurrence_count} placements -> "
            f"{result.summary_path}"
        )
        print(
            "[Odyssey Toolkit] Registry report groups: "
            + ", ".join(
                f"{name}={count}/{result.occurrence_counts.get(name, 0)}"
                for name, count in result.counts.items()
            )
        )
        self.report(
            {"INFO"},
            (
                f"Exported {result.actionable_signature_count} actionable "
                f"signatures covering {result.actionable_occurrence_count} "
                "placements; summary and full CSV loaded in the Text Editor."
            ),
        )
        return {"FINISHED"}

    def _cancel_report(self, context: bpy.types.Context) -> set[str]:
        self._cleanup(context)
        self.report(
            {"INFO"},
            "Registry report cancelled; the previous CSV was kept.",
        )
        return {"CANCELLED"}

    def _start(self, context: bpy.types.Context) -> set[str]:
        global _ACTOR_REGISTRY_REPORT_CANCEL_REQUESTED
        global _ACTOR_REGISTRY_REPORT_OPERATOR
        from .actor_registry import registry_file_status
        from .registry_report import RegistryReportBuilder

        preferences = get_addon_preferences(context)

        try:
            romfs_root = resolve_romfs_root(preferences.romfs_path)
            cache_directory = actor_registry_cache_directory(create=True)
            status = registry_file_status(romfs_root, cache_directory)

            if not status.valid or status.registry is None:
                raise RuntimeError("Build or rebuild the actor registry first.")

            self._builder = RegistryReportBuilder(
                status.registry,
                romfs_root,
                cache_directory,
            )
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start registry report: {exc}")
            return {"CANCELLED"}

        _ACTOR_REGISTRY_REPORT_CANCEL_REQUESTED = False
        _ACTOR_REGISTRY_REPORT_OPERATOR = self
        window_manager = context.window_manager
        window_manager.progress_begin(0, max(self._builder.total_count, 1))
        self._progress_started = True

        if bpy.app.background or context.window is None:
            while not self._builder.complete:
                self._builder.process_next()
                window_manager.progress_update(self._builder.current_index)

            return self._complete(context)

        self._timer = window_manager.event_timer_add(
            0.01,
            window=context.window,
        )
        window_manager.modal_handler_add(self)
        _tag_preferences_redraw(context)
        return {"RUNNING_MODAL"}

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        return self._start(context)

    def execute(self, context: bpy.types.Context) -> set[str]:
        return self._start(context)

    def modal(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        if (
            event.type == "ESC"
            or _ACTOR_REGISTRY_REPORT_CANCEL_REQUESTED
        ):
            return self._cancel_report(context)

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        try:
            for _ in range(8):
                if self._builder.complete:
                    break

                self._builder.process_next()
        except Exception as exc:
            print("[Odyssey Toolkit] Registry report failed:")
            traceback.print_exc()
            self._cleanup(context)
            self.report({"ERROR"}, f"Registry report failed: {exc}")
            return {"CANCELLED"}

        context.window_manager.progress_update(self._builder.current_index)
        _tag_preferences_redraw(context)

        if self._builder.complete:
            return self._complete(context)

        return {"RUNNING_MODAL"}

    def cancel(self, context: bpy.types.Context) -> None:
        self._cleanup(context)


class SMO_OT_cancel_actor_registry_report(Operator):
    bl_idname = "smo.cancel_actor_registry_report"
    bl_label = "Cancel Actor Registry Report"
    bl_description = (
        "Cancel report generation without replacing its previous CSV"
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not actor_registry_report_is_running():
            cls.poll_message_set("No actor registry report is currently running")
            return False

        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _ACTOR_REGISTRY_REPORT_CANCEL_REQUESTED
        _ACTOR_REGISTRY_REPORT_CANCEL_REQUESTED = True
        self.report({"INFO"}, "Registry report cancellation requested.")
        return {"FINISHED"}

class SMO_OT_clear_actor_registry(Operator):
    bl_idname = "smo.clear_actor_registry"
    bl_label = "Clear Actor Registry"
    bl_description = "Delete the generated registry for the remembered ROMFS"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if actor_registry_build_is_running():
            cls.poll_message_set("Wait for the actor registry build to finish")
            return False

        if actor_registry_report_is_running():
            cls.poll_message_set("Wait for the actor registry report to finish")
            return False

        preferences = get_addon_preferences(context)
        saved_path = str(getattr(preferences, "romfs_path", "")).strip()

        if not saved_path:
            cls.poll_message_set("No remembered ROMFS is available")
            return False

        try:
            romfs_root = resolve_romfs_root(saved_path)
        except Exception as exc:
            cls.poll_message_set(str(exc))
            return False

        from .actor_registry import registry_cache_path

        if not registry_cache_path(
            romfs_root,
            actor_registry_cache_directory(),
        ).is_file():
            cls.poll_message_set("No registry exists for this ROMFS")
            return False

        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        from .actor_registry import remove_actor_registry
        from .object_data import clear_object_data_index_cache
        from .registry_report import (
            registry_report_path,
            registry_report_summary_path,
        )

        preferences = get_addon_preferences(context)
        romfs_root = resolve_romfs_root(preferences.romfs_path)
        cache_directory = actor_registry_cache_directory()
        report_paths = (
            registry_report_path(romfs_root, cache_directory),
            registry_report_summary_path(romfs_root, cache_directory),
        )
        removed = remove_actor_registry(
            romfs_root,
            cache_directory,
        )

        for report_path in report_paths:
            if report_path.is_file():
                report_path.unlink()

            report_text = bpy.data.texts.get(report_path.name)

            if report_text is not None and (
                report_text.get("smo_actor_registry_report")
                or report_text.get("smo_resolution_coverage")
            ):
                bpy.data.texts.remove(report_text)

        clear_object_data_index_cache()

        if not removed:
            self.report({"WARNING"}, "No actor registry was found.")
            return {"CANCELLED"}

        _tag_preferences_redraw(context)
        self.report({"INFO"}, "Cleared the actor registry for this ROMFS.")
        return {"FINISHED"}

class SMO_OT_set_import_categories(Operator):
    bl_idname = "smo.set_import_categories"
    bl_label = "Set Import Categories"
    bl_description = "Select or deselect every stage-import category"

    enabled: BoolProperty(
        name="Enabled",
        default=True,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from .placement_classifier import IMPORT_CATEGORY_FILTERS

        settings = context.scene.smo_settings

        for _, _, property_name in IMPORT_CATEGORY_FILTERS:
            setattr(settings, property_name, self.enabled)

        return {"FINISHED"}

class SMO_OT_cancel_stage_import(Operator):
    bl_idname = "smo.cancel_stage_import"
    bl_label = "Cancel Import"
    bl_description = "Request cancellation of the current stage import"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not static_import_is_running():
            cls.poll_message_set("No stage import is currently running")
            return False

        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not request_static_import_cancel():
            self.report({"WARNING"}, "No stage import is currently running.")
            return {"CANCELLED"}

        self.report({"INFO"}, "Stage import cancellation requested.")
        return {"FINISHED"}


class SMO_OT_import_kingdom(Operator):
    bl_idname = "smo.import_kingdom"
    bl_label = "Import Diagnostic Placements"
    bl_description = (
        "Import every diagnostic placement for the selected stage and scenario"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if static_import_is_running():
            cls.poll_message_set("Wait for the current stage import to finish")
            return False

        settings = getattr(context.scene, "smo_settings", None)

        if not has_valid_selection(settings):
            cls.poll_message_set("Choose a valid ROMFS, stage, and scenario")
            return False

        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        from .performance import (
            PerformanceTimings,
            print_performance_summary,
            reset_active_timings,
            set_active_timings,
        )

        timings = PerformanceTimings()
        import_started = time.perf_counter()
        preparation_started = import_started
        timing_token = set_active_timings(timings)

        try:
            settings = context.scene.smo_settings
            world = _WORLD_BY_STAGE[settings.kingdom]
            scenario_number = int(settings.scenario)
            display_name = str(world["_display_name"])
            stage_name = str(world["Name"])
            romfs_root = resolve_romfs_root(settings.romfs_path)

            from .object_data import get_object_data_index
            from .placement_classifier import classify_stage_scenario
            from .stage_data import read_stage_scenario

            stage_scenario = read_stage_scenario(
                romfs_root,
                stage_name,
                scenario_number,
            )
            classified_placements = classify_stage_scenario(
                stage_scenario,
                get_object_data_index(romfs_root),
            )
            timings.add(
                "preparation_total",
                time.perf_counter() - preparation_started,
            )

            collection_name = f"SMO - {display_name} - Scenario {scenario_number}"
            collection = get_or_create_scene_collection(
                context.scene,
                collection_name,
            )

            root_name = f"{stage_name}_Scenario{scenario_number}"
            root = collection.objects.get(root_name)

            if root is None:
                root = bpy.data.objects.new(root_name, None)
                root.empty_display_type = "PLAIN_AXES"
                root.empty_display_size = 5.0
                collection.objects.link(root)

            root["smo_display_name"] = display_name
            root["smo_world_name"] = str(world.get("WorldName", ""))
            root["smo_stage_name"] = stage_name
            root["smo_scenario"] = scenario_number
            root["smo_total_placement_count"] = len(
                stage_scenario.placements
            )
            root["smo_missing_stage_layers"] = json.dumps(
                stage_scenario.missing_layers
            )
            root["smo_expanded_zone_count"] = len(
                stage_scenario.expanded_zones
            )
            root["smo_expanded_zones"] = json.dumps(
                stage_scenario.expanded_zones
            )
            if "smo_import_preset" in root:
                del root["smo_import_preset"]

            clear_diagnostic_placements(collection, root)
            placement_count = populate_diagnostic_placements(
                collection,
                root,
                classified_placements,
                f"{display_name} S{scenario_number} Diagnostics",
            )
            root["smo_placement_count"] = placement_count

            for obj in context.selected_objects:
                obj.select_set(False)

            root.select_set(True)
            context.view_layer.objects.active = root
            timings.add("import_total", time.perf_counter() - import_started)
            root["smo_performance_timings"] = timings.to_json()
            root["smo_performance_total_seconds"] = timings.seconds(
                "import_total"
            )
            print_performance_summary(
                timings,
                prefix="[Odyssey Toolkit Diagnostics]",
            )

            missing_suffix = ""

            if stage_scenario.missing_layers:
                missing_suffix = (
                    "; unavailable layers: "
                    + ", ".join(stage_scenario.missing_layers)
                )

            self.report(
                {"INFO"},
                (
                    f"Imported {placement_count} of "
                    f"{len(classified_placements)} diagnostic placements for "
                    f"{display_name}, scenario {scenario_number}"
                    f"{missing_suffix}."
                ),
            )
            return {"FINISHED"}

        except Exception as exc:
            print("[Odyssey Toolkit] Failed to import stage placements:")
            traceback.print_exc()
            self.report({"ERROR"}, f"Could not import stage: {exc}")
            return {"CANCELLED"}
        finally:
            reset_active_timings(timing_token)


class SMO_OT_export_model_report(Operator):
    bl_idname = "smo.export_model_report"
    bl_label = "Export Model Report"
    bl_description = (
        "Export model resolution details for every placement in the "
        "selected stage and scenario"
    )

    filepath: StringProperty(
        name="File Path",
        subtype="FILE_PATH",
        options={"SKIP_SAVE"},
    )

    filter_glob: StringProperty(
        default="*.json",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if static_import_is_running():
            cls.poll_message_set("Wait for the current stage import to finish")
            return False

        settings = getattr(context.scene, "smo_settings", None)

        if not has_valid_selection(settings):
            cls.poll_message_set("Choose a valid ROMFS, stage, and scenario")
            return False

        return True

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        settings = context.scene.smo_settings
        world = _WORLD_BY_STAGE[settings.kingdom]
        stage_name = str(world["Name"])
        self.filepath = (
            f"{stage_name}_Scenario{settings.scenario}_ModelReport.json"
        )
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            settings = context.scene.smo_settings
            world = _WORLD_BY_STAGE[settings.kingdom]
            scenario_number = int(settings.scenario)
            display_name = str(world["_display_name"])
            stage_name = str(world["Name"])
            world_name = str(world.get("WorldName", ""))
            romfs_root = resolve_romfs_root(settings.romfs_path)

            from .model_report import (
                build_model_resolution_report,
                model_resolution_report_json,
            )
            from .object_data import get_object_data_index
            from .placement_classifier import classify_stage_scenario
            from .stage_data import read_stage_scenario

            stage_scenario = read_stage_scenario(
                romfs_root,
                stage_name,
                scenario_number,
            )
            classified_placements = classify_stage_scenario(
                stage_scenario,
                get_object_data_index(romfs_root),
                include_suggestions=True,
            )
            report = build_model_resolution_report(
                display_name=display_name,
                world_name=world_name,
                stage_scenario=stage_scenario,
                classified_placements=classified_placements,
            )
            report_path = Path(self.filepath)

            if report_path.suffix.casefold() != ".json":
                report_path = report_path.with_suffix(".json")

            report_path.write_text(
                model_resolution_report_json(report),
                encoding="utf-8",
            )
            summary = report["summary"]["by_resolution_status"]
            self.report(
                {"INFO"},
                (
                    f"Exported {len(classified_placements)} placements: "
                    f"{summary.get('MODEL', 0)} models, "
                    f"{summary.get('ARCHIVE_WITHOUT_MODEL', 0)} archives "
                    f"without models, "
                    f"{summary.get('UNRESOLVED', 0)} unresolved."
                ),
            )
            return {"FINISHED"}

        except Exception as exc:
            print("[Odyssey Toolkit] Failed to export model report:")
            traceback.print_exc()
            self.report({"ERROR"}, f"Could not export model report: {exc}")
            return {"CANCELLED"}


class SMO_PT_kingdom_importer(Panel):
    bl_idname = "SMO_PT_kingdom_importer"
    bl_label = "Stage Importer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Odyssey"


    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        settings = context.scene.smo_settings
        import_running = static_import_is_running()

        source_box = layout.box()
        source_box.label(text="Source", icon="FILE_FOLDER")
        path_row = source_box.row(align=True)
        path_row.enabled = not import_running
        path_row.prop(settings, "romfs_path", text="")
        path_row.operator(
            "smo.reload_stages",
            text="",
            icon="FILE_REFRESH",
        )

        if settings.load_error:
            error_row = source_box.row()
            error_row.alert = True
            error_row.label(text=settings.load_error, icon="ERROR")
            return

        if not settings.worlds_loaded:
            source_box.label(
                text="Choose the extracted ROMFS folder.",
                icon="INFO",
            )
            return

        source_box.label(
            text=f"{len(_WORLD_BY_STAGE)} stages loaded",
            icon="CHECKMARK",
        )
        stage_box = layout.box()
        stage_box.enabled = not import_running
        stage_box.label(text="Stage", icon="WORLD")
        stage_box.prop(settings, "kingdom", text="")
        selected_world = _WORLD_BY_STAGE.get(str(settings.kingdom))

        if selected_world is not None:
            stage_box.label(
                text=f"Internal: {selected_world['Name']}",
                icon="INFO",
            )

        stage_box.label(text="Scenario")
        stage_box.prop(settings, "scenario", text="")
        from .placement_classifier import IMPORT_CATEGORY_FILTERS

        category_header = stage_box.row(align=True)
        category_header.label(
            text="Import Categories",
            icon="OUTLINER_COLLECTION",
        )
        select_all = category_header.operator(
            "smo.set_import_categories",
            text="All",
        )
        select_all.enabled = True
        select_none = category_header.operator(
            "smo.set_import_categories",
            text="None",
        )
        select_none.enabled = False

        for index in range(0, len(IMPORT_CATEGORY_FILTERS), 2):
            category_row = stage_box.row(align=True)

            for _, label, property_name in IMPORT_CATEGORY_FILTERS[
                index : index + 2
            ]:
                category_row.prop(
                    settings,
                    property_name,
                    text=label,
                )

        stage_box.prop(settings, "import_stage_lighting")
        if settings.scenario_error:
            scenario_error_row = stage_box.row()
            scenario_error_row.alert = True
            scenario_error_row.label(
                text=settings.scenario_error,
                icon="ERROR",
            )

        import_box = layout.box()
        import_box.label(text="Import", icon="IMPORT")

        if import_running:
            current, total, status_text = static_import_status()
            import_box.label(
                text=status_text or "Preparing stage data",
                icon="TIME",
            )

            if total:
                import_box.label(
                    text=f"Placement {min(current + 1, total)} of {total}",
                )

            cancel_row = import_box.row()
            cancel_row.scale_y = 1.25
            cancel_row.operator("smo.cancel_stage_import", icon="CANCEL")
        else:
            import_row = import_box.row()
            import_row.scale_y = 1.5
            import_row.operator(
                "smo.import_static_models",
                text="Import Stage",
                icon="MESH_DATA",
            )


class SMO_PT_models_and_animations(Panel):
    bl_idname = "SMO_PT_models_and_animations"
    bl_label = "Assets & Animations"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Odyssey"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        import_running = static_import_is_running()

        import_row = layout.row()
        import_row.enabled = not import_running
        import_row.scale_y = 1.25
        import_row.operator(
            "smo.import_test_model",
            text="Import Standalone Model",
            icon="FILE_3D",
        )

        if import_running:
            layout.label(text="Stage import in progress", icon="INFO")


def _active_stage_import_root(context: bpy.types.Context) -> Any | None:
    candidate = getattr(context, "active_object", None)

    while candidate is not None:
        if (
            candidate.get("smo_stage_name")
            and candidate.get("smo_import_status")
        ):
            return candidate
        candidate = getattr(candidate, "parent", None)

    return None


def _diagnostic_entry_count(value: object) -> int:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    return len(decoded) if isinstance(decoded, (dict, list)) else 0


class SMO_PT_diagnostics(Panel):
    bl_idname = "SMO_PT_diagnostics"
    bl_label = "Diagnostics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Odyssey"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.operator(
            "smo.export_model_report",
            text="Export Current Stage Report",
            icon="FILE_TEXT",
        )
        root = _active_stage_import_root(context)

        if root is None:
            layout.label(
                text="Select an object from an imported stage for its summary.",
                icon="INFO",
            )
            return

        summary = layout.box()
        summary.label(
            text=(
                f"{root.get('smo_display_name', root.name)} - "
                f"Scenario {root.get('smo_scenario', '?')}"
            ),
            icon="WORLD",
        )
        summary.label(
            text=f"Placements: {int(root.get('smo_placement_count', 0))}"
        )
        summary.label(
            text=(
                "Mesh objects: "
                f"{int(root.get('smo_static_mesh_object_count', 0))}"
            )
        )
        summary.label(
            text=(
                "Diagnostic fallbacks: "
                f"{int(root.get('smo_cube_fallback_count', 0))}"
            ),
            icon="INFO",
        )
        unsupported = int(root.get("smo_unsupported_asset_count", 0))
        texture_errors = _diagnostic_entry_count(
            root.get("smo_texture_errors", "{}")
        )
        normal_failures = int(
            root.get("smo_custom_normal_failure_count", 0)
        )
        warning_count = unsupported + texture_errors + normal_failures
        warning_row = summary.row()
        warning_row.alert = warning_count > 0
        warning_row.label(
            text=f"Import warnings: {warning_count}",
            icon="ERROR" if warning_count else "CHECKMARK",
        )


@persistent
def smo_load_post(_dummy: Any) -> None:
    scene = getattr(bpy.context, "scene", None)

    if scene is None or not hasattr(scene, "smo_settings"):
        return

    settings = scene.smo_settings

    if settings.romfs_path:
        if load_worlds(settings):
            preferences = get_addon_preferences()

            if preferences is not None:
                preferences.romfs_path = settings.romfs_path
    else:
        restore_saved_romfs_path(settings)


from .static_model_import import (
    SMO_OT_import_static_models,
    request_static_import_cancel,
    static_import_is_running,
    static_import_status,
)
from .standalone_import import SMO_OT_import_test_model, draw_import_menu
from .smd_animation import (
    SMO_OT_import_smd_animation,
    draw_smd_import_menu,
)
from .bfres_animation_import import (
    SMO_OT_apply_bfres_animation,
    SMO_OT_refresh_bfres_animations,
    SMO_PT_bfres_animations,
    register_bfres_animation_properties,
    unregister_bfres_animation_properties,
)
from .bfres_camera_import import (
    SMO_OT_apply_bfres_camera_animation,
    SMO_OT_refresh_bfres_camera_animations,
    SMO_PT_bfres_camera_animations,
    register_bfres_camera_properties,
    unregister_bfres_camera_properties,
)


CLASSES = (
    SMO_OT_set_preferences_section,
    SMOAddonPreferences,
    SMOProperties,
    SMO_OT_open_texture_cache,
    SMO_OT_refresh_texture_cache_status,
    SMO_OT_clear_texture_cache,
    SMO_OT_reload_stages,
    SMO_OT_refresh_actor_registry_status,
    SMO_OT_build_actor_registry,
    SMO_OT_cancel_actor_registry_build,
    SMO_OT_export_actor_registry_report,
    SMO_OT_cancel_actor_registry_report,
    SMO_OT_clear_actor_registry,
    SMO_OT_set_import_categories,
    SMO_OT_cancel_stage_import,
    SMO_OT_import_kingdom,
    SMO_OT_import_static_models,
    SMO_OT_import_test_model,
    SMO_OT_import_smd_animation,
    SMO_OT_refresh_bfres_animations,
    SMO_OT_apply_bfres_animation,
    SMO_OT_refresh_bfres_camera_animations,
    SMO_OT_apply_bfres_camera_animation,
    SMO_OT_export_model_report,
    SMO_PT_kingdom_importer,
    SMO_PT_models_and_animations,
    SMO_PT_diagnostics,
    SMO_PT_bfres_animations,
    SMO_PT_bfres_camera_animations,
)


def register() -> None:
    reset_enum_items()

    for cls in CLASSES:
        bpy.utils.register_class(cls)

    configure_actor_registry_from_preferences()
    bpy.types.Scene.smo_settings = PointerProperty(type=SMOProperties)
    register_bfres_animation_properties()
    register_bfres_camera_properties()
    bpy.types.TOPBAR_MT_file_import.append(draw_import_menu)
    bpy.types.TOPBAR_MT_file_import.append(draw_smd_import_menu)

    if smo_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(smo_load_post)

    scene = getattr(bpy.context, "scene", None)

    if scene is not None and hasattr(scene, "smo_settings"):
        settings = scene.smo_settings

        if settings.romfs_path:
            load_worlds(settings)
        elif not restore_saved_romfs_path(settings):
            settings.worlds_loaded = False
            settings.load_error = ""
            settings.scenario_error = ""


def unregister() -> None:
    from .actor_registry import configure_actor_registry
    from .object_data import clear_object_data_index_cache

    configure_actor_registry(None, enabled=False)
    clear_object_data_index_cache()
    bpy.types.TOPBAR_MT_file_import.remove(draw_smd_import_menu)
    bpy.types.TOPBAR_MT_file_import.remove(draw_import_menu)

    if smo_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(smo_load_post)

    unregister_bfres_camera_properties()
    unregister_bfres_animation_properties()

    if hasattr(bpy.types.Scene, "smo_settings"):
        del bpy.types.Scene.smo_settings

    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

    _WORLD_BY_STAGE.clear()
    _STAGE_SCENARIO_NUMBERS.clear()
    reset_enum_items()
    clear_preview_collections()


if __name__ == "__main__":
    register()
