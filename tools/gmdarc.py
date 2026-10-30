"""Shared parser/serializer library for ARC + GMD, both endians. Stdlib only.
Re-exported from launcher/gmdarc.py so launcher has zero dependencies on tools/.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_LAUNCHER_GMDARC = Path(__file__).resolve().parent.parent / "launcher" / "gmdarc.py"
_spec = importlib.util.spec_from_file_location("launcher_gmdarc", _LAUNCHER_GMDARC)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load gmdarc from {_LAUNCHER_GMDARC}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

for _attr in dir(_mod):
    if not _attr.startswith("_"):
        globals()[_attr] = getattr(_mod, _attr)

__all__ = [attr for attr in dir(_mod) if not attr.startswith("_")]
