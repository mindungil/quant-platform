"""
Integration test helpers.

Each service has its own `app` package so we cannot put them all on sys.path
simultaneously. Instead we use importlib to load specific modules from each
service directory on demand.
"""

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Ensure shared/ is importable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_module(name: str, file_path: Path):
    """Load a single Python module from an absolute file path."""
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_service_module(service_dir: str, module_path: str):
    """
    Load a module from a service directory.

    Example:
        load_service_module("market-data", "app.core.validator")
    loads services/market-data/app/core/validator.py
    """
    svc_root = ROOT / "services" / service_dir
    svc_str = str(svc_root)

    # Temporarily prepend service root so intra-package imports work
    inserted = svc_str not in sys.path
    if inserted:
        sys.path.insert(0, svc_str)

    parts = module_path.split(".")
    file_path = svc_root / "/".join(parts)

    if file_path.is_dir():
        file_path = file_path / "__init__.py"
    else:
        file_path = file_path.with_suffix(".py")

    # Use a namespaced module name to avoid collisions
    qualified = f"svc_{service_dir.replace('-', '_')}.{module_path}"

    # Also ensure parent packages exist in sys.modules
    parent_parts = qualified.split(".")
    for i in range(1, len(parent_parts)):
        parent_name = ".".join(parent_parts[:i])
        if parent_name not in sys.modules:
            parent_mod = types.ModuleType(parent_name)
            parent_mod.__path__ = []
            parent_mod.__package__ = parent_name
            sys.modules[parent_name] = parent_mod

    mod = _load_module(qualified, file_path)
    return mod
