from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .stage_data import StagePlacement


ACTOR_RESOURCE_ALIASES = {
    "CapFlower": "CapFlowerBloom",
    "Chorobon2D3D": "Chorobon2D",
    "CoinStackGroup": "CoinStack",
    "DemoActorCapManHero": "CapManHero",
    "DemoActorCapManHeroine": "CapManHeroine",
    "DemoActorKoopaShip": "KoopaShip",
    "DemoActorPeach": "Peach",
    "DemoActorShineTower": "ShineTower",
    "DemoPeachWedding": "PeachWedding",
    "ElectricWireKoopa": "ElectricWireMoverKoopa",
    "FigureWalkingNpc": "NokonokoNpc",
    "FireBrosPossessed": "FireBros",
    "HackCar": "Car",
    "GrowPlantSeedTop": "GrowPlantPartsTop",
    "HammerBrosPossessed": "HammerBros",
    "KoopaChurch": "Koopa",
    "KoopaLv1": "Koopa",
    "KoopaLv2": "Koopa",
    "KoopaLv3": "Koopa",
    "Kuribo2D3D": "Kuribo2D",
    "KuriboPossessed": "Kuribo",
    "OpeningStageStartCapManHero": "CapManHero",
    "PaulineAtCeremony": "CityMayorDress",
    "RadiconNpc": "CityMan",
    "SandWorldHomePyramidKai000": "SandWorldHomePyramid000",
    "SessionMusicianBass": "BandMan",
    "SessionMusicianDrum": "BandMan",
    "SessionMusicianGuitar": "BandMan",
    "SnowManRaceNpc": "SnowMan",
    "VolleyballNpc": "SeaMan",
    "Yukimaru": "SnowMan",
    "YukimaruRacePlayer": "SnowManRacer",
    "YukimaruRacer": "SnowManRacer",
    "YukimaruRacerTiago": "SnowManRacer",
}

ACTOR_COMPOSITE_RESOURCE_ALIASES = {
    "BossKnuckleLv2": ("BossKnuckleBody", "BossKnuckleHead"),
    "Gunetter": ("GunetterBody", "GunetterHead"),
    "GunetterMove": ("GunetterBody", "GunetterHead"),
    "Mofumofu": ("MofumofuBody", "MofumofuHead"),
    "MofumofuLv2": ("MofumofuBody", "MofumofuHead"),
    "PaulineAtCeremony": ("CityMayorDress", "CityMayorFace"),
}

_STAGE_FAMILY_RESOURCE_RULES = {
    "BirdCarryMeat": {
        "LavaWorld": "BirdLava",
    },
    "MarchingCubeBlockParts": {
        "ForestWorld": "MarchingCubeBlockPartsForest",
        "LavaWorld": "MarchingCubeBlockPartsLava",
    },
}


# Some standalone stage assets deliberately share a texture archive whose name
# cannot be derived from the asset or its containing subarea. Keep these
# relationships explicit so similarly named kingdom archives are not searched
# speculatively or allowed to override the intended material variants.
ASSET_TEXTURE_ARCHIVE_ALIASES = {
    "PeachWorldPictureRoom": ("PeachWorldCastleTexture.szs",),
    "PeachWorldPictureRoomDokan": ("PeachWorldCastleTexture.szs",),
}


def texture_archive_rule_names(asset_name: str) -> tuple[str, ...]:
    names = list(ASSET_TEXTURE_ARCHIVE_ALIASES.get(asset_name, ()))
    folded_name = asset_name.casefold()

    # Mario's caps reuse the matching costume head texture archive.  This is
    # consistent across the base outfit and the costume variants, but cannot
    # be inferred by the ordinary <AssetName>Texture.szs convention.
    if (
        folded_name.startswith("mario")
        and folded_name.endswith("cap")
        and not folded_name.endswith("nocap")
    ):
        names.append(f"{asset_name[:-len('Cap')]}HeadTexture.szs")

    return tuple(dict.fromkeys(names))


def report_resource_rule_candidates(
    unit_config_name: str,
    source_stage_names: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    composite = ACTOR_COMPOSITE_RESOURCE_ALIASES.get(unit_config_name)

    if composite is not None:
        return tuple(("CompositeActorResource", name) for name in composite)

    result: list[tuple[str, str]] = []
    alias = ACTOR_RESOURCE_ALIASES.get(unit_config_name)

    if alias is not None:
        result.append(("ActorResourceAlias", alias))

    stage_rules = _STAGE_FAMILY_RESOURCE_RULES.get(unit_config_name, {})

    for stage_prefix, archive_name in stage_rules.items():
        if any(
            stage_name.startswith(stage_prefix)
            for stage_name in source_stage_names
        ):
            result.append(("StageResourceRule", archive_name))

    return tuple(dict.fromkeys(result))

def resource_rule_candidates(
    placement: "StagePlacement",
) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = []

    if placement.unit_config_name == "ShineTowerRocket":
        source_links = placement.raw.get("SrcUnitLayerList")
        is_before_clear = isinstance(source_links, list) and any(
            isinstance(link, dict) and link.get("LinkName") == "BeforeClear"
            for link in source_links
        )
        candidates.append(
            (
                "ActorStateResourceRule",
                "ShineTowerDirty" if is_before_clear else "ShineTower",
            )
        )

    alias_name = ACTOR_RESOURCE_ALIASES.get(placement.unit_config_name)

    if alias_name:
        candidates.append(("ActorResourceAlias", alias_name))

    stage_rules = _STAGE_FAMILY_RESOURCE_RULES.get(
        placement.unit_config_name,
        {},
    )

    for stage_prefix, archive_name in stage_rules.items():
        if placement.source_stage_name.startswith(stage_prefix):
            candidates.append(("StageResourceRule", archive_name))
            break

    return tuple(candidates)