# Contributing to Odyssey Toolkit

Thanks for your interest in contributing to Odyssey Toolkit!

Bug reports and feature requests are welcome, as are code contributions that improve the Toolkit's existing functionality.

## Before You Start

Odyssey Toolkit is currently focused on stability and the road to 1.0.

Before beginning a large change, please open an issue first so the proposed work can be discussed before significant time is spent implementing it.

For bugs, use the bug report form. For new ideas, use the feature request form.

## Development Requirements

Odyssey Toolkit currently targets:

- Blender 4.5 LTS
- Windows 10/11 x64
- Python bundled with Blender

Clone or fork the repository and install the `odyssey_toolkit` directory as a
development copy of the Blender extension.

## Making Changes

When contributing code:

- Keep changes focused on a single issue or feature where practical.
- Follow the existing structure and style of the surrounding code.
- Avoid unrelated formatting or refactoring in the same pull request.
- Test affected workflows in Blender 4.5 LTS.
- Update documentation when behaviour visible to users changes.
- Add noteworthy user-facing changes to `CHANGELOG.md` where appropriate.

## Testing

### Automated checks

The unit tests, Ruff checks and package validation run in CI on every pull
request. They need Python 3.11, the version bundled with Blender 4.5; older
Python versions cannot import the add-on modules. Blender's own interpreter
works and already includes `numpy`:

```text
Windows: "C:\Program Files\Blender Foundation\Blender 4.5\4.5\python\bin\python.exe"
macOS:   /Applications/Blender.app/Contents/Resources/4.5/python/bin/python3.11
```

From the repository root, run:

```shell
python -m unittest discover -s tests
python -m ruff check odyssey_toolkit tests tools
python tools/build_package.py --source odyssey_toolkit --archive dist/odyssey_toolkit.zip
python tools/validate_package.py --source odyssey_toolkit --archive dist/odyssey_toolkit.zip
```

Replace `python` with the interpreter path above if Python 3.11 is not on your
`PATH`. Ruff can be installed with `python -m pip install ruff`.

### Manual testing

Before opening a pull request, test the relevant workflow using legally obtained game data.

For importer changes, test more than one stage or asset where practical. For
changes that could affect generated output or import time, compare against the
baseline in the [manual regression checklist](docs/regression-testing.md).

Check Blender's console and Odyssey Toolkit diagnostics for unexpected errors.

## Game Data and Copyrighted Material

Do not commit or upload:

- ROMFS files
- Extracted Super Mario Odyssey assets
- Encryption keys
- Copyrighted textures, models, animations, audio, or other game data
- `.blend` files containing extracted game assets

## Pull Requests

Pull requests should explain:

- What changed
- Why the change was made
- Any related issue
- What was tested
- Any known limitations

Screenshots are welcome.

By contributing, you agree that your contribution will be distributed under the repository's GNU General Public License v2.0 or later.
