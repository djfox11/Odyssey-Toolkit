from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "odyssey_toolkit" / "static_model_import.py"


def load_placement_property_writer() -> Any:
    module = ast.parse(
        SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(SOURCE_PATH),
    )
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_set_placement_collection_properties"
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
    namespace: dict[str, object] = {"Any": Any, "json": json}
    exec(compile(extracted, str(SOURCE_PATH), "exec"), namespace)
    return namespace["_set_placement_collection_properties"]


set_placement_collection_properties = load_placement_property_writer()


def classified_placement() -> object:
    component = SimpleNamespace(
        archive_path=Path("D:/romfs/ObjectData/CapFlowerBloom.szs"),
        requested_name="CapFlowerBloom",
        bfres_files=("CapFlowerBloom.bfres",),
    )
    placement = SimpleNamespace(
        identifier="2183",
        unit_config_name="CapFlowerBloom",
        unit_config={"ParameterConfigName": "CapFlowerBloom"},
        model_name=None,
        stage_layer="Common",
        source_stage_name="PeachWorldHomeStage",
        raw={},
        zone_path=(),
    )
    resource = SimpleNamespace(
        source_field="UnitConfigName",
        archive_path=component.archive_path,
        bfres_files=component.bfres_files,
        model_resources=(component,),
    )
    expectation = SimpleNamespace(
        expectation=SimpleNamespace(value="EXPECTED_MODEL"),
        confidence="HIGH",
        reasons=("fixture",),
    )
    return SimpleNamespace(
        placement=placement,
        resource=resource,
        category=SimpleNamespace(value="GAMEPLAY"),
        model_expectation=expectation,
    )


class PlacementCollectionMetadataTests(unittest.TestCase):
    def test_placement_and_resource_metadata_is_written_to_collection(
        self,
    ) -> None:
        collection: dict[str, object] = {}

        set_placement_collection_properties(
            collection,
            classified_placement(),
            "STATIC_MODEL",
        )

        self.assertEqual(collection["smo_id"], "2183")
        self.assertEqual(
            collection["smo_unit_config_name"],
            "CapFlowerBloom",
        )
        self.assertEqual(collection["smo_representation"], "STATIC_MODEL")
        self.assertEqual(collection["smo_import_category"], "GAMEPLAY")
        self.assertIn(
            "CapFlowerBloom.szs",
            str(collection["smo_resource_archive"]),
        )
        self.assertFalse(collection["smo_procedural_ocean"])

    def test_fallback_diagnostics_are_cleared_after_successful_reimport(
        self,
    ) -> None:
        collection: dict[str, object] = {}
        classified = classified_placement()

        set_placement_collection_properties(
            collection,
            classified,
            "CUBE_FALLBACK",
            "No matching ObjectData archive",
        )
        self.assertIn("smo_model_expectation", collection)

        set_placement_collection_properties(
            collection,
            classified,
            "STATIC_MODEL",
        )

        self.assertEqual(collection["smo_fallback_reason"], "")
        self.assertNotIn("smo_model_expectation", collection)
        self.assertNotIn("smo_model_expectation_confidence", collection)
        self.assertNotIn("smo_model_expectation_reasons", collection)


if __name__ == "__main__":
    unittest.main()
