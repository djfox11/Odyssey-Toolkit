from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "odyssey_toolkit"


def _install_oead_stub() -> None:
    if "oead" in sys.modules:
        return

    stub = ModuleType("oead")
    for name in ("S32", "U32", "S64", "U64", "F32", "F64"):
        setattr(stub, name, type(name, (), {}))
    stub.byml = SimpleNamespace(Hash=dict, Array=list)
    sys.modules["oead"] = stub


def load_toolkit_module(name: str) -> ModuleType:
    _install_oead_stub()
    package = sys.modules.get("odyssey_toolkit")
    if package is None:
        package = ModuleType("odyssey_toolkit")
        package.__path__ = [str(PACKAGE_ROOT)]
        sys.modules["odyssey_toolkit"] = package
    return importlib.import_module(f"odyssey_toolkit.{name}")
