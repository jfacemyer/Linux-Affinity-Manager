"""Finding Affinity installations that already exist on this machine.

AffinityOnLinux leaves no inventory -- it installs into ~/.AffinityLinux or
into one remembered custom path, and anything created before that, or by an
older version, or by hand, is simply somewhere. Before the manager can manage
a set, it has to find the set.

The search is deliberately shallow and targeted. Walking a home directory to
arbitrary depth to find Wine prefixes is slow and finds things nobody asked
about; what is wanted is the handful of places AffinityOnLinux and its users
actually put them.

Within those places it is deliberately unselective. It reports every Wine
prefix, including ones with no Affinity in them, and every prefix already
managed. Filtering those out was the first design and was wrong: a prefix whose
install was interrupted has drive_c, dosdevices and nothing else, and it is the
one case where somebody most needs the manager to admit it can see it.
Describing what each one is and letting the user decide costs a column.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import probe, registry

# Where AffinityOnLinux itself remembers a custom install.
AOL_CONFIG = Path.home() / ".config" / "AffinityOnLinux" / "install_location"

MAX_DEPTH = 3


def search_roots() -> list[Path]:
    """Directories worth looking in, most likely first."""
    user = os.environ.get("USER", "")
    roots = [Path.home()]
    for candidate in (
        Path("/mnt"),
        Path("/media") / user if user else None,
        Path("/run/media") / user if user else None,
        Path.home() / "Games",
        Path("/opt"),
    ):
        if candidate and candidate.is_dir():
            roots.append(candidate)
    return roots


def remembered_paths() -> list[Path]:
    """Installs that a config file names outright, so no searching is needed."""
    found = [Path.home() / ".AffinityLinux"]
    try:
        saved = AOL_CONFIG.read_text().strip()
        if saved:
            found.append(Path(saved).expanduser())
    except OSError:
        pass
    return found


def _scan(root: Path, depth: int, seen: set[str], out: list[Path]) -> None:
    if depth < 0:
        return
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue
        try:
            key = str(entry.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        # Every Wine prefix, not only the ones with Affinity in them. A prefix
        # whose install was interrupted has drive_c and dosdevices and no
        # Affinity.exe, and it is precisely the one the manager can help with --
        # hiding it left the user with a half-built prefix the application
        # claimed not to be able to see. What it is gets reported; what to do
        # about it is theirs to decide.
        if probe.is_prefix(entry):
            out.append(entry)
            # No point descending: drive_c is enormous and holds nothing that
            # is itself a prefix.
            continue
        if entry.name in {".git", ".cache", "node_modules"}:
            continue
        _scan(entry, depth - 1, seen, out)


def find_installations(extra_roots=None) -> list[dict]:
    """Every Wine prefix found, described, newest-modified first.

    Already-managed prefixes are included and flagged rather than hidden: it is
    useful to see that a search turned up nothing new. So are prefixes with no
    Affinity in them yet -- has_affinity says which -- because an interrupted
    install looks exactly like that and is the case most worth surfacing."""
    seen: set[str] = set()
    found: list[Path] = []

    for path in remembered_paths():
        try:
            key = str(path.resolve())
        except OSError:
            continue
        if key not in seen and probe.is_prefix(path):
            seen.add(key)
            found.append(path)

    for root in list(search_roots()) + list(extra_roots or []):
        _scan(Path(root), MAX_DEPTH, seen, found)

    reg = registry.Registry()
    results = []
    for path in found:
        entry = reg.by_path(path)
        info = probe.describe(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        results.append(
            {
                "path": str(path),
                "managed_as": entry["name"] if entry else None,
                "has_affinity": info["has_affinity"],
                "affinity_version": info["affinity_version"],
                "wine": info["wine"],
                "running": info["running"],
                "mtime": mtime,
                "suggested_name": suggest_name_for(path),
            }
        )
    results.sort(key=lambda r: r["mtime"], reverse=True)
    return results


def suggest_name_for(path) -> str:
    """A name derived from where it was found, rather than a dated default --
    'AffinityLinux' from ~/.AffinityLinux says more than 'AffinityLinux
    2026-09-15 03'."""
    name = Path(path).name.lstrip(".").replace("_", " ").strip()
    return name or "Affinity"


def can_move(src, dest_base) -> tuple[bool, str]:
    """Whether moving this prefix is safe, and why not if it is not."""
    src = Path(src).expanduser()
    dest_base = Path(dest_base).expanduser()
    if probe.running_pids(src):
        return False, "It is running. Close Affinity in it first."
    try:
        if dest_base.resolve() in src.resolve().parents or src.resolve() == dest_base.resolve():
            return False, "It is already inside the base directory."
    except OSError:
        pass
    if not os.access(src.parent, os.W_OK):
        return False, f"{src.parent} is not writable, so it cannot be moved out of."
    return True, ""


def move_into_base(src, name, base=None) -> Path:
    """Move a prefix under the base directory, keeping it working.

    Safe to move: a prefix's dosdevices/c: is a relative symlink and the
    registries hold no absolute prefix paths, so nothing inside it refers to
    where it used to be. What does break is anything outside pointing at the old
    path -- desktop entries and AffinityOnLinux's own install_location -- so
    those are the caller's problem to fix afterwards.

    Falls back to copy-then-delete across filesystems, where rename cannot work.
    """
    src = Path(src).expanduser()
    base = Path(base).expanduser() if base else registry.base_dir()
    dest = base / registry.dir_name(name)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    base.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dest)
    except OSError:
        # Different filesystem. copytree with symlinks preserved: dosdevices is
        # nothing but symlinks, and following them would copy whole drives.
        shutil.copytree(src, dest, symlinks=True)
        shutil.rmtree(src)
    return dest
