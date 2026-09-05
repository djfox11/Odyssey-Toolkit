from __future__ import annotations

from types import SimpleNamespace
import unittest

from pure_module_loader import load_toolkit_module


model_expectation = load_toolkit_module("model_expectation")
placement_classifier = load_toolkit_module("placement_classifier")
object_data = load_toolkit_module("object_data")
stage_data = load_toolkit_module("stage_data")


def placement(
    unit_name: str,
    *,
    category: str = "ObjList",
    layer: str = "Map",
    model_name: str | None = None,
    parameter_name: str = "",
) -> object:
    return stage_data.StagePlacement(
        identifier="fixture-1",
        unit_config_name=unit_name,
        model_name=model_name,
        category=category,
        stage_layer=layer,
        placement_file_name="FixtureMap.byml",
        layer_config_name="Common",
        translate=stage_data.Vector3(0.0, 0.0, 0.0),
        rotate=stage_data.Vector3(0.0, 0.0, 0.0),
        scale=stage_data.Vector3(1.0, 1.0, 1.0),
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        links={},
        unit_config={"ParameterConfigName": parameter_name},
        is_link_destination=False,
        is_root=True,
        raw={},
    )


def resource(*, has_model: bool) -> object:
    return object_data.ObjectResource(
        source_field=None,
        requested_name=None,
        archive_path=None,
        bfres_files=("Fixture.bfres",) if has_model else (),
    )


class ModelExpectationTests(unittest.TestCase):
    def test_high_confidence_model_and_non_mesh_signals_win(self) -> None:
        model = model_expectation.assess_model_expectation(
            unit_config_name="Fixture",
            resource=SimpleNamespace(has_model=True),
        )
        sound = model_expectation.assess_model_expectation(
            unit_config_name="Mystery",
            stage_layers=("Sound",),
        )

        self.assertEqual(
            model.expectation,
            model_expectation.ModelExpectation.MODEL_EXPECTED,
        )
        self.assertEqual(model.confidence, "HIGH")
        self.assertEqual(
            sound.expectation,
            model_expectation.ModelExpectation.CONFIRMED_NON_MESH,
        )
        self.assertEqual(sound.confidence, "HIGH")

    def test_runtime_and_visual_category_signals_are_classified(self) -> None:
        runtime = model_expectation.assess_model_expectation(
            unit_config_name="Mystery",
            archive_metadata=SimpleNamespace(
                init_model_references=(),
                sub_actor_model_names=("ComposedPart",),
                byml_files=(),
            ),
        )
        visual = model_expectation.assess_model_expectation(
            unit_config_name="Mystery",
            import_category="CHARACTERS",
        )

        self.assertEqual(
            runtime.expectation,
            model_expectation.ModelExpectation.RUNTIME_VISUAL,
        )
        self.assertEqual(runtime.confidence, "HIGH")
        self.assertEqual(
            visual.expectation,
            model_expectation.ModelExpectation.MODEL_EXPECTED,
        )
        self.assertEqual(visual.confidence, "MEDIUM")

    def test_unknown_is_retained_without_reliable_evidence(self) -> None:
        assessment = model_expectation.assess_model_expectation(
            unit_config_name="Mystery",
        )
        self.assertEqual(
            assessment.expectation,
            model_expectation.ModelExpectation.UNKNOWN,
        )
        self.assertEqual(assessment.confidence, "LOW")


class PlacementClassificationTests(unittest.TestCase):
    def test_placement_rules_cover_each_category_family(self) -> None:
        cases = (
            (placement("SoundEmitter", layer="Sound"), False, "AUDIO"),
            (placement("Fixture", category="DebugList"), False, "DEBUG"),
            (placement("CoinPile"), True, "COLLECTIBLES"),
            (placement("DokanEntrance"), True, "GAMEPLAY"),
            (placement("Fixture", category="AreaList"), False, "AREAS"),
            (placement("FollowCamera"), False, "CAMERAS"),
            (placement("NpcTraveller"), True, "CHARACTERS"),
            (placement("StageSwitchController"), False, "HELPERS"),
            (placement("OldTree", model_name="OldTree"), True, "ENVIRONMENT"),
            (placement("EffectMist"), False, "EFFECTS"),
            (placement("MysteryVisible"), True, "UNKNOWN_MODEL"),
            (placement("MysteryLogic"), False, "UNKNOWN_MODELLESS"),
        )

        for fixture, has_model, expected in cases:
            with self.subTest(unit=fixture.unit_config_name):
                actual = placement_classifier.classify_placement(
                    fixture,
                    resource(has_model=has_model),
                )
                self.assertEqual(actual.value, expected)

    def test_explicit_override_is_stable(self) -> None:
        actual = placement_classifier.classify_placement(
            placement("OceanWave"),
            resource(has_model=False),
        )
        self.assertEqual(
            actual,
            placement_classifier.PlacementCategory.ENVIRONMENT,
        )


if __name__ == "__main__":
    unittest.main()
