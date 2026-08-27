from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class StageCatalogueEntry:
    stage_name: str
    display_name: str
    group_name: str
    translated: bool

    @property
    def selector_name(self) -> str:
        if self.display_name == self.group_name:
            return self.display_name

        return f"{self.group_name} — {self.display_name}"


NAMED_STAGE_GROUPS = (
    ("Cap Kingdom", (
        ("CapWorldHomeStage", "Cap Kingdom"),
        ("CapWorldTowerStage", "Cap Tower"),
        ("RollingExStage", "Rolling Sublevel"),
        ("PoisonWaveExStage", "Poison Tide Sublevel"),
        ("PushBlockExStage", "Push-Block Sublevel"),
        ("FrogSearchExStage", "Frog Pond Sublevel"),
    )),
    ("Cascade Kingdom", (
        ("WaterfallWorldHomeStage", "Cascade Kingdom"),
        ("CapAppearExStage", "Mysterious Clouds Sublevel"),
        ("WanwanClashExStage", "Chain Chomp Cave Sublevel"),
        ("Lift2DExStage", "Chasm Lifts Sublevel"),
        ("WindBlowExStage", "Gusty Bridges Sublevel"),
        ("TrexPoppunExStage", "Dinosaur Nest Sublevel"),
    )),
    ("Sand Kingdom", (
        ("SandWorldHomeStage", "Sand Kingdom"),
        ("SandWorldMeganeExStage", "Invisible Maze Sublevel"),
        ("SandWorldSphinxExStage", "Jaxi Ruins Underground"),
        ("SandWorldUnderground000Stage", "Underground Temple"),
        ("SandWorldUnderground001Stage", "Deepest Underground"),
        ("SandWorldKillerExStage", "Bullet Bill Maze Sublevel"),
        ("SandWorldShopStage", "Crazy Cap"),
        ("SandWorldPressExStage", "Ice Cave"),
        ("SandWorldPyramid000Stage", "Inverted Pyramid 1"),
        ("SandWorldPyramid001Stage", "Inverted Pyramid 2"),
        ("SandWorldCostumeStage", "Dance Room"),
        ("SandWorldRotateExStage", "Strange Neighborhood Sublevel"),
        ("SandWorldSlotStage", "Slots Room"),
        ("SandWorldSecretStage", "Sand Kingdom Secret Sublevel"),
        ("MeganeLiftExStage", "Transparent Platform Sublevel"),
        ("RocketFlowerExStage", "Colossal Ruins Sublevel"),
        ("WaterTubeExStage", "Freezing Waterway Sublevel"),
        ("SandWorldVibrationStage", "Rumbling Floor Sublevel"),
    )),
    ("Wooded Kingdom", (
        ("ForestWorldHomeStage", "Wooded Kingdom"),
        ("ForestWorldTowerStage", "Sky Garden Tower"),
        ("ForestWorldWaterExStage", "Flooding Pipeway Sublevel"),
        ("ForestWorldCloudBonusExStage", "Cloud Lift Bonus Stage"),
        ("ShootingElevatorExStage", "Elevator Shaft Sublevel"),
        ("FogMountainExStage", "Foggy Sky Sublevel"),
        ("ForestWorldBossStage", "Secret Flower Field (Boss)"),
        ("RailCollisionExStage", "Flower Road Sublevel"),
        ("AnimalChaseExStage", "Herding Path Sublevel"),
        ("ForestWorldWoodsStage", "Deep Woods"),
        ("ForestWorldWoodsTreasureStage", "Deep Woods (Treasure Chest Tree)"),
        ("PackunPoisonExStage", "Invisible Road Sublevel"),
        ("ForestWorldBonusStage", "Treasure Room"),
        ("ForestWorldWoodsCostumeStage", "Deep Woods (Treasure Chest Cave)"),
        ("KillerRoadExStage", "Breakdown Road Sublevel"),
    )),
    ("Lake Kingdom", (
        ("LakeWorldHomeStage", "Lake Kingdom"),
        ("LakeWorldShopStage", "Crazy Cap"),
        ("FrogPoisonExStage", "Waves of Poison Sublevel"),
        ("TrampolineWallCatchExStage", "Ledge Climbing Sublevel"),
        ("GotogotonExStage", "Puzzle Part Sublevel"),
        ("FastenerExStage", "Zipper Chasm Sublevel"),
    )),
    ("Cloud Kingdom", (
        ("CloudWorldHomeStage", "Cloud Kingdom"),
        ("Cube2DExStage", "2D Cube Sublevel"),
        ("FukuwaraiKuriboStage", "Goomba Picture Match Sublevel"),
    )),
    ("Lost Kingdom", (
        ("ClashWorldHomeStage", "Lost Kingdom"),
        ("ClashWorldShopStage", "Crazy Cap"),
        ("ImomuPoisonExStage", "Poison Geyser Sublevel"),
        ("JangoExStage", "Klepto Lava Pit Sublevel"),
    )),
    ("Metro Kingdom", (
        ("CityWorldHomeStage", "Metro Kingdom"),
        ("CityWorldShop01Stage", "Crazy Cap"),
        ("Note2D3DRoomExStage", "Private Room 2D Sublevel"),
        ("CityWorldFactoryStage", "New Donk City Power Plant"),
        ("CityWorldMainTowerStage", "New Donk City Hall Interior"),
        ("PoleKillerExStage", "Bullet Bill Sublevel"),
        ("BikeSteelExStage", "Vanishing Road Sublevel"),
        ("CapRotatePackunExStage", "Rotating Maze Sublevel"),
        ("ElectricWireExStage", "Wiring Costume Sublevel"),
        ("CityWorldSandSlotStage", "Slots Room"),
        ("RadioControlExStage", "RC Car Room Sublevel"),
        ("ShootingCityExStage", "Siege Area Sublevel"),
        ("SwingSteelExStage", "Swinging Scaffolding Sublevel"),
        ("PoleGrabCeilExStage", "Swinging High-Rise Sublevel"),
        ("Theater2DExStage", "Projection Room Sublevel"),
        ("DonsukeExStage", "Pitchblack Mountain Sublevel"),
        ("CityPeopleRoadStage", "Crowded Alleyway Sublevel"),
        ("TrexBikeExStage", "T-Rex Chase Sublevel"),
    )),
    ("Seaside Kingdom", (
        ("SeaWorldHomeStage", "Seaside Kingdom"),
        ("SeaWorldCostumeStage", "Beach House Costume Sublevel"),
        ("WaterValleyExStage", "Narrow Valley Sublevel"),
        ("SeaWorldSecretStage", "Sphynx\u0027s Underwater Vault"),
        ("CloudExStage", "Cloud Sea Sublevel"),
        ("LStage", "Sinking Island Sublevel"),
        ("ReflectBombExStage", "Pokio Valley Sublevel"),
        ("TogezoRotateExStage", "Spinning Maze Sublevel"),
        ("SeaWorldSneakingManStage", "Flooded Cave Sublevel"),
        ("SeaWorldUtsuboCaveStage", "Underwater Tunnel Sublevel"),
        ("SeaWorldVibrationStage", "Rumbling Floor Sublevel"),
    )),
    ("Snow Kingdom", (
        ("SnowWorldHomeStage", "Snow Kingdom"),
        ("IceWaterBlockExStage", "Freezing Water Sublevel"),
        ("SnowWorldTownStage", "Shiveria Town"),
        ("ByugoPuzzleExStage", "Wooden Block Puzzle Sublevel"),
        ("IceWaterDashExStage", "Freezing Water Path Sublevel"),
        ("KillerRailCollisionExStage", "Flower Road Sublevel"),
        ("SnowWorldCloudBonusExStage", "Sky Bonus Sublevel"),
        ("SnowWorldLobby000Stage", "Bound Bowl Lobby: Regular Cup"),
        ("SnowWorldRaceExStage", "Bound Bowl: Regular Cup"),
        ("SnowWorldLobby001Stage", "Bound Bowl Lobby: Master Cup"),
        ("SnowWorldRaceHardExStage", "Bound Bowl: Master Cup"),
        ("SnowWorldRaceTutorialStage", "Bound Bowl Tutorial"),
        ("SnowWorldRace000Stage", "Bound Bowl Race 1"),
        ("SnowWorldRace001Stage", "Bound Bowl Race 2"),
        ("SnowWorldLobbyExStage", "Bound Bowl Race 3"),
        ("SnowWorldShopStage", "Crazy Cap"),
        ("IceWalkerExStage", "Trace-Walking Cave Sublevel"),
        ("SnowWorldCostumeStage", "Cold Room Costume Sublevel"),
    )),
    ("Luncheon Kingdom", (
        ("LavaWorldHomeStage", "Luncheon Kingdom"),
        ("LavaWorldUpDownExStage", "Magma Swap Sublevel"),
        ("LavaWorldBubbleLaneExStage", "Magma Narrow Path Sublevel"),
        ("LavaWorldFenceLiftExStage", "Lava Islands Sublevel"),
        ("LavaWorldClockExStage", "Spinning Athletics Sublevel"),
        ("LavaWorldExcavationExStage", "Cheese Rock Sublevel"),
        ("LavaWorldShopStage", "Crazy Cap"),
        ("DemoLavaWorldScenario1EndStage", "DemoLavaWorldScenario1EndStage"),
        ("CapAppearLavaLiftExStage", "Volcano Cave Sublevel"),
        ("ForkExStage", "Fork Flickin\u0027 Mountain Sublevel"),
        ("GabuzouClockExStage", "Rotating Gear Sublevel"),
        ("LavaWorldTreasureStage", "Treasure Room"),
        ("LavaWorldCostumeStage", "Simmering Room Costume Sublevel"),
    )),
    ("Ruined Kingdom", (
        ("BossRaidWorldHomeStage", "Ruined Kingdom"),
        ("BullRunExStage", "Chincho Army Sublevel"),
        ("DotTowerExStage", "Roulette Tower Sublevel"),
    )),
    ("Bowser\u0027s Kingdom", (
        ("SkyWorldHomeStage", "Bowser\u0027s Kingdom"),
        ("SkyWorldShopStage", "Crazy Cap"),
        ("SkyWorldCostumeStage", "Folding Screen Costume Sublevel"),
        ("TsukkunClimbExStage", "Wooden Tower Sublevel"),
        ("TsukkunRotateExStage", "Spinning Tower Sublevel"),
        ("JizoSwitchExStage", "Jizo Area Sublevel"),
        ("SkyWorldCloudBonusExStage", "Sky Slope Bonus Stage"),
        ("KaronWingTowerStage", "Hexagon Tower Sublevel"),
        ("SkyWorldTreasureStage", "Bowser\u0027s Castle Treasure Vault"),
    )),
    ("Moon Kingdom", (
        ("MoonWorldHomeStage", "Moon Kingdom"),
        ("MoonWorldWeddingRoomStage", "Wedding Hall"),
        ("MoonWorldShopRoom", "Crazy Cap"),
        ("MoonWorldSphinxRoom", "Sphinx\u0027s Hidden Vault"),
        ("MoonWorldCaptureParadeStage", "Underground Moon Caverns"),
        ("MoonAthleticExStage", "Giant Swing Sublevel"),
        ("Galaxy2DExStage", "2D Galaxy Sublevel"),
        ("MoonWorldBasementStage", "Crumbling Cavern Bowser Stage"),
        ("MoonWorldKoopa1Stage", "Captured Bowser Stage Background"),
    )),
    ("Mushroom Kingdom", (
        ("PeachWorldHomeStage", "Mushroom Kingdom"),
        ("PeachWorldCastleStage", "Peach\u0027s Castle Interior"),
        ("PeachWorldShopStage", "Crazy Cap"),
        ("PeachWorldCostumeStage", "SM64 Castle Courtyard Sublevel"),
        ("YoshiCloudExStage", "Yoshi Cloud Sublevel"),
        ("FukuwaraiMarioStage", "Mario Picture Match Sublevel"),
        ("DotHardExStage", "Moving 2D Sublevel"),
        ("PeachWorldPictureBossMagmaStage", "Cookatiel\u0027s Rematch Painting Room"),
        ("PeachWorldPictureMofumofuStage", "Mechawiggler\u0027s Rematch Painting Room"),
        ("PeachWorldPictureBossRaidStage", "Ruined Dragon\u0027s Rematch Painting Room"),
        ("PeachWorldPictureBossForestStage", "Torkdrift\u0027s Rematch Painting Room"),
        ("PeachWorldPictureBossKnuckleStage", "Knucklotec\u0027s Rematch Painting Room"),
        ("PeachWorldPictureGiantWanderBossStage", "Mollusque-Lanceur\u0027s Rematch Painting Room"),
        ("RevengeBossMagmaStage", "Cookatiel\u0027s Rematch Sublevel"),
        ("RevengeMofumofuStage", "Mechawiggler\u0027s Rematch Sublevel"),
        ("RevengeBossRaidStage", "Ruined Dragon\u0027s Rematch Sublevel"),
        ("RevengeForestBossStage", "Torkdrift\u0027s Rematch Sublevel"),
        ("RevengeBossKnuckleStage", "Knucklotec\u0027s Rematch Sublevel"),
        ("RevengeGiantWanderBossStage", "Mollusque-Lanceur\u0027s Rematch Sublevel"),
    )),
    ("Dark Side", (
        ("Special1WorldHomeStage", "Dark Side"),
        ("PackunPoisonNoCapExStage", "Invisible Road Sublevel"),
        ("KillerRoadNoCapExStage", "Breakdown Road Sublevel"),
        ("BikeSteelNoCapExStage", "Vanishing Road Sublevel"),
        ("SenobiTowerYoshiExStage", "Sinking Island Sublevel"),
        ("ShootingCityYoshiExStage", "Siege Sublevel, with Yoshi"),
        ("LavaWorldUpDownYoshiExStage", "Magma Swamp Sublevel"),
        ("Special1WorldTowerStackerStage", "Topper Rematch"),
        ("Special1WorldTowerBombTailStage", "Hariet Rematch"),
        ("Special1WorldTowerFireBlowerStage", "Spewart Rematch"),
        ("Special1WorldTowerCapThrowerStage", "Rango Rematch"),
    )),
    ("Darker Side", (
        ("Special2WorldHomeStage", "Darker Side"),
        ("Special2WorldKoopaStage", "Darker Side Bowser Area"),
        ("Special2WorldLavaStage", "Darker Side Course Area"),
        ("Special2WorldCloudStage", "Darker Side Cloud Area"),
    )),
    ("Duplicates", (
        ("MoonWorldKoopa2Stage", "Captured Bowser Stage Background Duplicate"),
        ("MoonWorldWeddingRoom2Stage", "Wedding Hall Duplicate"),
    )),
)


