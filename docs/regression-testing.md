# Manual Regression Testing

This checklist establishes the user-visible baseline that cleanup and refactoring
work must preserve. Run it with a legally obtained ROMFS. Do not commit the ROMFS,
extracted assets, generated `.blend` files, screenshots containing game assets, or
console logs that disclose local ROMFS paths.

## Baseline status

The initial comparison version is Odyssey Toolkit v0.41.3. The quantitative
baseline below was captured on 2026-09-03 from commit `2fbaa67`, using the exact
source archive and Blender in background mode. Focused regression scripts also
exercised materials, rigging, ocean and atmosphere special cases, animations,
the texture cache, cancellation, and transactional re-import.

Background runs measure generated data and timing but cannot approve appearance
or viewport responsiveness. Items marked **manual visual sign-off required** are
the remaining human checks; they are not assumed to pass. Keep screenshots and
any game-derived artifacts outside the repository.

Do not replace v0.41.3 measurements after refactoring begins. If a baseline run
must be repeated, use the exact v0.41.3 release package and record why it was
repeated.

## Fixed test environment

Use the same machine and settings for the baseline and comparison runs.

| Field | v0.41.3 baseline |
| --- | --- |
| Odyssey Toolkit source commit | `2fbaa67` |
| Source archive SHA-256 | `D7E376F5ED13001B4E8C0E44AFB5B4E0A6A6A1D40A2794E13AA11B24A1476883` |
| Built package SHA-256 | `4A4B19EBB4C05654841EE44A9F5AFB912F7CBC010499961AE41F8549C6ED8545` |
| Blender version | 4.5.5 LTS (`836beaaf597a`) |
| Windows version | Windows 11 Pro 10.0.26200 |
| CPU | AMD Ryzen 5 3600 6-Core Processor |
| RAM | 15.9 GiB |
| GPU and driver | NVIDIA GeForce RTX 3070, 32.0.15.9636 |
| ROMFS game/update version | Local legally obtained dump; revision not independently identified |
| Default stage-import preferences | Custom normals and lighting on; armatures, experimental cloth, actor registry, audio, technical, and unclassified categories off |
| Test date | 2026-09-03 |

Start from a clean Blender profile. Disable unrelated add-ons, use a new scene,
and keep viewport mode and background applications consistent. Record the exact
stage, scenario, import-category toggles, armature/custom-normal/cloth settings,
and stage-lighting setting for each run.

## What to record

For every completed stage import, select its generated root and copy these custom
properties before editing or saving the scene:

- `smo_placement_count`
- `smo_static_mesh_object_count`
- `smo_cube_fallback_count`
- `smo_unsupported_asset_count`
- `smo_custom_normal_failure_count`
- `smo_texture_errors`
- `smo_performance_total_seconds`
- `smo_performance_timings`
- `smo_import_status`

Also record Blender data-block counts for objects, collections, meshes, materials,
images, armatures, actions, and cameras. Count only data created by the run; begin
from a new file or record before-and-after values. Keep the complete
`smo_performance_timings` JSON with the private test record and enter the principal
category timings in the tables below.

Use a stopwatch only as a secondary wall-clock measurement. The generated timing
properties are the comparison source of truth. A result passes when counts,
generated names, warnings, and visible output match the baseline and no timing
regression remains unexplained. The initial values below are single runs, so use
them as order-of-magnitude guardrails. Before making a performance claim, run each
case three times and record the median and range.

## Kingdom stage matrix

### Simple kingdom

Use a small home stage and scenario with no optional categories enabled. Cap
Kingdom is the preferred case. Verify stage selection, collection hierarchy,
object placement, materials, lighting, and the Diagnostics summary.

| Measurement | v0.41.3 baseline |
| --- | ---: |
| Exact stage and scenario | `CapWorldHomeStage`, scenario 1 |
| Placements | 319 total; 289 model; 271 category-filtered |
| Static mesh objects | 1,041 |
| Diagnostic fallbacks | 30 cubes |
| Unsupported assets | 0 |
| Texture errors | 0 |
| Objects / collections | 1,076 / 9 |
| Meshes / materials / images | 349 / 223 / 346 |
| Total seconds, single run | 151.799 |
| StageData / placements / ObjectData seconds | 0.082 / 0.521 / 1.874 |
| SZS / BFRES / BNTX seconds | 0.666 / 7.810 / 129.719 |
| Blender images / meshes / lighting seconds | 7.738 / 2.311 / 0.102 |

### Nested zones

Use Metro Kingdom, `CityWorldHomeStage`, scenario 2, or another case whose nested
zone hierarchy is known. Verify that nested-zone objects, sky placements, links,
transforms, and generated names are stable. Inspect at least one object two levels
below the root and record its world location, rotation quaternion, and scale.

