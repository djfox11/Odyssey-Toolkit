from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_function(path: Path, name: str, globals_: dict[str, object]):
    """Load one source function without importing Blender-only modules."""
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    extracted = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            function,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(extracted)
    namespace = dict(globals_)
    exec(compile(extracted, str(path), "exec"), namespace)
    return namespace[name]


def parsed_source(relative_path: str) -> ast.Module:
    path = ROOT / relative_path
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def class_node(module: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


class AnimationDropdownCacheTests(unittest.TestCase):
    def test_enum_items_are_reused_without_reloading_sources(self) -> None:
        calls = 0
        animation = SimpleNamespace(
            name="Idle",
            frame_count=59,
            looping=True,
            skeletal=None,
            visibility=None,
            materials=(),
        )
        source = SimpleNamespace(
            bfres_name="Actor.bfres",
            animations=(animation,),
        )

        def animation_sources(_armature):
            nonlocal calls
            calls += 1
            return (source,)

        armature = SimpleNamespace(
            name="Actor_Armature",
            data=SimpleNamespace(bones=()),
            get=lambda key, default=None: {
                "smo_armature_generated": True,
                "smo_rig_key": "rig-key",
            }.get(key, default),
        )
        context = SimpleNamespace(armature=armature)
        item_cache: dict[object, object] = {}
        enum_items = load_function(
            ROOT / "odyssey_toolkit" / "bfres_animation_import.py",
            "bfres_animation_enum_items",
            {
                "_target_armature": lambda current: current.armature,
                "_animation_sources": animation_sources,
                "_source_material_names": lambda _armature: set(),
                "_animation_identifier": lambda source, animation: (
                    f"{source}:{animation}"
                ),
                "_ENUM_ITEM_CACHE": item_cache,
            },
        )

        first = enum_items(None, context)
        second = enum_items(None, context)

        self.assertIs(first, second)
        self.assertEqual(calls, 1)


class SearchableDropdownTests(unittest.TestCase):
    def test_camera_operator_callback_uses_the_context_scene(self) -> None:
        scene = object()
        context = SimpleNamespace(scene=scene)
        callback = load_function(
            ROOT / "odyssey_toolkit" / "bfres_camera_import.py",
            "bfres_camera_search_enum_items",
            {
                "bfres_camera_enum_items": (
                    lambda callback_scene, callback_context: (
                        (callback_scene, callback_context),
                    )
                ),
            },
        )

        self.assertEqual(callback(None, None), ())
        self.assertEqual(callback(None, context), ((scene, context),))

    def test_every_enum_selector_uses_blenders_search_popup(self) -> None:
        selectors = (
            ("odyssey_toolkit/__init__.py", "SMO_OT_select_kingdom"),
            ("odyssey_toolkit/__init__.py", "SMO_OT_select_scenario"),
            (
                "odyssey_toolkit/bfres_animation_import.py",
                "SMO_OT_select_bfres_animation",
            ),
            (
                "odyssey_toolkit/bfres_camera_import.py",
                "SMO_OT_select_bfres_camera_animation",
            ),
        )

        for relative_path, selector_name in selectors:
            with self.subTest(selector=selector_name):
                selector = class_node(parsed_source(relative_path), selector_name)
                invoke = next(
                    node
                    for node in selector.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "invoke"
                )
                self.assertTrue(
                    any(
                        isinstance(node, ast.Attribute)
                        and node.attr == "invoke_search_popup"
                        for node in ast.walk(invoke)
                    )
                )
                self.assertTrue(
                    any(
                        isinstance(node, ast.Return)
                        and isinstance(node.value, ast.Set)
                        and any(
                            isinstance(element, ast.Constant)
                            and element.value == "RUNNING_MODAL"
                            for element in node.value.elts
                        )
                        for node in ast.walk(invoke)
                    )
                )

    def test_panels_use_search_operators_instead_of_enum_props(self) -> None:
        expected = {
            "odyssey_toolkit/__init__.py": (
                "smo.select_kingdom",
                "smo.select_scenario",
            ),
            "odyssey_toolkit/bfres_animation_import.py": (
                "smo.select_bfres_animation",
            ),
            "odyssey_toolkit/bfres_camera_import.py": (
                "smo.select_bfres_camera_animation",
            ),
        }

        for relative_path, operator_ids in expected.items():
            source = (ROOT / relative_path).read_text(encoding="utf-8")

            for operator_id in operator_ids:
                with self.subTest(operator=operator_id):
                    self.assertIn(f'"{operator_id}"', source)

        self.assertNotIn(
            'stage_box.prop(settings, "kingdom"',
            (ROOT / "odyssey_toolkit/__init__.py").read_text(
                encoding="utf-8"
            ),
        )
        self.assertNotIn(
            'stage_box.prop(settings, "scenario"',
            (ROOT / "odyssey_toolkit/__init__.py").read_text(
                encoding="utf-8"
            ),
        )


class StandaloneImportStructureTests(unittest.TestCase):
    def test_standalone_import_creates_no_collection_or_root_empty(self) -> None:
        source = (ROOT / "odyssey_toolkit/standalone_import.py").read_text(
            encoding="utf-8"
        )
        module = parsed_source("odyssey_toolkit/standalone_import.py")
        operator = class_node(module, "SMO_OT_import_test_model")
        method_names = {
            node.name for node in operator.body if isinstance(node, ast.FunctionDef)
        }
        execute = next(
            node
            for node in operator.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )

        self.assertNotIn("_test_collection", method_names)
        self.assertNotIn("bpy.data.collections.new", source)
        self.assertTrue(
            any(
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "context"
                and node.attr == "collection"
                for node in ast.walk(execute)
            )
        )
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "new"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value is None
                for node in ast.walk(execute)
            ),
            "Standalone imports must not create a root empty",
        )


if __name__ == "__main__":
    unittest.main()
