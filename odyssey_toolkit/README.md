# Odyssey Toolkit

Odyssey Toolkit is a Blender extension for reconstructing and inspecting content
from a user-extracted Super Mario Odyssey ROMFS.

The Toolkit provides four focused workflows:

- **Stage Importer** reconstructs scenarios, nested zones, placements, supported
  geometry, materials, textures, sky and approximate lighting.
- **Asset Importer** loads standalone SZS and BFRES model packages.
- **Animation Tools** apply supported native rig, material and camera animation,
  with Switch Toolbox SMD retained as compatibility tooling.
- **Diagnostics** exposes model reports, resolution coverage, import summaries,
  fallbacks, performance telemetry and cache state.

It reads SZS/Yaz0, SARC, BYML, BFRES and BNTX directly and creates Blender-native
data. It is a reconstruction toolkit, not a game renderer or runtime emulator;
unsupported and runtime-only content remains visible through diagnostic fallbacks.
See CHANGELOG.md for release history.

## Requirements

- Blender 4.5 LTS on Windows x64.
- A legally extracted Super Mario Odyssey romfs directory containing
  SystemData/WorldList.szs, StageData and ObjectData.

The release ZIP includes the compatible oead 1.2.9.post4 wheel. Do not install
oead manually into Blender before using Odyssey Toolkit.

## Quick Start

1. Install one extension ZIP using the instructions below and restart Blender.
2. Open the 3D Viewport sidebar with `N`, then select the **Odyssey** tab.
3. In **Stage Importer > Source**, select the extracted `romfs` folder.
4. Choose a stage and scenario. Use **Reload Stages** if ROMFS contents changed.
5. Enable or disable optional **Stage lighting**.
6. Press **Import Stage**. Progress appears in the panel and status bar.
7. Press **Cancel Import** or `Esc` to cancel safely.
8. Expand **Assets & Animations** and use **Import Standalone Model** to inspect one `.szs` or `.bfres`.

## Import categories

The Stage Importer exposes eight broad category switches: **Environment**,
**Characters**, **Gameplay**, **Collectibles**, **Effects**, **Audio**, **Technical** and
**Unclassified**. **All** and **None** provide quick selection. Filtering happens after
placement classification and model resolution but before model preparation, so excluded
placements do not incur mesh or texture import work. Collection organisation remains
unchanged.

Environment, Characters, Gameplay, Collectibles and Effects are enabled by default.
Audio, Technical and Unclassified are opt-in diagnostic categories so a normal import
starts with useful visual content instead of controller and helper fallbacks.

The extension ships a curated category for all 1,967 model archives resolved by the
development ROMFS. Actor-specific overrides still take priority, then the resolved BFRES
archive category, then conservative legacy heuristics. Enemies are intentionally grouped
under **Characters**. Technical includes lighting, areas, cameras, helpers and debug-style
placements; unresolved actors remain under **Unclassified** so they can be inspected rather
than silently discarded.

## Install, update and uninstall

### Install from an extension ZIP

1. Open **Edit > Preferences > Get Extensions**.
2. Open the top-right menu and choose **Install from Disk...**.
3. Select the release ZIP and enable the extension if Blender asks.
4. Restart Blender before importing.

Both `odyssey_toolkit.zip` and a versioned name such as
`odyssey_toolkit_v0.41.1.zip` are valid distribution filenames. They contain the
same manifest package ID, `odyssey_toolkit`; install only one of them. Do not also
copy an extracted legacy add-on folder into Blender's `scripts/addons` directory.

### Update

Version 0.41.0 changes the manifest package ID. Before upgrading from 0.40.0 or
earlier, remove **smo_kingdom_importer** from Blender's **Installed** extension list,
restart Blender, and then install the 0.41.0 ZIP. Existing imported scene data and
the established `smo.*` operators remain compatible.

For updates from 0.41.0 onward, install the newer ZIP with **Install from Disk...**.
Blender identifies the extension from its manifest rather than the ZIP filename, so a
generic ZIP can replace a versioned ZIP and vice versa. If Blender refuses to replace
the installed package, remove the installed copy, restart Blender, then install the new
ZIP.

The equivalent Blender 4.5 command-line form is:

```powershell
& "<Blender folder>\blender.exe" --command extension install-file -r user_default -e "<release ZIP>"
```

### Uninstall

Open **Edit > Preferences > Get Extensions > Installed**, expand the extension's
details or right-side menu, and choose **Uninstall** or **Remove**. If that control is not
shown, close Blender and use:

```powershell
& "<Blender folder>\blender.exe" --command extension remove odyssey_toolkit
```

Restart Blender after removal.

### Remove stale duplicate installs

If behavior does not match the newly installed version:

1. Close every Blender process.
2. Remove the extension through Blender or the command above.
3. Check the following user locations, replacing `<version>` as appropriate:

   - `%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\odyssey_toolkit`
   - `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\odyssey_toolkit`

4. Remove only leftover directories belonging to this add-on. Do not delete the whole
   Blender version, `extensions`, `scripts` or `addons` directory.
5. Start Blender, install one ZIP, then restart once more.

Imported meshes, materials, images, Worlds, lights and custom properties are Blender
datablocks saved inside a `.blend`; uninstalling the extension intentionally does not
delete them. Those saved datablocks are scene content, not stale Python add-on files.

## Structure