| Measurement | v0.41.3 baseline |
| --- | ---: |
| Exact stage and scenario | `CityWorldHomeStage`, scenario 2 |
| Expanded zone paths | 1: `CityWorldTimerAthletic000Zone` |
| Placements | 1,528 total; 1,469 model; 767 category-filtered |
| Static mesh objects | 4,647 |
| Two-level object transform | Manual visual sign-off and transform capture required |
| Total seconds, single run | 324.360 |
| Zone-expansion seconds | 0.033 |

### Heavy kingdom

Use a full Metro import or another consistently large kingdom. Keep all chosen
category and import preferences identical between versions. Observe viewport
responsiveness and peak memory as secondary diagnostics.

| Measurement | v0.41.3 baseline |
| --- | ---: |
| Exact stage and scenario | `CityWorldHomeStage`, scenario 2 |
| Enabled import categories | Environment, characters, gameplay, collectibles, effects |
| Placements | 1,528 total; 1,469 model; 767 category-filtered |
| Static mesh objects | 4,647 |
| Diagnostic fallbacks / unsupported assets | 59 / 0 |
| Objects / collections | 4,711 / 9 |
| Meshes / materials / images | 983 / 771 / 971 |
| Total seconds, single run | 324.360 |
| Preparation / resolution / archive-loading seconds | 9.031 / 4.683 / 1.313 |
| BFRES / BNTX / Blender images / meshes seconds | 22.545 / 266.880 / 15.215 / 5.757 |
| Texture warnings | 1 unsupported `HintPhoto_alb.0` format (`0x2D06`) |
| Peak memory and responsiveness notes | Manual visual sign-off required |

## Asset and material matrix

For standalone imports, record the source archive and BFRES names privately, the
generated root name, mesh/material/image counts, warning properties, and total
timing JSON.

### Rigged character

Enable armature import and use a character with a known skeleton and skin weights.
Verify bone hierarchy and names, bind pose, weighted deformation, segment-scale
compensation, and object/armature parenting. Pose representative limbs and confirm
that deformation matches v0.41.3.

### Transparent or glass material

Use a known glass or alpha-blended surface. Compare blend mode, shadows, backface
behavior, alpha channel, transmission/refraction appearance, texture channel
mapping, and sorting from multiple camera angles.

### Cloth material

Enable the same experimental cloth option used for the baseline. Compare the
material graph, view-dependent response, normal/roughness inputs, and appearance
under front, grazing, and back lighting. Confirm that disabling cloth reproduces
the v0.41.3 non-cloth result.

### Ocean and atmosphere

Use a Seaside ocean case and a sky/atmosphere case such as Wooded or a stage with
synthesised sky placement. Verify ocean mesh shape and placement, shared normal
textures, transparency, world nodes, sun direction/energy, atmosphere colors, and
generated Ocean/Lighting collection names.

| Case | Source and options | Generated counts | Total seconds | v0.41.3 result |
| --- | --- | --- | ---: | --- |
| Rigged character | Cap scenario 1; armatures on; warm cache | 189 armature objects, 737 rigged mesh objects, 24 armature data blocks; no reported rig errors | 26.048 | Structural checks passed; posing/deformation needs manual visual sign-off |
| Transparent/glass | Focused `GlassMT` and shader-route checks | Route, alpha, and node assertions passed | Not separately timed | Data checks passed; appearance and sorting need manual visual sign-off |
| Cloth enabled | Cap scenario 1; armatures and experimental cloth on; warm cache | 1,041 mesh objects, 223 materials, 346 images | 26.048 | Import passed; lighting response needs manual visual sign-off |
| Cloth disabled | Cap scenario 1; default options; warm cache | 1,041 mesh objects, 223 materials, 346 images | 22.216 | Import passed |
| Ocean | Focused Seaside special-case regression | Ocean construction and material assertions passed | Not separately timed | Data checks passed; surface appearance needs manual visual sign-off |
| Atmosphere | Focused cloud, sky, sand, ice, and cave regressions | World/sky and material assertions passed | Not separately timed | Data checks passed; appearance needs manual visual sign-off |

The historical standalone Kuribo rigging harness expected a v0.40-era collection
that v0.41.3 no longer creates. The observed v0.41.3 standalone result was 12
static meshes; one skin bind was skipped because the source had 845 skin vertices
for an 846-vertex mesh. Preserve this observed behavior unless a later task
explicitly changes it. A historical Mario hair-mask assertion also did not match
v0.41.3; treat it as a stale assertion, not as a passing baseline.

## Animation matrix

Use known-good skeletal, bone-visibility, material, and camera animations. Record
the source BFRES/archive and selected animation identifier privately. Check start
and end frames, frame rate, looping, action and data-block names, curve/keyframe
counts, interpolation modes, bone/material/camera targets, and values at the first,
middle, and final frames. For visibility, check frames immediately before and at a
transition. For cameras, compare position, quaternion, field of view, clipping,
aspect ratio, and twist.

