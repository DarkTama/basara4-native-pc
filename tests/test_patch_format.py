"""An English patch built by an older converter must be detectable so the launcher can ask for a rebuild."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "launcher"))

import basara4_repatch as rp  # noqa: E402


def _manifest(tmp_path, **data):
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_missing_manifest_is_zero(tmp_path):
    assert rp.patch_format(tmp_path) == 0


def test_legacy_ps3_import_is_format_1(tmp_path):
    assert rp.patch_format(_manifest(tmp_path, mode="ps3_import")) == 1
    assert rp.patch_format(tmp_path) < rp.PATCH_FORMAT


def test_current_format(tmp_path):
    assert rp.patch_format(_manifest(tmp_path, mode="ps3_import", format=rp.PATCH_FORMAT)) == rp.PATCH_FORMAT


def test_non_ps3_patch_is_zero(tmp_path):
    assert rp.patch_format(_manifest(tmp_path, mode="db")) == 0
