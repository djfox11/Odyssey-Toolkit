# Odyssey Toolkit

Odyssey Toolkit is a Blender extension for reconstructing and inspecting
user-extracted Super Mario Odyssey content. It combines stage and scenario
importing, standalone BFRES assets, native animations and cameras, material
reconstruction, lighting, rigging, and import diagnostics in one Odyssey-focused
workspace.

## Requirements

- Blender 4.5 LTS
- Windows x64
- A legally obtained, user-extracted Super Mario Odyssey ROMFS for stage workflows

The required `oead` Python wheel is bundled with the extension. No game assets,
keys, ROMFS data, or tools for obtaining them are included.

## Installation

1. Download the versioned ZIP from this repository's **Releases** page.
2. In Blender, open **Edit > Preferences > Get Extensions**.
3. Choose **Install from Disk** and select the downloaded ZIP.
4. Open the **Odyssey** tab in the 3D View sidebar.

The complete workflow guide and compatibility notes are included in
[`smo_kingdom_importer/README.md`](smo_kingdom_importer/README.md).

## Release status

Version 0.40 is the stabilization beta for 1.0. It is intended for clean-install,
real-ROMFS, and production-project feedback. The supported 1.0 scope is dependable
stage reconstruction, standalone asset inspection, animations, cameras, and clear
diagnostics when exact reconstruction is unavailable.

Odyssey Toolkit is licensed under GPL-2.0-only. See [LICENSE](LICENSE).