```text
odyssey_toolkit/
|-- __init__.py
|-- world_list.py
|-- stage_catalog.py
|-- stage_data.py
|-- stage_lighting.py
|-- object_data.py
|-- placement_classifier.py
|-- resource_rules.py
|-- model_report.py
|-- performance.py
|-- bfres_mesh.py
|-- bfres_animation.py
|-- bfres_animation_import.py
|-- bntx_texture.py
|-- static_model_import.py
|-- standalone_import.py
|-- blender_manifest.toml
|-- README.md
`-- previews/
    `-- scenarios/
        |-- _scenario.png
        |-- main_story_clear.png
        |-- postgame.png
        |-- moon_rock.png
        `-- balloon_world.png
```

## Stage and scenario menus

The Stage selector is a normal text dropdown rather than a thumbnail grid. It discovers
every `*Map.szs` archive in `romfs/StageData`, storing the exact internal stage name as
the enum value. The 175 scenes in the noclip.website Odyssey scene table use the supplied
English names and kingdom grouping, such as `Cap Kingdom - Cap Tower`. This translation
table was contributed to noclip.website by djfox11.

Map archives absent from that scene table are still available as **Unlisted Stages**,
**Unlisted Demos** or **Unlisted Zones** under their internal names. Zones normally appear
inside another stage with a parent transform; importing one directly intentionally shows
its local standalone layout at the origin. Reserved and demonstration stages may also be
incomplete outside their original game sequence.

WorldList continues to provide the standard kingdoms' scenario counts and story tags.
For every other stage, the add-on reads the selected Map BYML only when the selection
changes and lists each non-null scenario dictionary. Results are cached until the ROMFS
path is reloaded. Scenario icons support PNG, JPG, JPEG and WebP. Ordinary scenarios use
`_scenario`; tagged standard-kingdom scenarios use one icon according to this priority:
Moon Rock, Balloon World, Postgame, then Main story clear. Every matching tag remains in
the scenario label even though Blender can display only one enum icon.
## Stage Importer UI

The **Source** section stores the last successfully validated ROMFS path. Invalid or
partially typed paths report an error without replacing the saved working path.
**Reload Stages** rescans WorldList and StageData while retaining the selected stage and
scenario where possible. It also refreshes caches whose contents depend on ROMFS files.

The **Stage** section contains the stage and scenario selectors plus **Stage lighting**.
Lighting imports use a fixed, documented conversion from the game files, so repeated
imports of the same preset produce the same values without user multiplier state.

The **Import** section starts **Import Stage**. Preparation and placement progress are
shown in the panel and Blender status bar. While the modal import is running, conflicting
stage controls are disabled and **Cancel Import** requests safe cancellation. `Esc`
remains available.

**Assets & Animations** is a separate top-level Odyssey panel, so standalone model testing
and native BFRES animation controls stay apart from the stage workflow. It remains available
even before a ROMFS is selected and starts collapsed to keep the sidebar compact. The
optional **Export Model Report** diagnostic remains available through Blender's `F3`
operator search.

### Sky and lighting

Stage lighting is enabled by default. The importer selects the scenario's
`GraphicsArea.byml` preset, creates a Blender World and Sun from its global lighting
data, and converts supported authored pre-pass point, spot and line lights into Blender
lights in a dedicated collection.

The preset's peak `DirectionalLight.Color` component is divided by 128 for Blender Sun
energy, with its RGB ratios retained as Sun colour. For World lighting, the importer
reads the preset's Default `MaterialLight` map, sums `LightColor * LightIntencity` for
every enabled lobe, normalises that accumulated colour and divides its peak by the same
128 reference. The directional distribution of those lobes is collapsed into one uniform
Blender World. If the referenced light map is unavailable, a documented 25%-of-Sun
fallback is used. No minimum strength or user multiplier is applied.

Authored point and spot lights use `max(ColorRed, ColorGreen, ColorBlue) * 0.2` as Blender
power; line lights use the corresponding `* 0.4` area-light approximation. These are one
tenth of the conversion used before 0.32.0. Raw graphics-preset values, accumulated World
radiance, exposure and white point remain in custom-property JSON. Odyssey's HDR exposure,
tone mapping, shadowing and per-material directional light-map evaluation are not reproduced,
so the result remains an approximation rather than one-to-one renderer parity.

Generated Worlds and Suns use persistent scene-and-import identities, so same-named imports
do not mutate another scene's lighting or an untagged user World. Only the Sun paired with
the active generated World is enabled; importing multiple stages therefore does not stack
global Suns. The most recently imported stage becomes active, and disabling lighting on
re-import restores the previously active World and its matching Sun when safe.
Sky domes are selected indirectly by the game rather than listed as ordinary map actors.
The importer derives `Sky...` candidates from scenario Cloud and Distant SkyList resources,
then consults the exact `Sky.Name` in the GraphicsArea preset before inheriting a home or
parent-zone sky. Materials authored with `alRenderSky` use an opaque Emission graph rather
than Principled BSDF. Their texture alpha is preserved and used as an RGBM-style HDR
multiplier (range 8), never as opacity; `uSkyScale` and `uAlphaColor` remain active colour
controls. `alRenderCloudLayer` materials use a separate normal-aware diffuse/emission mix,
the authored `albedo`, BFRES vertex colour and `wrap_coef`, and a boosted density-to-coverage
route instead of feeding their low-range density alpha directly to surface opacity. These
graphs are selected from the FMAT shader archive, not from object-name heuristics.

## Actor registry

Add-on Preferences contains an **Actor Registry** section for the last valid ROMFS selected
in the Odyssey sidebar. **Build Registry** scans Map, Design and Sound StageData archives and all
15 scenario entries directly as raw `oead.byml.Hash` and `oead.byml.Array` nodes. It records
observed `UnitConfigName`, `ParameterConfigName` and explicit `ModelName` relationships without
performing full placement conversion or importing Blender data.

The generated versioned JSON is stored in Blender's user configuration directory and keyed to
the resolved ROMFS location; no game-derived database is shipped with the extension. Rebuilds
are transactional, so cancellation or a failed build keeps the previous registry. Preferences
shows archive progress and offers explicit cancel and clear actions.

After building, **Export Resolution Coverage** writes a concise JSON summary and an
exhaustive UTF-8 CSV beside the registry JSON, then loads both into Blender Text
datablocks. Only signatures observed at least once without a StageData `ModelName` are
included. The JSON presents overall coverage plus frequency-ranked actionable, runtime-visual
and unknown queues; the CSV retains every signature and all source-stage, StageData-layer,
placement-list, candidate-model, ObjectData archive and BFRES evidence. Rows are grouped as
safe registry mappings, validated curated aliases, explicit multi-archive composites, direct
ObjectData matches, ambiguous mappings, confirmed non-mesh actors, runtime/composed visuals,
likely missing models and unknowns. Report generation validates ObjectData archives
incrementally on Blender's main thread, displays progress and can be cancelled without
replacing the previous files. The full development ROMFS report took approximately 7-39
seconds depending on filesystem cache.

Version 0.34.0 tightens controller/runtime evidence so it does not pollute the missing-model
queue, adds verified state and stage-family aliases, and imports verified multi-archive actors
as all of their component BFRES resources under one placement transform. The exhaustive CSV
remains the audit trail; low-confidence unknowns stay separate until ROMFS evidence justifies
a model mapping or a confirmed model-less classification.

When **Use actor registry for model resolution** is enabled, the resolver consults the registry
only after explicit StageData names, same-stage observations and curated state/actor rules have
failed. A registry mapping is offered only when the exact actor/parameter signature has one
observed model, or every observed parameter variant of that actor agrees on one model. The
candidate must still resolve to an ObjectData archive containing BFRES before it can affect an
import. Ambiguous evidence and filename-similarity suggestions never import automatically.

The development ROMFS benchmark scanned 631 archives, 7,834 scenario entries and 565,395
placement records in approximately 62 seconds. The resulting 2,558-signature schema-2
registry was 1,355,797 bytes, saved in 0.076 seconds and loaded with archive-inventory
validation in 0.358 seconds. It contained 114 unambiguous observed signatures before
ObjectData validation. Of 2,304 signatures with at least one model-less occurrence, 1,729 now resolve to
validated resources, 270 are confirmed non-mesh helpers/controllers, 227 are runtime or
composed visuals, and 78 remain explicitly unknown. No signature is currently classified as
an unresolved model that should have a static BFRES. One of the validated resources still
comes from a safe registry mapping after exact and curated rules are credited. The saved
archive-inventory
fingerprint is checked on load; changed StageData or ObjectData invalidates the registry and
requires an explicit rebuild.

## Armatures and skin weights

**Preferences > Import > Import armatures and skin weights** enables
rigging for both complete stages and the standalone importer. It is opt-in so existing
imports retain their static bind-pose behavior and memory profile until explicitly enabled.

For validated multi-bone FSKL models, the importer creates Blender armatures with the
original bone names and parent hierarchy, converts rest transforms through the same
`(X, -Z, Y)` basis and centimetres-to-metres scale as geometry, and creates vertex groups
and Armature modifiers for rigid and one- to four-weight shapes. Repeated placements share
the immutable Armature and Mesh datablocks while retaining independent pose state. Each
armature receives the actor placement transform; rigged mesh objects remain local to it.
Verified multi-resource actors can create one independent armature per component model.

Malformed hierarchy, palette, index or weight data never produces a partial rig. The
affected model falls back to the same validated static bind pose and records the reason in
`smo_rig_errors`. Import roots also record armature and rigged-mesh counts, and performance
telemetry reports rest-armature creation and skin binding separately.

Generated armatures retain their precise source archive, BFRES member and native FSKL rest
transforms so the **BFRES Animations** panel can discover and apply FSKA and FBVS tracks.
Each armature can instead select a separate animation-only SZS or BFRES package without
changing the model source. Game IK or procedural constraints, physics, sockets and runtime
subactor attachment are not
reconstructed. Bone display lengths are derived for Blender usability and do not represent
a separate animation file.

## Standalone model import

**Assets & Animations > Import Standalone Model** and the matching **File > Import** operator import
one ObjectData `.szs` or uncompressed `.bfres` at the origin. The operation uses the same
mesh, texture and material readers as a complete stage import, making it suitable for
isolating an asset without loading a kingdom.

Embedded textures and an adjacent `<ModelName>Texture.szs` are detected automatically.
When **Search StageTexture Archives** is enabled in the file browser, known model-specific
shared archives are checked first. Mario costume cap archives use the matching
`<CostumeName>HeadTexture.szs` (for example, `MarioCap.szs` uses
`MarioHeadTexture.szs`); the Peach Picture Room variants use
`PeachWorldCastleTexture.szs`. Otherwise an internal family such as `WaterfallWorld` is
inferred from the model filename and matched to the most specific available
`*StageTexture.szs`. The selected sidebar stage is used when the model name has no stage
family or when another same-family archive is needed. A replacement is
committed only after the new asset succeeds; failure removes its partial objects and
zero-user meshes or armatures without discarding the previous successful test import. The new root is
selected and records searched texture archives, missing textures, decoder errors and
performance totals.

## Legacy diagnostic placement import

The earlier diagnostic operator remains available as **Import Diagnostic Placements**
through Blender's operator search for troubleshooting, but is no longer shown in the
sidebar. It reads
`<StageName>Map.szs`, `Design.szs` and `Sound.szs` from
`romfs/StageData`. Scenario numbers are one-based and select the corresponding
zero-based BYML array entry. Linked placement records are collected recursively and
deduplicated by their placement ID.

`ZoneList` references are expanded recursively from their own StageData Map, Design
and Sound archives. Child transforms are composed with the complete parent-zone
translation, quaternion rotation and scale before coordinate conversion. Zone
archives are cached for the scenario, nested references are cycle-checked, and
zone-child placement IDs are prefixed by their reference path so they remain unique.
Null or absent optional zone layers are treated as empty. Optional zone layers that
do not contain the selected higher scenario are also skipped safely.

The add-on inspects matching `romfs/ObjectData/*.szs` archives using `ModelName`,
`UnitConfigName` and `ParameterConfigName`. It records whether the archive contains
a BFRES resource, then combines that evidence with placement category and actor-name
rules. This separates environment, characters, gameplay objects, collectibles,
effects, audio, areas, cameras, helpers, debug objects and two unknown groups.
Known runtime actor aliases are resolved explicitly; for example,
`CoinStackGroup` uses the visible `CoinStack.szs` model resource.
Classification does not discard parser data or control whether a placement imports. It
organises every placement into a semantic Outliner collection.

Resolution uses direct StageData names first, followed by unique model names observed for
the same actor or parameter class and confirmed actor or stage-family rules. This mirrors
Spotlight's safe exact-name baseline while handling Odyssey's code-selected resources.
`SandWorldHomePyramidKai000` resolves to `SandWorldHomePyramid000`;
`CapFlower` resolves to its `CapFlowerBloom` visual resource;
`MarchingCubeBlockParts` selects the Forest or Lava resource from its source stage.
Darker Side's `PaulineAtCeremony` resolves to `CityMayorDress`, while
`SessionMusicianGuitar`, `SessionMusicianDrum` and `SessionMusicianBass` resolve to the
shared `BandMan` body selected by the game's actor variants. The game attaches each
instrument as a `BandMan` subactor; this static importer currently resolves the principal
character body rather than reconstructing animated subactor attachments.
`ShineTowerRocket` selects `ShineTowerDirty` at Cascade's `BeforeClear` placement in
scenario 1 and `ShineTower` at the normal landing point afterward. The Odyssey's other
parts remain runtime-created subactors. Likewise, Mushroom scenario 3 authors
`WorldTravelingPeach` and a `TiaraWaitActionName`, but the game creates and animates
Tiara at runtime; the static importer does not invent an unauthored attachment offset.
Linked `KeyMoveNext` records remain movement waypoints rather than duplicate actors.
Sand Kingdom's pyramid uses its raised waypoint from scenario 3 onward.

Model-report exports include every attempted resolution candidate and ranked filename
suggestions for unresolved actors. High-confidence suggestions are inspected lazily for
BFRES files plus `InitModel` and `InitSubActor` references. Suggestions are diagnostic
only and never change imports automatically.

Every parsed placement is included. The Outliner recreates the former checkbox groups
as colour-tagged collections. Environment, Characters, Gameplay Objects, Collectibles
and Unknown Models sit directly under the kingdom. Effects, Areas, Cameras and Helpers,
Audio, Debug and Unknown Modelless sit under one colour-tagged **Extras** collection,
so all extras can be hidden with one viewport or render toggle. Map, Design and Sound
remain object metadata rather than additional collection branches.

Each diagnostic placement becomes a cube-display empty in that compact group
hierarchy. These are lightweight empties, not mesh cube
datablocks. The complete placement transform is applied to each marker. Positions
are converted from game coordinates `(X, Y, Z)` to Blender `(X, -Z, Y)` and scaled
to metres. Odyssey's SEAD XYZ Euler rotation is converted through the same coordinate
basis and stored as a Blender quaternion, avoiding incorrect component swapping for
combined rotations. Scale becomes `(X, Z, Y)`, including non-uniform or negative
values. Original composed translation, rotation, scale, source stage, zone path, layer,
model, unit configuration, links, classification and resolved resource details
remain available as custom properties.

Re-importing the same stage and scenario replaces its generated diagnostic markers.
The operation supports Blender Undo.
Some stages do not provide every optional layer. Available layers are still
imported and missing layers are recorded on the root empty.

## Static BFRES import

**Import Stage** imports every placement from the selected stage and
scenario. It Yaz0-decompresses each required ObjectData SZS, extracts the preferred BFRES from its
SARC archive, and decodes Odyssey's version-8 FMDL, FVTX, FSHP, FSKL and first-LOD
mesh data without an external converter. Supported shapes currently include
triangle lists with rigid or up to four-weight skinning and the vertex formats
encountered in the ROMFS survey.

The static-model operation runs progressively through Blender's modal event loop.
The viewport redraws between short batches, the status bar shows the current source zone
and asset, and **Cancel Import** or `Esc` requests safe cancellation. The queue imports
the main stage first, then each expanded zone, prioritising environment geometry before
gameplay objects, characters, collectibles and remaining extras.

A same-stage and scenario re-import is transactional until its final commit. The previous
generated result stays intact with temporary object and datablock names while the
replacement is prepared. Cancellation or a preparation/progressive failure before commit
removes the partial replacement and zero-user meshes, restores the previous result and
records `smo_last_reimport_status` and `smo_last_reimport_message` on its root. Only a
successful replacement removes the previous result. First-time cancellation and failure
also clean partial output and clear stale counts. A completed import remains one Blender
Undo step.

Each decoded BFRES asset is cached for the operation. The cache includes the placement's
shared-texture context, so an asset reused by different source stages cannot retain the
first stage's materials. Repeated placements in the same context share Blender
mesh datablocks instead of duplicating geometry, while each placement receives its own
object transform and metadata. The local `DisplayTranslate`,
`DisplayRotate` and `DisplayScale` from UnitConfig are composed after the StageData
placement transform, matching Odyssey's model display offset for coins, Power Moons
and other actors. Multi-shape assets create one Blender object per shape. With experimental
armatures disabled, FSHP base bones, the complete FSKL parent hierarchy and `_i0`/`_w0`
data continue to produce the existing validated static bind pose. With armatures enabled,
the same parser retains those validated bindings for Blender bones, vertex groups and
Armature modifiers without changing the rest geometry. Unsupported assets, unresolved
resources and model-less placements
remain visible as cube-display empties in the same checkbox-equivalent collection
hierarchy.

## Native BFRES animation import

With **Import armatures and skin weights** enabled, select an imported armature or
one of its rigged meshes, expand **Assets & Animations** in the Odyssey sidebar, then expand
its **BFRES Animations** subpanel. By default the animation dropdown is populated from the exact BFRES member that
created the selected rig. Use the per-armature **Animation Package** file picker to select a
separate `.szs` or uncompressed `.bfres`, such as `PlayerAnimation.szs`,
`PeachAnimation.szs` or `KoopaAnimation.szs`. Leaving it blank returns that armature to its
model package. Choose an entry and press **Use Animation** to create and activate a Blender
Action. The refresh icon clears the source cache if either archive changed on disk.

The importer reads Odyssey's version-8 FSKA bone tracks, FBVS bone-visibility
tracks and FMAA shader-parameter/colour tracks without an intermediate export. FSKA supports Euler XYZ and quaternion rotations,
translation and scale, cubic, linear, stepped and baked curve encodings, segment-scale
compensation and the BFRES loop flag. Compensation cancels only the immediate parent's
local animated scale and is baked into the Action, so global root/cutscene scale remains
effective through compensated descendants. Transform curves are sampled at every integer game
frame into continuous Blender quaternion channels. FBVS packed boolean curves become
constant-interpolation Action channels on their target bones and drive viewport and render
visibility for meshes attached to those BFRES base bones. Matching FSKA and FBVS resources are merged by name. FMAA suffixes such as
`_fts` and `_fcl` are removed for matching, so a companion shader or colour record is
applied with the corresponding skeletal clip. Genuine visibility-only and material-only
entries remain selectable. Shader values used by the translated Blender material become node-tree Actions. This
includes float and integer constants/curves, RGBA parameters such as `const_color0` and
`base_color_mul_color`, translated cloth/Fresnel scalar inputs, and animated
`TexSrt` parameters. TexSrt channels are sampled through the same Maya, 3ds Max or
Softimage conversion as static UV transforms. Materials are copied and linked per selected
model before animation, preventing one imported placement from changing every other user
of a shared material.

Switch FMAA texture-pattern resources (the `_ftp` suffix), material-visibility
resources (`_fvt`) and native FSHA shape animations are not yet imported. They remain
separate from the supported FMAA shader-parameter (`_fts`) and colour (`_fcl`)
tracks; unsupported pattern records are not presented as functional material tracks.

The scene range and playback rate are set to the animation's inclusive frame range at
60 FPS. The armature controller Action stores source, transform, skipped-track,
visibility-target, driven-mesh and material-Action metadata as custom properties; each
material Action retains its source clip and selected-model owner.

Animations with at least one target on the selected armature are listed. The selector reports
matching versus total targets; tracks for companion or runtime subactor bones absent from the
selected rig are skipped and recorded on the Action. This is required for composite assets such
as ShineTower, whose 26 FSKAs also name separate ShineNumber, rope, sticker and component-light
parts. Imported armatures from 0.35.3 can fall back to their Blender rest matrices, but
re-importing with 0.36.0 or newer preserves the exact native FSKL scale and rotation data.
Re-import with 0.36.2 or newer to add the per-shape BFRES base-bone metadata required for
mesh visibility drivers. Re-import with 0.38.0 or newer before using shader/colour
animation so the translated shader nodes retain their exact BFRES parameter bindings. Armatures and Actions created before 0.38.4 must be re-imported to receive the hierarchy-preserving scale compensation; the hidden correction-bone hierarchy cannot be retrofitted safely.
The model source and any selected animation SZS or BFRES must
remain at their recorded paths when the list is opened.

Native BFRES import is preferred over SMD because SMD does not carry skeletal scale tracks.
The SMD workflow remains available as a compatibility path for edited or externally supplied
animations.

## Native BFRES camera animation import

Expand **Assets & Animations > BFRES Camera Animations**. With a configured ROMFS, the
panel uses `ObjectData/DemoCamera.szs` automatically; its file picker can instead select
another `.szs` or uncompressed `.bfres`. Choose a scene clip such as `DemoOpening01` and
press **Import Camera Animation**. The operator updates the selected Camera object, or
creates a new Camera when none is selected, then makes it the active scene camera.

The importer reads Odyssey's native FSCN and FCAM resources directly. It reconstructs
every integer frame from the authored camera position, look-at target and twist, converts
Y-up centimetres to Blender's Z-up metres, and creates quaternion transform channels. A
separate Camera-data Action carries the vertical field of view and near/far clipping.
The BFRES aspect ratio, inclusive frame range and 60 FPS rate are applied to the scene.
Both Actions retain the source archive, BFRES member, scene clip and conversion metadata.
Odyssey's `DemoCamera.szs` uses perspective look-at cameras; uncommon Euler-ZXY FCAM
resources are reported as unsupported rather than imported with a guessed convention.

## Switch Toolbox SMD animation import

With **Import armatures and skin weights** enabled, select an imported
Toolkit-generated armature or one of its rigged meshes, then choose **File > Import >
Super Mario Odyssey Animation (.smd)**. The legacy SMD importer is intentionally kept out
of the Odyssey sidebar so the native BFRES animation workflow remains the primary interface. Export an animation-only SMD from Switch Toolbox; model SMDs
with triangle or vertex-animation sections are rejected.

Generated armatures from 0.35.2 and earlier have legacy incorrect stored bone axes even
though their static bind pose looks normal. They are rejected by the animation importer:
re-import the model or stage with 0.35.3 or newer before importing an SMD animation.

The importer validates the complete SMD bone-name and parent hierarchy against the
selected armature before creating a Blender Action. It converts every parent-local
transform from Odyssey's Y-up centimetre convention to the add-on's Z-up metre convention,
uses quaternion rotation channels and preserves the SMD frame numbers with linear
interpolation. Bones present in the hierarchy but omitted from every frame retain their
rest transforms and follow animated parents normally. The scene frame range is set to the
imported animation.

Importing these files directly through Blender Source Tools is not compatible with the
add-on's converted armatures: its raw translations are 100 times too large and its local
bone basis differs. Use this native operator for Switch Toolbox animations.

Every fallback records its exact reason in the `smo_fallback_reason` custom property. It
also records `smo_model_expectation`, confidence and evidence, distinguishing confirmed
non-mesh controllers, runtime/composed visuals, likely unresolved models and genuinely
unknown cases. The import root stores the aggregate groups as
`smo_model_expectation_counts` JSON. These fields are diagnostic and never change model
resolution or fallback creation.

Static `RippleFixMapParts` resources, including water, quicksand and poison surfaces, are
classified as environment geometry and always imported. Their first decodable declared
texture is displayed directly as the Principled Base Color.
The modelless `OceanWave` controller used by Seaside and other kingdoms receives a
5-kilometre translucent plane at its StageData water height. This represents the game's
camera-following procedural ocean without pretending it is a recoverable BFRES mesh.
Other modelless effect actors such as particle waterfalls remain fallbacks because their
visuals are not BFRES meshes.

Vertices, UV0, `_c0` through `_c3` vertex colours and 16- or 32-bit triangle indices
are imported. Vertices use the same `(X, -Z, Y)` basis and centimetre-to-metre
conversion as StageData, and UV V coordinates are flipped for Blender. Colour sets
are preserved as `Color`, `Color1`, `Color2` and `Color3` point-domain attributes;
they are not yet connected to materials automatically. Every successfully decoded texture
referenced by a shape's FMAT is loaded into the Blender file and retained as a labelled
Image Texture node. For Odyssey's `alRenderMaterial`, the importer retains typed shader
parameters, shader options, sampler assignments, sampler state and render infos. Known
output codes authoritatively route Base Color, Normal, Roughness, Metallic, Emission and
Alpha. Scalar outputs can use RGBA channels, `const_single0` through `const_single3`,
components of `const_color0` through `const_color3`, supported blend results, or literal
zero/one. Colour outputs can use the base-colour path, `_u0` through `_u4`, direct
`const_color0` through `const_color3`, prior supported blend results, or literal zero/one.
The verified blend subset is equation 0 (`mix(dst, src, coefficient)`), equation 1
(`src * coefficient + dst`) and equation 2 (`src * dst`), including non-inverted RGB/R/G/B/A
source selection and nested earlier blend stages. `base_color_mul_color` and
`uniform0_mul_color` through `uniform4_mul_color` are applied without clamping, so HDR
emission values remain intact. Constant-colour alpha is never connected to Principled
Alpha unless `o_alpha` explicitly selects that route. Compatible U/V wrap modes are copied
to Blender image nodes. Materials record recursive translated routes, the full shader-option
table, inactive outputs and genuinely active unhandled outputs as separate custom
properties.

FMAT component selectors now preserve their actual channel. Dedicated BC4 roughness and
metallic maps keep the compact direct connection because their decoded RGB values are
identical; packed or otherwise multi-channel maps use an explicit Separate Color node for
Red, Green or Blue, and Alpha uses the image's alpha socket. This avoids Blender's implicit
RGB-to-luminance conversion changing combined masks. Enabled `o_ao` modulates Base Color,
and enabled `o_sss` and `o_refract_rate` drive Principled Subsurface Weight and
Transmission Weight. `cRenderType`, `enable_transparent`, `enable_translucent`, alpha-mask
state, `forward_xlu` and `display_face` are retained; transparent materials use Blender's
dithered surface mode and front/back FMAT faces enable back-face culling. Refraction colour,
eta and alpha-test threshold remain as structured material metadata where Principled has no
equivalent input with matching Odyssey semantics.

When **Experimental cloth NoV/Fresnel approximation** is enabled, `cloth_nov` materials receive a view-dependent Blender translation. The option is disabled by default; parsed metadata is retained either way.
Layer Weight **Facing** supplies Blender's Fresnel polarity: zero head-on and one at
grazing angles. Odyssey's `is_cloth_nov_reverse=1` uses that value directly; otherwise
it is converted back to NoV. The factor combines `cloth_nov_slope0` and `cloth_nov_tone_pow0`; an angular curve built
from `cloth_nov_peak_pos0`, `cloth_nov_peak_pow0` and
`cloth_nov_peak_intensity0` modulates that primary tone instead of adding a second
independent band. It then applies `o_cloth_mask_map` and `cloth_mask_component`.
Direct RGBA selectors 30-60 and inverted RGBA selectors 70-100 are supported. This is
required by MarioFace HairMT, whose inverted-red roughness mask prevents the orange cloth
colour from covering the complete hair surface. That factor mixes `o_cloth_map` into Base Color and adds
`o_cloth_emission_map * cloth_nov_emission_scale0` to Emission. An unlinked Principled
emission input is treated as black before this additive branch, because Blender's dormant
white default is paired with zero strength and must not become visible when the branch
enables emission. This reproduces the authored Fresnel/rim intent on materials such as Peach's dress and Pauline's clothing, but
is explicitly an approximation because the original game shader equation is not stored
in BFRES. `cloth_nov_emission_scale_type` and random-noise settings are preserved in
`smo_cloth_nov` metadata; the random-noise modulation is not yet reconstructed.

Verified FMAT texture-coordinate routes now resolve `base_color_fuv_selector`,
`normal_fuv_selector` and `uniform0_fuv_selector` through `uniform4_fuv_selector`.
The selected FUV follows its shader attribute assignment to `_u0` through `_u7` and its
optional `tex_mtx0` through `tex_mtx3` parameter. Static Maya, 3ds Max and Softimage
`TexSrt` values use an explicit affine node chain after the existing V-axis conversion.
Generated or procedural FUV selectors, missing UV attributes, malformed matrices and a
texture sampled through conflicting coordinate routes retain the active-UV fallback and
are recorded in `smo_shader_unhandled_texture_coordinates` rather than guessed.

Unknown shader families, output codes, inverted blend channels, post operations and blend
equations outside the verified subset deliberately keep the
existing semantic fallback: `_alb`/`.alb`, `_rgh`, `_mtl`, `_nrm` and `_emm`/emission
names connect to Principled Base Color, Roughness, Metallic, Normal and Emission Color.
This also remains the fallback for advanced `alRenderMaterial` outputs that require the
game shader, including procedural water/normal blends, transparent-texture modes,
screen-buffer refraction, indirect UV distortion and environment reflections. Roughness, metallic
and normal images use Non-Color. Scalar data maps use their selected channel (red for the
legacy fallback), while normals pass through a Normal Map node. Odyssey normal textures
store tangent-space X and Y in red and green, so the positive Z/blue component is
reconstructed before Blender image creation. This also makes a normal map used as the
Base Color fallback display in its expected purple range. Albedo and emission images use
sRGB. Non-albedo images use Blender's channel-packed alpha mode so BC4 data replicated
into alpha cannot darken its RGB channels; a data map used as the Base Color fallback is
kept opaque. Secondary and unrecognised maps remain available as unconnected nodes.
Specular IOR Level is set to `0.2`.

The Base Color selection still prefers an albedo, then tries every other FMAT texture in
its declared order. This fallback remains deliberately semantic-agnostic: when no albedo
can be decoded, a normal, roughness, metallic, emission, mask or other map can also be
displayed directly as Base Color. A data map used by that fallback receives a separate
sRGB image datablock while its semantic node retains the Non-Color version. For ordinary
Base Color materials, source alpha is connected when appropriate. Synthesised SkyList
materials remain opaque even though their image alpha is retained.

The importer resolves embedded BNTX textures first, then companion object texture
archives, the placement's source-stage texture archive and the selected stage's texture
archive. An asset whose name identifies another kingdom family also searches that
family's HomeStage texture archive before the source/selected-stage fallbacks. This covers
cross-kingdom reuse such as Waterfall break parts placed in the Deep Woods. Shared texture BFRES files, decoded pixels and Blender images are loaded lazily
and cached for the operation. Materials and images include a stable source digest in
their Blender datablock names, preventing same-named resources from different archives
from overwriting one another.

BC1 sRGB, BC3 sRGB, BC4 UNORM/SNORM, BC5 UNORM/SNORM and uncompressed
RGBA8/BGRA8 images are decoded. Textureless FMATs use supported Float4 base-colour
parameters where available, which covers assets such as the Wooded Kingdom greenhouse
frame. A named brown placeholder remains when neither a colour input nor a supported
texture can be recovered. Loaded image counts, missing names and decoder errors are
recorded on the import root.

Selected sky objects are marked with `smo_synthesised_sky`; selection and alpha behaviour
are described under **Sky and lighting**.

Decoded topology is validated before UVs, colour attributes or normals are assigned.
By default Blender calculates display normals from the resulting smooth geometry.
BFRES UV attributes `_u0` through `_u7` are retained as `UVMap`, `UVMap.001`, and
later Blender UV layers. Every layer uses the existing V-axis conversion; `_u0`
remains the active render layer when present. Supported `alRenderMaterial` textures now
receive explicit UV Map nodes selecting the FMAT-requested layer. Identity transforms
share that UV output directly; non-identity `TexSrt` routes share a deterministic affine
coordinate chain between textures using the same FUV and matrix.

**Apply BFRES custom normals** in the add-on preferences validates and normalises
BFRES `_n0`, converts it to Blender's basis and assigns it per corner. It is enabled by
default for new installations and can be disabled to let Blender recalculate smooth
normals. Meshes record the result in the
`smo_custom_normals` custom property.

As a repeatable resource validation case, Wooded Kingdom scenario 3 queues all 1,722
placements: 1,179 resolved model resources and 543 resource fallbacks. That includes
139 `MarchingCubeBlockParts` placements selected through the Forest stage-family rule
and 17 static `RippleFixMapParts` surfaces classified as environment.


## Performance and telemetry

StageData roots remain as raw `oead.byml.Array` values until the requested scenario has
been selected. The selected `oead.byml.Hash` is traversed directly, converting each
placement's Python-facing fields and metadata once while retaining numeric conversion,
links and linked placements. Duplicate StageData reads have been removed, and unavailable
optional layers are cached as negative lookups so nested-zone expansion does not repeat
known misses. `WorldList.byml` continues to use the general-purpose reader independently.

ObjectData filenames use a process-local index keyed by ROMFS path. Repeated imports reuse
that index, while the ObjectData directory modification time invalidates it when the
extracted files change. Each import also shares texture-path, archive, decoded-pixel and
Blender-image caches across placements without allowing same-named resources from another
source archive to collide.

### Persistent texture cache

**Use persistent texture cache** in the add-on preferences is disabled by default. When
enabled, each successfully decoded BNTX texture is written lazily as a lossless RGBA PNG
with validated metadata. Later stage and standalone imports query those files before
running the Python BC decoder. Blender colour spaces, alpha modes, normal-blue
reconstruction and image packing are still applied by the existing image pipeline, so
cached and directly decoded pixels produce the same portable `.blend` data.

Cache keys include the source archive path, size and modification time, BFRES member,
texture name and cache schema. Identically named textures in different embedded,
companion or StageTexture archives therefore remain isolated. Source changes and future
cache-schema changes invalidate entries automatically. Missing, corrupt or unwritable
entries fall back to direct BNTX decoding without failing the import.

The preferences show the effective folder, texture count and disk usage. An optional
parent folder can be selected; the extension always creates a dedicated
`odyssey_toolkit/texture_cache` child so **Clear Cache** cannot target the selected
parent itself. Disabling the option stops all cache reads and writes but leaves existing
entries available for later use. Imported images remain packed into the `.blend` and do
not depend on the cache after import.

Blender's bundled NumPy handles normal-map blue reconstruction, vertical image flipping,
byte-to-float conversion, coordinate/unit conversion, loop UV expansion, polygon
smoothing, vertex colours and optional custom-normal expansion. Texture reconstruction
has a pure-Python fallback if NumPy cannot be imported. Mesh topology and source arrays
are validated before Blender's bulk `foreach_set` APIs are used, and all Blender API work
remains on the main thread.

The 0.26.0 cleanup replaces Python per-pixel BNTX channel remapping and alpha scans
with byte-exact bulk operations, caches archive texture-name sets, and caches repeated
BFRES normal-transform coefficients. On the representative benchmark the BC5 SNORM
decode is about 31% faster; the BFRES parser improvement is deliberately smaller because
per-normal multiplication and normalisation still dominate that path. A process-local
ObjectData index is reused for repeated imports from the same ROMFS and invalidated when
**Reload Stages** is pressed or the `ObjectData` directory timestamp changes. Companion
and StageTexture path/archive lookup is also cached within an import. No cache writes into
the ROMFS. The optional persistent texture cache is stored only in its dedicated user
cache folder; geometry remains uncached.

Successful imports print one aggregate performance summary. The primary, diagnostic and
standalone roots store `smo_performance_timings` as JSON and
`smo_performance_total_seconds` as a scalar. Depending on the import path, the JSON
contains counts and totals for StageData/BYML parsing, placement collection, zone
expansion, ObjectData resolution, SZS/Yaz0/SARC loading, BFRES parsing, BNTX decoding,
persistent texture-cache reads and writes, Blender image creation, Blender mesh creation,
preparation and total import time. Import roots also store `smo_texture_cache` as JSON.

## Model resolution report

Run **Export Model Report** from Blender's `F3` operator search to write a JSON file
for the selected stage and scenario.
The report covers every parsed placement. Its summary includes counts by stage layer,
semantic category, resolution status and resolution source, along with unique model
archive and multi-BFRES counts.

Each placement records its actor names, composed transform, source stage, zone path,
links, classification and resolved ObjectData archive and BFRES filenames. The
report also lists every expanded zone and counts placements by source stage.
ObjectData paths are stored relative to the ROMFS, so the report does not expose or
depend on the user's absolute ROMFS path. Unresolved actors and archives without
models are also aggregated by actor name.

## Current release scope

The 0.40 line is the stabilization beta for 1.0. Its supported scope is dependable
stage and scenario reconstruction, standalone asset inspection, native skeletal and
material animation, camera animation, and actionable diagnostics when exact
reconstruction is unavailable.

Collision, runtime-created actors, particle effects, cubemaps, texture arrays, exact
game-renderer shader parity, shape animation, and additional Blender platforms are
outside the 1.0 scope. Diagnostic fallbacks for those systems are expected behavior.
