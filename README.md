<div align="center">

<p>
  <img src="/logo.png" height=250px">
</p>

<hr>

**[Installation](#installation) · [Quick Start](#your-first-import) · [Feedback](#feedback)**

</div>

Odyssey Toolkit is a Blender extension for reconstructing and inspecting content from *Super Mario Odyssey*. It brings kingdom stages, assets, animations, cameras, materials, lighting and rigging straight into Blender with no outside conversion needed.

> [!IMPORTANT]
> Odyssey Toolkit does **not** include game assets, encryption keys, ROMFS data, or
> tools for obtaining them. It is required that you legally obtain a copy of the required game data

## Requirements

- Blender 4.5 LTS
- Windows 10/11 x64
- Super Mario Odyssey ROMFS

## Installation

1. Open the repo's **[Releases](https://github.com/djfox11/Odyssey-Toolkit/releases)** page.
2. Download the ZIP archive named `odyssey_toolkit_v0.xx.x.zip`.
3. In Blender, open **Edit → Preferences → Get Extensions**.
4. Open the top-right menu and choose **Choose from Disk...** and select the downloaded ZIP.
5. In the 3D Viewport, press <kbd>N</kbd> and open the **Odyssey** tab.

> [!TIP]
> The compatible `oead` Python wheel is already bundled. You do not need to install the Python package into Blender manually.

## Your First Import

1. Select a ROMFS containing `SystemData\WorldList.szs`, `StageData`, and `ObjectData`.
2. Choose a stage and scenario.
3. Adjust import categories or optional stage lighting if needed.
4. Select **Import Stage**.
5. Watch progress in the panel and Blender status bar; use <kbd>Esc</kbd> to cancel the import.

For individual files, expand **Assets & Animations** and choose **Import Standalone Model**.

## Current Scope

**In the 1.0 scope:**
- Stage and scenario reconstruction
- Standalone model importing
- Support for rigs
- Skeletal, material and camera animations

**Outside the 1.0 scope:**
- Runtime-created actors and game logic
- Particle system recreation
- Exact shader parity
- Cubemaps
- Texture arrays and shape animation
- Additional OSes and Blender versions

## Feedback

Useful beta reports include:
- Blender version and Windows version
- The workflow and stage or asset involved
- Exact reproduction steps
- Diagnostics summary

Please do not attach ROMFS files, extracted game assets, encryption keys, or complete `.blend` projects containing the game data.

## License 

Odyssey Toolkit is distributed under the **GNU General Public License v2.0 or later.**
See [LICENSE](LICENSE) for the complete terms.

---

<div align="center">

*\~Built by djfox11~*

</div>
