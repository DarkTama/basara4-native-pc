"""Button prompt patch must be built on top of the English patch, not on the raw game arc."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "launcher"))

import basara4_prompts as bp  # noqa: E402


def _touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_base_arc_prefers_topmost_lower_layer(tmp_path):
    game = tmp_path / "game"
    _touch(game / bp.ARC_REL, b"game")
    en = tmp_path / "en"
    _touch(en / bp.ARC_REL, b"english")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert bp.find_base_arc(game, [en, empty]) == en / bp.ARC_REL
    assert bp.find_base_arc(game, [empty]) == game / bp.ARC_REL
    assert bp.find_base_arc(game, None) == game / bp.ARC_REL


def test_base_arc_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bp.find_base_arc(tmp_path, [])


def test_rebuilds_when_base_changes(tmp_path, monkeypatch):
    game = tmp_path / "game"
    _touch(game / bp.ARC_REL, b"game")
    en = tmp_path / "en"
    en_arc = _touch(en / bp.ARC_REL, b"english-v1")
    out = tmp_path / "out"
    built = []

    def fake_build(base_arc, out_dir, style="xbox"):
        built.append((Path(base_arc), style))
        _touch(Path(out_dir) / bp.ARC_REL, Path(base_arc).read_bytes() + b"+" + style.encode())
        return Path(out_dir) / bp.ARC_REL

    monkeypatch.setattr(bp, "_build_prompts_arc", fake_build)

    bp.ensure_xbox_prompts(game, out, [en])
    bp.ensure_xbox_prompts(game, out, [en])          # cached
    assert built == [(en_arc, "xbox")]
    assert (out / bp.ARC_REL).read_bytes() == b"english-v1+xbox"

    en_arc.write_bytes(b"english-v2-longer")          # English patch rebuilt
    bp.ensure_xbox_prompts(game, out, [en])
    assert len(built) == 2
    assert (out / bp.ARC_REL).read_bytes() == b"english-v2-longer+xbox"

    bp.ensure_xbox_prompts(game, out, [])             # English patch turned off
    assert built[-1] == (game / bp.ARC_REL, "xbox")

    bp.ensure_kbm_prompts(game, out, [])              # style switch
    assert built[-1] == (game / bp.ARC_REL, "kbm")
