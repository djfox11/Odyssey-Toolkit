# Contributing to Odyssey Toolkit

Odyssey Toolkit is in a 1.0 stabilization freeze. Changes should fix a release
blocker, improve diagnostics or documentation, strengthen tests, or remove risk
from an existing supported workflow. New capabilities belong in the post-1.0
backlog unless they are required to prevent crashes, corruption, installation
failure, or silent unsupported-format behavior.

## Development checks

Use Python 3.11 for repository-only checks:

```powershell
python -m compileall -q smo_kingdom_importer tests tools
python tests/release_hygiene.py
python tools/build_release.py
python tests/release_hygiene.py --zip dist/odyssey_toolkit.zip
```

Before a release candidate, run the complete Blender matrix with Blender 4.5 LTS:

```powershell
python tests/release_hygiene.py --run --blender "C:\path\to\blender.exe" --romfs "D:\path\to\romfs"
```

Then complete every clean-profile and interactive check in
`docs/RELEASE_CHECKLIST.md`.

## Change expectations

- Preserve the extension ID `smo_kingdom_importer`, `smo.*` operator names, saved
  preferences, and generated `smo_*` metadata unless an approved migration exists.
- Add or update a focused regression for behavioral changes.
- Keep import fallbacks visible through diagnostics; do not silently discard data.
- Keep release archives reproducible and the explicit package allowlist accurate.
- Do not commit ROMFS files, extracted game assets, Blender scenes containing game
  data, keys, logs containing private paths, or generated release archives.

Pull requests should state the user-visible problem, the supported workflow
affected, the checks run, and any remaining limitations.
