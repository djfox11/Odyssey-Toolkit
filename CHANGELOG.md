# Changelog

All notable Odyssey Toolkit releases are documented here.

## Unreleased

## 0.41.1 - 2026-08-28

### Fixed

- Fixed textureless and base-colour materials failing to forward the experimental cloth option, which could abort whole static-model archives and omit ground, scenery, and procedural ocean geometry.

## 0.41.0 - 2026-08-27

### Changed

- Renamed the extension package and manifest ID from `smo_kingdom_importer` to `odyssey_toolkit` ahead of 1.0.
- Preserved the established `smo.*` Blender operator IDs and generated scene metadata for compatibility.
- Updated cache storage, installation guidance, and tagged-release packaging for the Odyssey Toolkit identity while retaining legacy cache discovery.
- Reduced the repository to the core add-on, release metadata, and one self-contained tagged-release workflow.

## 0.40.0 - 2026-08-27

### Changed

- Rebranded the public extension from Super Mario Odyssey Kingdom Importer to Odyssey Toolkit while preserving the smo_kingdom_importer package ID, smo.* operators, preferences, and generated metadata.
- Organised the product around stage, asset, animation, and diagnostic workflows.
- Graduated BFRES custom normals and armatures from experimental wording.
- Enabled validated BFRES custom normals by default for new installations.
- Kept armatures and skin weights optional because of stage import cost.
- Added a default-off experimental cloth NoV/Fresnel approximation while retaining parsed shader metadata.
- Made visual stage categories the default; Audio, Technical, and Unclassified placements remain available as opt-in diagnostic categories.
- Added a visible Diagnostics panel with current-stage report export and selected-import summary.
- Raised the supported runtime to Blender 4.5 LTS on Windows x64.
- Bundled the unmodified oead 1.2.9.post4 CPython 3.11 wheel for self-contained installation.

### Release engineering

- Added the GPL 2.0 license text, release glossary, compatibility decision record, reproducible package builder, and explicit release-candidate checklist.
- Retained the existing 38-script Blender regression matrix and static source/ZIP hygiene checks.

## 0.39.1 - 2026-08-23

- Fixed shader and stage-import regressions following the 0.39 material translation work.

## 0.39.0 - 2026-08-23

- Expanded FMAT translation with packed-mask channels, AO, SSS, glass transmission, alpha/render-state handling, face culling, and inactive-output diagnostics.

## 0.38.0 - 0.38.4

- Added native material animation, animated texture matrices, and hierarchy-preserving BFRES segment-scale compensation.

## 0.37.0

- Added native FSCN/FCAM camera animation and improved FSKA scale inheritance.

## 0.36.0 - 0.36.6

- Added native skeletal and bone-visibility animation, companion animation packages, and the initial experimental cloth NoV translation.

## 0.35.0 - 0.35.3

- Added validated FSKL armatures, skin weights, deformation, and corrected source bone axes.

## 0.34.0 and earlier

- Established stage/scenario import, nested zones, model resolution, BFRES/BNTX decoding, materials, textures, sky, approximate lighting, actor registry, diagnostics, cancellation, and performance telemetry.