| Animation type | Source and animation | Frame range | Actions / curves / keys | Apply seconds | v0.41.3 result |
| --- | --- | --- | --- | ---: | --- |
| Skeletal | Kuribo `Wait` | 0–118 (119 frames), looping | 20 bones; 45 source curves; 257 output transform curves | Not separately timed | Values and interpolation assertions passed |
| Visibility | Kuribo visibility animations | Selected clip range; verify per clip | 38 clips available; selected result animated 5 bones and 9 meshes with 5 visibility curves | Not separately timed | Transition assertions passed |
| Material | `EyeMove` | Selected clip range; verify per clip | 2 materials, 2 parameters, 2 actions, 12 TexSrt curves | Not separately timed | Material-animation assertions passed |
| Camera | `DemoOpening01` / `AnimCamera` | 0–5408 at 60 fps | 1 camera action, 8 curves; values checked at 0, 2704, and 5408 | Not separately timed | Perspective camera assertions passed |

The focused animation regression also found 55 skeletal animations for Kuribo.
For `ShineTower` it found 27 animations; `ScaleUp` matched three of four targets
and skipped `ShineNumber`, producing 117 scale channels and four visibility
curves. Preserve those warning and matching semantics.

## Texture-cache runs

Use the same stage or asset for both runs. Before the cold run, clear the cache
through Odyssey Toolkit and confirm its status reports no entries. Import once,
record the cache directory file/byte count and the texture-cache payload, then
close the file and repeat without clearing the cache. Do not count the baseline
warm-up run as the warm measurement.

| Measurement | v0.41.3 cold | v0.41.3 warm |
| --- | ---: | ---: |
| Exact stage/asset | `CapWorldHomeStage`, scenario 1 | Same |
| Cache hits / misses / writes / errors | 0 / 340 / 340 / 0 | 340 / 0 / 0 / 0 |
| Cache bytes read / written | 0 / 72,176,863 | 72,176,863 / 0 |
| Cache files / bytes after run | 681 / 72,176,907 | 681 / 72,176,907 |
| Texture-cache read / write seconds | 0.314 / 5.471 | 2.050 / 0 |
| BNTX decode seconds | 130.500 | 0 (all cache hits) |
| Total seconds, single run | 156.194 | 22.216 |

Confirm that images, transparency, channel mapping, generated names, and data-block
counts are identical between cold and warm results. Corrupt a copied cache entry
outside the baseline cache and confirm the import rejects it, reports one cache
error, and succeeds through direct decoding.

The cold and warm baseline generated identical stage counts. A focused small-cache
regression also passed corruption rejection and direct-decode fallback; its cold
and warm wall times were 2.493 seconds and 0.025 seconds (100.6x), respectively.

## Cancellation and re-import

Use the heavy-kingdom case.

1. Start an import, cancel during object creation, and confirm no partial generated
   result remains when there was no previous successful result.
2. Complete the import and record its generated counts and root properties.
3. Re-import successfully with unchanged settings. Confirm there is one generated
   result, all established object/collection/custom-property names are unchanged,
   and counts match the first successful import.
4. Trigger a controlled failure during a subsequent re-import without modifying
   the ROMFS. Confirm the previous successful objects remain, their names and data
   are restored, partial replacements are removed, `smo_import_status` remains
   `FINISHED`, and the failed attempt is reported separately.
5. Cancel another re-import and confirm the same transactional preservation.

| Measurement | v0.41.3 baseline |
| --- | --- |
| Cancel point and cleanup result | ESC immediately after re-import preparation returned `CANCELLED`; previous root remained `FINISHED`. Fresh-import cleanup still needs manual confirmation. |
| First successful import counts/timing | 1,076 objects, 9 collections, 349 meshes, 223 materials, 346 images; 20.923 seconds |
| Successful re-import counts/timing | `FINISHED`; 1,072 generated objects; generated name/type/parent signature unchanged; 21.780 seconds |
| Failed re-import injection and error | Synthetic `RuntimeError` on the first placement; operator returned `CANCELLED`, prior root remained `FINISHED`, and last-attempt status was `FAILED` |
| Previous-result preservation evidence | Generated signature remained `ead76d0fea6520afc93298311618f9ec0dbf3bf6fb33c6e4f315250ec10d163f` after success and failure |
| Cancelled re-import preservation evidence | Root remained `FINISHED`; generated signature stayed identical after ESC cancellation |

The successful re-import left the same linked objects, meshes, and collections,
but Blender data contained 440 images and 235 materials afterward rather than 346
and 223. These are unlinked data-blocks and are part of the v0.41.3 baseline. The
synthetic failure is reported separately as last-attempt status `FAILED`, while
ESC cancellation records `CANCELLED`.

## Result record

For each comparison run, record the commit, package checksum, test date, pass/fail,
all count differences, timing deltas, screenshots kept outside the repository, and
an explanation for every accepted difference. Stop the refactor when a difference
cannot be explained and behavior cannot be preserved confidently.
