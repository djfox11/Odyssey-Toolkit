# Odyssey Toolkit

Odyssey Toolkit is a Blender 4.5 LTS extension for reconstructing and inspecting
user-extracted Super Mario Odyssey content. It brings stage importing, standalone
asset inspection, native animation and camera tools, material reconstruction, and
diagnostics into one Odyssey-focused workspace.

The project is currently on the **0.40 stabilization line**. New feature work is
frozen while the documented Blender regression, clean-profile, interactive, and
external-beta gates are completed for 1.0.

## Supported environment

- Blender 4.5 LTS
- Windows x64
- A legally obtained, user-extracted Super Mario Odyssey ROMFS for stage workflows

The extension bundles its required `oead` Python wheel. It does not contain game
assets, keys, ROMFS data, or tools for obtaining them.

## Install a development build

1. Run `python tools/build_release.py`.
2. In Blender, open **Edit > Preferences > Get Extensions**.
3. Choose **Install from Disk** and select `dist/odyssey_toolkit.zip`.
4. Open the **Odyssey** tab in the 3D View sidebar.

See the [complete user guide](smo_kingdom_importer/README.md) for workflows,
limitations, compatibility notes, and troubleshooting.

## Release status

The acceptance contract for 1.0 is in
[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md). The short roadmap is in
[roadmap.txt](roadmap.txt), and shipped changes are recorded in
[CHANGELOG.md](CHANGELOG.md).

Static release checks and deterministic packaging run on every push and pull
request. The complete Blender matrix requires a local ROMFS and therefore remains
a deliberate maintainer release gate rather than a hosted CI job.

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes during the 1.0
freeze. Report security problems according to [SECURITY.md](SECURITY.md).

Odyssey Toolkit is licensed under GPL-2.0-only. See [LICENSE](LICENSE).
