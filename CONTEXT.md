# Odyssey Toolkit

Odyssey Toolkit is a Blender extension for reconstructing and inspecting content from a user-extracted Super Mario Odyssey ROMFS.

## Language

**Toolkit**:
The complete Blender extension and its collection of import, animation, and diagnostic workflows.
_Avoid_: Kingdom Importer, importer

**Kingdom**:
A user-facing catalogue grouping for related stages; it is not itself an importable resource.
_Avoid_: World, stage

**Stage**:
An importable StageData map viewed in one selected scenario, including its nested zones and placements.
_Avoid_: Kingdom, level

**Scenario**:
One authored state of a stage that selects its placements and related presentation data.
_Avoid_: Variant, version

**Asset**:
A standalone Odyssey resource package that can contain models, materials, textures, rigs, or animations.
_Avoid_: Model, object

**Animation**:
Native or compatibility data applied to an imported rig, material, or camera.
_Avoid_: Motion

**Diagnostic fallback**:
A visible Blender representation of a placement whose game appearance cannot be reconstructed as supported scene content.
_Avoid_: Missing model, placeholder

**Reconstruction**:
A useful Blender-native interpretation of Odyssey data, without promising exact game-renderer or runtime behaviour.
_Avoid_: Emulation, extraction
