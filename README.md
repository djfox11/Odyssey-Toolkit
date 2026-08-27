<div align="center">

# 🌙 Odyssey Toolkit

### Explore, reconstruct, and inspect *Super Mario Odyssey* content in Blender

[![Release](https://img.shields.io/badge/release-0.41.0%20beta-6f42c1?style=for-the-badge)](https://github.com/djfox11/Odyssey-Toolkit/releases)
[![Blender](https://img.shields.io/badge/Blender-4.5%20LTS-F5792A?style=for-the-badge&logo=blender&logoColor=white)](https://www.blender.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-0078D4?style=for-the-badge&logo=windows&logoColor=white)](#requirements)
[![License](https://img.shields.io/badge/license-GPL--2.0--or--later-2EA44F?style=for-the-badge)](LICENSE)

**[Installation](#installation) · [Toolkit](#one-toolkit-four-workflows) · [Quick start](#your-first-import) · [Release status](#road-to-10) · [User guide](odyssey_toolkit/README.md)**

</div>

---

Odyssey Toolkit is a Blender extension for reconstructing and inspecting content
from a user-extracted *Super Mario Odyssey* ROMFS. It brings kingdom stages,
standalone assets, native animations, cameras, materials, lighting, rigging, and
diagnostics together in one focused workspace.

> [!IMPORTANT]
> Odyssey Toolkit does **not** include game assets, encryption keys, ROMFS data, or
> tools for obtaining them. Stage workflows require your own legally obtained,
> user-extracted game files.

## One toolkit, four workflows

| Workflow | What it does |
| :-- | :-- |
| 🗺️ **Stage Importer** | Reconstructs scenarios, nested zones, placements, geometry, materials, textures, sky, and approximate lighting. |
| 📦 **Asset Importer** | Opens standalone SZS and BFRES model packages without requiring a stage import. |
| 🎬 **Animation Tools** | Applies supported skeletal, material, visibility, and camera animation to Blender-native data. |
| 🔎 **Diagnostics** | Surfaces model resolution, fallbacks, warnings, performance information, and cache state instead of silently dropping unsupported content. |

The Toolkit reads `SZS/Yaz0`, `SARC`, `BYML`, `BFRES`, and `BNTX` directly and
creates editable Blender objects, materials, images, armatures, actions, and cameras.

> [!NOTE]
> This is a reconstruction and inspection toolkit—not a game renderer or runtime
> emulator. When exact recreation is unavailable, Odyssey Toolkit favors useful
> Blender data and visible diagnostics over hidden failure.

## Installation

1. Open the repository's **[Releases](https://github.com/djfox11/Odyssey-Toolkit/releases)** page.
2. Download the versioned extension archive, such as `odyssey_toolkit_v0.41.0.zip`.
3. In Blender, open **Edit → Preferences → Get Extensions**.
4. Open the top-right menu and choose **Install from Disk…**.
5. Select the downloaded ZIP and restart Blender if prompted.
6. In the 3D Viewport, press <kbd>N</kbd> and open the **Odyssey** tab.

> [!IMPORTANT]
> Upgrading from 0.40.0 or earlier requires removing the old
> **smo_kingdom_importer** extension, restarting Blender, and then installing
> 0.41.0. The package ID changed as part of the Odyssey Toolkit rename.

> [!TIP]
> The compatible `oead` Python wheel is already bundled. You do not need to install
> Python packages into Blender manually.

## Your first import

```text
Odyssey tab
└── Stage Importer
    ├── Source → choose your extracted romfs folder
    ├── Stage → choose a kingdom stage
    ├── Scenario → choose the scenario to reconstruct
    └── Import Stage
```

1. Select a ROMFS containing `SystemData/WorldList.szs`, `StageData`, and `ObjectData`.
2. Choose a stage and scenario.
3. Adjust import categories or optional stage lighting if needed.
4. Select **Import Stage**.
5. Watch progress in the panel and Blender status bar; use <kbd>Esc</kbd> to cancel safely.

For individual files, expand **Assets & Animations** and choose
**Import Standalone Model**. The full set of workflows, controls, compatibility
notes, and troubleshooting guidance lives in the
**[complete user guide](odyssey_toolkit/README.md)**.

## Requirements

| Requirement | Supported target |
| :-- | :-- |
| **Blender** | 4.5 LTS |
| **Operating system** | Windows x64 |
| **Stage source** | Legally obtained, user-extracted *Super Mario Odyssey* ROMFS |
| **Python dependency** | Bundled `oead 1.2.9.post4` wheel |

## Scope and expectations

| In the 1.0 scope | Outside the 1.0 scope |
| :-- | :-- |
| Dependable stage and scenario reconstruction | Runtime-created actors and game logic |
| Standalone model and texture inspection | Collision and particle-system recreation |
| Supported rigs and native animation | Exact game-renderer shader parity |
| Material and camera animation | Cubemaps, texture arrays, and shape animation |
| Actionable fallbacks and diagnostics | Additional operating systems and Blender versions |

Unsupported content may appear as a diagnostic fallback, placeholder, or warning.
That visibility is intentional: it keeps reconstruction gaps inspectable.

## Road to 1.0

Version **0.41.0** is the stabilization beta that completes Odyssey Toolkit's package rename ahead of 1.0.

- [x] Toolkit-first identity and focused Blender interface
- [x] Stage, asset, animation, camera, and diagnostic workflows
- [x] Self-contained Windows x64 extension package
- [x] Conservative defaults and visible unsupported-format reporting
- [ ] Wider clean-install and real-project beta feedback
- [ ] Final Blender 4.5 qualification and release-blocker pass

> [!WARNING]
> Treat 0.41 as a beta for production projects. Keep backups of important `.blend`
> files and report installation failures, crashes, data corruption, or silent import
> failures before the 1.0 release.

## Repository layout

```text
.
├── odyssey_toolkit/            # Blender extension source and bundled runtime files
├── .github/workflows/          # Tagged release packaging
├── CHANGELOG.md                # Release history
├── LICENSE                     # GPL-2.0-or-later license
└── README.md                   # Project landing page
```

## Feedback

Useful beta reports include:

- Blender version and Windows version
- The workflow and stage or asset involved
- Exact reproduction steps
- The Diagnostics summary and relevant console output
- Whether the issue reproduces in a clean Blender profile

Please do not attach ROMFS files, extracted assets, encryption keys, or complete
`.blend` projects containing copyrighted game data.

<details>
<summary><strong>Legal and project-scope note</strong></summary>

Odyssey Toolkit is an independent community project and is not affiliated with,
endorsed by, or sponsored by Nintendo. *Super Mario Odyssey*, Nintendo, and related
names and marks belong to their respective owners. Users are responsible for
complying with the laws and license terms applicable to their own game files.

</details>

## License

Odyssey Toolkit is distributed under the **GNU General Public License v2.0 or later**.
See [LICENSE](LICENSE) for the complete terms.

---

<div align="center">

**Built for exploration, preservation, and better Blender workflows.**

</div>