def home_stage_for(stage_name: str) -> str | None:
    for _, stages in NAMED_STAGE_GROUPS:
        if any(candidate == stage_name for candidate, _ in stages):
            return stages[0][0]

    return None


def _unlisted_group(stage_name: str) -> str:
    if stage_name.endswith("Zone"):
        return "Unlisted Zones"

    if stage_name.startswith(("Demo", "StaffRoll")):
        return "Unlisted Demos"

    return "Unlisted Stages"


def discover_stage_catalogue(
    stage_data_directory: Path,
) -> tuple[StageCatalogueEntry, ...]:
    if not stage_data_directory.is_dir():
        raise FileNotFoundError(
            f"Could not find StageData directory: {stage_data_directory}"
        )

    suffix = "Map.szs"
    available = {
        path.name[: -len(suffix)]
        for path in stage_data_directory.iterdir()
        if path.is_file() and path.name.endswith(suffix)
    }
    catalogue = []

    for group_name, stages in NAMED_STAGE_GROUPS:
        for stage_name, display_name in stages:
            if stage_name not in available:
                continue

            catalogue.append(
                StageCatalogueEntry(
                    stage_name=stage_name,
                    display_name=display_name,
                    group_name=group_name,
                    translated=True,
                )
            )
            available.remove(stage_name)

    unlisted_groups = {
        "Unlisted Stages": [],
        "Unlisted Demos": [],
        "Unlisted Zones": [],
    }

    for stage_name in sorted(available, key=str.casefold):
        unlisted_groups[_unlisted_group(stage_name)].append(stage_name)

    for group_name, stage_names in unlisted_groups.items():
        catalogue.extend(
            StageCatalogueEntry(
                stage_name=stage_name,
                display_name=stage_name,
                group_name=group_name,
                translated=False,
            )
            for stage_name in stage_names
        )

    if not catalogue:
        raise RuntimeError(
            f"No *Map.szs StageData archives found in {stage_data_directory}."
        )

    return tuple(catalogue)
