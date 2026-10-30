"""Launch-time sparse overlay directory for Basara 4 English Translation Patch.
Creates a sparse mirror of game/ using junctions and hard links so that game/ is never
written to or modified.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any


def _is_link_or_reparse(entry: os.DirEntry) -> bool:
    if hasattr(entry, "is_junction") and entry.is_junction():
        return True
    if entry.is_symlink():
        return True
    try:
        st = entry.stat(follow_symlinks=False)
        attrs = getattr(st, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if attrs & reparse_flag:
            return True
    except OSError:
        pass
    return False


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _check_not_nested(overlay: Path, game: Path) -> None:
    overlay_res = overlay.resolve()
    game_res = game.resolve()
    if overlay_res == game_res:
        raise ValueError(f"Refusing operation: overlay path equals game path ({overlay_res})")
    if _is_relative_to(overlay_res, game_res):
        raise ValueError(f"Refusing operation: overlay path is inside game path ({overlay_res} inside {game_res})")
    if _is_relative_to(game_res, overlay_res):
        raise ValueError(f"Refusing operation: game path is inside overlay path ({game_res} inside {overlay_res})")


def _safe_remove_tree(path: Path | str, game: Path | str | None = None) -> None:
    """Safely removes an overlay tree without ever recursing into junction/symlink targets."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        if not path.exists():
            return
        st = None

    if game is not None:
        _check_not_nested(path, Path(game))

    # If path itself is a junction or symlink, remove only the link
    is_link = False
    if st is not None:
        attrs = getattr(st, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if os.path.islink(path) or bool(attrs & reparse_flag):
            is_link = True
    if is_link:
        if os.path.isdir(path):
            os.rmdir(path)
        else:
            os.unlink(path)
        return

    # Path is a real directory
    with os.scandir(path) as it:
        for entry in it:
            if _is_link_or_reparse(entry):
                if entry.is_dir(follow_symlinks=False):
                    os.rmdir(entry.path)
                else:
                    os.unlink(entry.path)
            elif entry.is_dir(follow_symlinks=False):
                _safe_remove_tree(Path(entry.path), game=game)
            else:
                try:
                    os.unlink(entry.path)
                except PermissionError:
                    os.chmod(entry.path, stat.S_IWRITE)
                    os.unlink(entry.path)

    os.rmdir(path)


def _link_file(src: Path, dst: Path) -> None:
    """Hard links src to dst, falling back to copy2."""
    if dst.is_file() or dst.is_symlink():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _link_or_recurse_dir(src: Path, dst: Path) -> None:
    """Creates a directory junction from dst to src.
    Falls back to symlink, then to recursing with real dirs + file links.
    """
    if dst.exists():
        return

    # Try junction
    try:
        import _winapi
        _winapi.CreateJunction(str(src), str(dst))
        return
    except (AttributeError, OSError):
        pass

    # Try symlink
    try:
        os.symlink(src, dst, target_is_directory=True)
        return
    except OSError:
        pass

    # Recurse with real dirs + file links
    dst.mkdir(parents=True, exist_ok=True)
    with os.scandir(src) as it:
        for entry in it:
            c_src = src / entry.name
            c_dst = dst / entry.name
            if entry.is_dir(follow_symlinks=False):
                _link_or_recurse_dir(c_src, c_dst)
            else:
                _link_file(c_src, c_dst)


def build_overlay(game: Path | str, patch_root: Path | str, overlay: Path | str) -> Path:
    """Builds an idempotent sparse overlay directory pointing to game and patch files."""
    game = Path(game)
    patch_root = Path(patch_root)
    overlay = Path(overlay)

    _check_not_nested(overlay, game)

    if not patch_root.is_dir():
        return game

    # Collect replaced files
    replaced_files: dict[Path, Path] = {}
    for p in patch_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(patch_root)
        if rel.as_posix() == "manifest.json" or rel.name == ".overlay_stamp.json":
            continue
        replaced_files[rel] = p

    if not replaced_files:
        return game

    # Read patch manifest if present
    db_sha256 = ""
    manifest_file = patch_root / "manifest.json"
    if manifest_file.is_file():
        try:
            m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            db_sha256 = m_data.get("db_sha256", "")
        except (OSError, ValueError):
            pass

    # Build target stamp dict
    stamp_replaced = [
        {
            "rel": rel.as_posix(),
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        }
        for rel, p in sorted(replaced_files.items(), key=lambda x: x[0].as_posix())
    ]
    expected_stamp = {
        "game": str(game.resolve()),
        "db_sha256": db_sha256,
        "replaced": stamp_replaced,
    }

    # Check existing overlay stamp
    stamp_file = overlay / ".overlay_stamp.json"
    if overlay.is_dir() and stamp_file.is_file():
        try:
            existing_stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
            if (
                existing_stamp.get("game") == expected_stamp["game"]
                and existing_stamp.get("db_sha256") == expected_stamp["db_sha256"]
                and len(existing_stamp.get("replaced", [])) == len(stamp_replaced)
            ):
                match = True
                stamp_map = {item["rel"]: item for item in existing_stamp.get("replaced", [])}
                for item in stamp_replaced:
                    prev = stamp_map.get(item["rel"])
                    if not prev or prev.get("size") != item["size"] or abs(prev.get("mtime", 0) - item["mtime"]) > 1e-4:
                        match = False
                        break
                    # Also check that the file actually exists in overlay
                    if not (overlay / Path(item["rel"])).is_file():
                        match = False
                        break
                if match:
                    return overlay
        except (OSError, ValueError):
            pass

    # If we reached here, need to rebuild: clean old overlay
    if overlay.exists() or os.path.islink(overlay):
        _safe_remove_tree(overlay, game=game)

    overlay.mkdir(parents=True, exist_ok=True)

    # Compute all ancestor directories leading to replaced files
    replaced_dirs: set[Path] = {Path(".")}
    for rel in replaced_files:
        parent = rel.parent
        while parent != Path("."):
            replaced_dirs.add(parent)
            parent = parent.parent

    replaced_files_lower: dict[str, tuple[Path, Path]] = {
        rel.as_posix().lower(): (rel, patch_path)
        for rel, patch_path in replaced_files.items()
    }
    replaced_dirs_lower: set[str] = {d.as_posix().lower() for d in replaced_dirs}

    # Process directories in order of path parts depth
    sorted_dirs = sorted(replaced_dirs, key=lambda d: len(d.parts))
    for rel_dir in sorted_dirs:
        overlay_sub = overlay / rel_dir
        overlay_sub.mkdir(parents=True, exist_ok=True)
        game_sub = game / rel_dir

        if game_sub.is_dir():
            with os.scandir(game_sub) as it:
                for entry in it:
                    child_rel = Path(entry.name) if rel_dir == Path(".") else rel_dir / entry.name
                    child_key = child_rel.as_posix().lower()
                    dst_child = overlay / child_rel

                    if entry.is_dir(follow_symlinks=False):
                        if child_key in replaced_dirs_lower:
                            # Subdirectory is on the path to a replaced file;
                            # will be handled in its turn in sorted_dirs.
                            pass
                        else:
                            # Child directory is untouched -> junction
                            _link_or_recurse_dir(Path(entry.path), dst_child)
                    else:
                        # Entry is a file
                        if child_key in replaced_files_lower:
                            _, patch_file = replaced_files_lower[child_key]
                            _link_file(patch_file, dst_child)
                        else:
                            _link_file(Path(entry.path), dst_child)

    # Link any replaced files that might not have existed in game
    for rel, patch_file in replaced_files.items():
        dst = overlay / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            _link_file(patch_file, dst)

    # Write stamp file
    stamp_file.write_text(json.dumps(expected_stamp, indent=2), encoding="utf-8")

    return overlay
