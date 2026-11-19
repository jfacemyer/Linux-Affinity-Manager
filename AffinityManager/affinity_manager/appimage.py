"""AppImage installs: installing from an AppImage, upgrading, going back.

The AppImage carries a ready-made prefix and the Wine that runs it. Starting
it the first time copies that prefix out of the image into a folder of the
user's; after that the image only supplies Wine. That leaves two questions the
AppImage used never to answer: what happens to an install when a newer
AppImage arrives, and how to get back if the newer one is worse.

The answer here rests on one observation. Of an install's six or seven
gigabytes, everything but about sixty megabytes is program -- Affinity, .NET,
WebView2, Wine's own files -- and the AppImage file already holds all of it.
What only the user has is his settings, presets, fonts and sign-in. So:

  - An upgrade builds a fresh prefix from the new image and carries the user's
    own files into it. Nothing of the old program is copied.
  - The old install is renamed aside, not copied, so keeping it for a rollback
    costs no time and no extra space beyond what it already used.
  - A settings snapshot is taken before every upgrade, always. It is small.

Everything lives beside the install, under fixed names, so it can be found by
the AppImage without the manager and by a person with a file manager:

    P                 the install (by default ~/.affinity-appimage-wineprefix)
    P.previous        the install before the last upgrade -- at most one
    P.backups/        settings snapshots, and the journal of an upgrade
    P.upgrading       an upgrade being built; P.installing, a first install;
    P.switching, P.removing
                      the brief names of a swap or a delete in progress

Two kinds of install:

  linked    Wine stays inside the AppImage file, which runs the prefix. What
            the AppImage itself makes.
  unpacked  the image's Wine is copied into the prefix, which is then an
            ordinary managed prefix that no longer needs the AppImage. What
            the manager makes when asked to install from an AppImage.

This module is shared. The AppImage ships a copy and its AppRun calls it, and
the manager calls it directly, so there is one implementation of each step,
and an install made by one can be upgraded or rolled back by the other.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import liveness, maintenance, prefsseed, probe, regfile, snapshots

ENV_PREFIX = "AFFINITY_APPIMAGE_PREFIX"
IMAGE_MANIFEST = "affinity-appimage.json"   # inside the image, beside AppRun
MARKER = ".affinity-appimage.json"          # inside an install
LEGACY_MARKER = ".appimage-setup-complete"  # what AppImages up to 3.3.1 wrote
FORMAT = 1

LINKED = "linked"
UNPACKED = "unpacked"

PROGRAM_IMAGE = "image"   # upgrade to the image's Affinity
PROGRAM_KEEP = "keep"     # keep the installed Affinity, update around it

PREVIOUS = ".previous"
BACKUPS = ".backups"
UPGRADING = ".upgrading"
INSTALLING = ".installing"
SWITCHING = ".switching"
REMOVING = ".removing"
# Every sibling name this module creates. Anything it deletes must end in one
# of the transient ones, so a bug can never point rmtree at an install.
TRANSIENT = (UPGRADING, INSTALLING, SWITCHING, REMOVING)
SIBLINGS = (PREVIOUS, BACKUPS) + TRANSIENT

JOURNAL = "operation.json"
KEEP_AUTO_SNAPSHOTS = 5
WINE_LINK = "ElementalWarriorWine"


class AppImageError(RuntimeError):
    pass


class NotAnImage(AppImageError):
    pass


class Busy(AppImageError):
    """Wine is running in the install."""


# ── names ────────────────────────────────────────────────────────────────────


def default_prefix() -> Path:
    return Path.home() / ".affinity-appimage-wineprefix"


def prefix_from_env(env=None) -> Path:
    value = (env if env is not None else os.environ).get(ENV_PREFIX, "").strip()
    return Path(value).expanduser() if value else default_prefix()


def _sibling(prefix, suffix: str) -> Path:
    p = Path(prefix).expanduser()
    return p.with_name(p.name + suffix)


def previous_of(prefix) -> Path:
    return _sibling(prefix, PREVIOUS)


def backups_of(prefix) -> Path:
    return _sibling(prefix, BACKUPS)


def sibling_role(path) -> tuple[str, Path] | None:
    """(suffix, the install it belongs to) for a sibling this module made.

    For discovery: a P.previous is a whole prefix, and listing it as an
    install of its own would invite somebody to launch or adopt it."""
    p = Path(path)
    for suffix in SIBLINGS:
        if p.name.endswith(suffix) and len(p.name) > len(suffix):
            return suffix, p.with_name(p.name[: -len(suffix)])
    return None


def owner_of_backups(path) -> Path | None:
    role = sibling_role(path)
    return role[1] if role and role[0] == BACKUPS else None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def version_key(text) -> tuple:
    """'3.3.1.3786' -> (3, 3, 1, 3786), for comparing. Non-numbers sort low."""
    return tuple(int(n) for n in re.findall(r"\d+", str(text or "")))


# ── the image ────────────────────────────────────────────────────────────────


@dataclass
class Image:
    """An AppImage's contents: a mounted image, or an extracted one."""

    root: Path
    manifest: dict
    path: Path | None = None        # the .AppImage file, when known

    @classmethod
    def load(cls, root, path=None) -> "Image":
        root = Path(root)
        try:
            data = json.loads((root / IMAGE_MANIFEST).read_text())
        except (OSError, ValueError):
            raise NotAnImage(
                f"{root} has no {IMAGE_MANIFEST}. Only AppImages built with "
                "upgrade support (after 3.3.1) can install or upgrade from "
                "here.") from None
        if not isinstance(data, dict) or not isinstance(data.get("format"), int):
            raise NotAnImage(f"{root / IMAGE_MANIFEST} is not a manifest this understands.")
        if data["format"] > FORMAT:
            raise NotAnImage(
                f"This AppImage's manifest is format {data['format']}; this "
                f"code understands format {FORMAT}. Update the manager.")
        image = cls(root, data, Path(path) if path else None)
        if not (image.prefix_dir / "drive_c").is_dir():
            raise NotAnImage(f"{image.prefix_dir} is not a prefix.")
        return image

    def _text(self, key, fallback="") -> str:
        v = self.manifest.get(key)
        return v if isinstance(v, str) else fallback

    @property
    def prefix_dir(self) -> Path:
        return self.root / self._text("prefix_dir", "wineprefix")

    @property
    def wine_dir(self) -> Path:
        return self.root / self._text("wine_dir", "usr")

    @property
    def version(self) -> str:
        """The image's own version, e.g. 3.3.2-1."""
        return self._text("image_version", "unknown")

    @property
    def built(self) -> str:
        """When it was built, ISO 8601 UTC. What "newer" is decided on."""
        return self._text("built")

    @property
    def affinity(self) -> str:
        return self._text("affinity") or probe.affinity_version(self.prefix_dir) or "unknown"

    @property
    def wine(self) -> str:
        return self._text("wine", "unknown")

    @property
    def wine_build(self) -> str:
        """The directory name an unpacked install gives the image's Wine."""
        name = self._text("wine_build") or f"AppImage-wine-{self.wine}"
        return re.sub(r"[^A-Za-z0-9._-]+", "-", name)

    @property
    def overlay(self) -> list[str]:
        """Files in Affinity's folder that are not Serif's: the plugin loader,
        WineFix, vkd3d-proton. Laid over a kept Affinity, so keeping a newer
        Affinity does not mean keeping old fixes."""
        items = self.manifest.get("overlay")
        return [i for i in items if isinstance(i, str) and ".." not in Path(i).parts] \
            if isinstance(items, list) else []

    def describe(self) -> dict:
        return {"version": self.version, "built": self.built,
                "affinity": self.affinity, "wine": self.wine,
                "path": str(self.path) if self.path else None}


# ── an install ───────────────────────────────────────────────────────────────


def read_marker(prefix) -> dict | None:
    try:
        data = json.loads((Path(prefix).expanduser() / MARKER).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_install(prefix) -> bool:
    """Made by an AppImage, or from one: either marker is there."""
    p = Path(prefix).expanduser()
    return (p / MARKER).is_file() or (p / LEGACY_MARKER).is_file()


@dataclass
class Install:
    path: Path
    kind: str
    marker: dict = field(default_factory=dict)

    @property
    def legacy(self) -> bool:
        """Made by an AppImage with no manifest (3.3.1 and earlier). Such an
        install may have gained a marker since -- the manager writes one when
        told where its AppImage is -- but never a build time."""
        return not self.built

    @property
    def image(self) -> dict:
        v = self.marker.get("image")
        return v if isinstance(v, dict) else {}

    @property
    def image_path(self) -> Path | None:
        v = self.image.get("path")
        return Path(v) if isinstance(v, str) and v else None

    @property
    def image_version(self) -> str:
        return str(self.image.get("version") or ("3.3.1 or earlier" if self.legacy else "unknown"))

    @property
    def built(self) -> str:
        v = self.image.get("built")
        return v if isinstance(v, str) else ""

    @property
    def affinity(self) -> str | None:
        return probe.affinity_version(self.path)

    @property
    def wine(self) -> str:
        return str(self.image.get("wine") or "unknown")

    @property
    def declined(self) -> str:
        v = self.marker.get("declined")
        return v if isinstance(v, str) else ""

    @property
    def history(self) -> list:
        v = self.marker.get("history")
        return v if isinstance(v, list) else []

    @property
    def previous(self) -> "Install | None":
        prev = previous_of(self.path)
        return inspect(prev) if probe.is_prefix(prev) else None

    def describe(self) -> dict:
        prev = self.previous
        return {
            "path": str(self.path), "kind": self.kind, "legacy": self.legacy,
            "image": self.image, "affinity": self.affinity,
            "previous": {"path": str(prev.path), "affinity": prev.affinity,
                         "image_version": prev.image_version} if prev else None,
            "backups": str(backups_of(self.path)),
            "interrupted": interrupted(self.path),
        }


def inspect(prefix) -> Install | None:
    """What kind of AppImage install this is, or None if it is not one."""
    p = Path(prefix).expanduser()
    marker = read_marker(p)
    if marker is not None:
        kind = marker.get("kind") if marker.get("kind") in (LINKED, UNPACKED) else LINKED
        return Install(p, kind, marker)
    if (p / LEGACY_MARKER).is_file():
        return Install(p, LINKED, {})
    return None


def write_marker(prefix, image: Image | None, kind: str, *, base: dict | None = None,
                 event: str | None = None, image_path=None) -> dict:
    """Write an install's marker. `base` is the marker it replaces, whose
    history is kept; `event` is appended to it."""
    data = dict(base or {})
    data["format"] = FORMAT
    data["kind"] = kind
    data.setdefault("created", _now())
    if image is not None:
        info = image.describe()
        if image_path:
            info["path"] = str(image_path)
        data["image"] = info
        data.pop("declined", None)
    elif image_path:
        data.setdefault("image", {})["path"] = str(image_path)
    if event:
        history = list(data.get("history") or [])
        history.append({"when": _now(), "event": event})
        data["history"] = history[-50:]
    target = Path(prefix).expanduser() / MARKER
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(target)
    # Kept for an older AppImage started on this install: without it, it
    # would copy its own prefix over this one.
    (Path(prefix).expanduser() / LEGACY_MARKER).touch()
    return data


def remember_image(prefix, image_path) -> None:
    """Record where the AppImage that just started this install is.

    Every start, because AppImages get moved and renamed; the manager reads it
    to know what to launch."""
    inst = inspect(prefix)
    if inst is None or not image_path:
        return
    if inst.image_path and str(inst.image_path) == str(image_path):
        return
    base = dict(inst.marker)
    if inst.legacy:
        # Located by hand, for an install an old AppImage made: say so, rather
        # than letting it become an install of "unknown" origin.
        base["image"] = {"version": inst.image_version}
    write_marker(prefix, None, inst.kind, base=base, image_path=image_path)


def decline(prefix, image: Image) -> None:
    """Remember "not now" for this image, so it does not ask on every start."""
    inst = inspect(prefix)
    if inst is None:
        return
    data = dict(inst.marker)
    data["declined"] = image.built or image.version
    write_marker(prefix, None, inst.kind, base=data)


def compare(image: Image, inst: Install) -> str:
    """'newer', 'same' or 'older': the image against the install.

    Decided on build time, which is the one thing that always moves forward.
    An install with no marker came from an AppImage older than any that
    writes one."""
    if inst.legacy or not inst.built:
        return "newer"
    if not image.built:
        return "same"
    if image.built > inst.built:
        return "newer"
    return "same" if image.built == inst.built else "older"


def should_offer(image: Image, inst: Install) -> bool:
    return compare(image, inst) == "newer" and inst.declined != (image.built or image.version)


# ── checks ───────────────────────────────────────────────────────────────────


def require_quiet(prefix) -> None:
    """Nothing at all running in it -- not Affinity, not leftovers.

    Stricter than a backup's check, because an upgrade renames the prefix
    away and edits its registry files: a wineserver still holding them would
    write the old registry back over the new one when it exits."""
    p = Path(prefix).expanduser()
    if not p.exists():
        return
    now = liveness.scan(p)
    if now.state != liveness.QUIET:
        raise Busy(f"{now.summary()} Close Affinity in {p} first"
                   + ("" if now.state == liveness.RUNNING else
                      ", or end the leftover processes") + ".")


def _free(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def _remove(path: Path) -> None:
    """Delete one of this module's own siblings -- never anything else."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    if not path.name.endswith(TRANSIENT):
        raise AppImageError(f"Refusing to delete {path}: not a name this made.")
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _discard(path: Path) -> None:
    """Rename a sibling to P.removing-style name, then delete it, so a delete
    cut short never leaves something that looks like a usable install."""
    path = Path(path)
    if not path.exists():
        return
    doomed = path.with_name(path.name + REMOVING)
    _remove(doomed)
    path.rename(doomed)
    _remove(doomed)


# ── the journal ──────────────────────────────────────────────────────────────
#
# Written before each phase of an upgrade or a switch, so one that was cut
# short -- power, a kill, a full disk -- can be finished or undone from what
# is on disk. Every phase is safe to run twice.


def journal_path(prefix) -> Path:
    return backups_of(prefix) / JOURNAL


def read_journal(prefix) -> dict | None:
    try:
        data = json.loads(journal_path(prefix).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _journal(prefix, **fields) -> dict:
    data = dict(read_journal(prefix) or {})
    data.update(fields)
    target = journal_path(prefix)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(target)
    return data


def _journal_done(prefix) -> None:
    try:
        journal_path(prefix).unlink()
    except FileNotFoundError:
        pass


def interrupted(prefix) -> str | None:
    """What was interrupted here, in a sentence, or None."""
    j = read_journal(prefix)
    if j:
        return f"An {j.get('op', 'operation')} started {j.get('started', '?')} did not finish."
    for suffix in (UPGRADING, INSTALLING, SWITCHING):
        if _sibling(prefix, suffix).exists():
            return f"{_sibling(prefix, suffix).name} was left behind."
    return None


# ── copying ──────────────────────────────────────────────────────────────────


def _build_prefix(image: Image, dest: Path, kind: str, progress=None) -> None:
    """A fresh prefix from the image, at dest (which must not exist)."""
    def step(pct, text):
        if progress:
            progress(pct, text)

    step(0, "Copying the prefix out of the AppImage")
    maintenance.copy_tree(image.prefix_dir, dest,
                          progress=(lambda p: step(p * (0.8 if kind == UNPACKED else 1),
                                                   "Copying the prefix out of the AppImage")))
    if kind == UNPACKED:
        step(80, "Copying Wine into the prefix")
        build = dest / image.wine_build
        maintenance.copy_tree(image.wine_dir, build,
                              progress=lambda p: step(80 + p * 0.2, "Copying Wine into the prefix"))
        link = dest / WINE_LINK
        if link.is_symlink() or link.exists():
            link.unlink()
        # Relative, so it still points at the build after the prefix is
        # renamed into place -- or moved, or cloned.
        link.symlink_to(image.wine_build)


def _copy_users(src: Path, dst: Path) -> int:
    """The Windows user profiles, from an old install into a new one.

    Everything in them is the user's -- settings, presets, the sign-in, the
    WebView2 profile, autosaves of unsaved documents -- so all of it travels
    except Temp. Symlinks (Documents and the like point into the real home)
    are copied as links, never followed. Affinity's AI models are hard-linked:
    half a gigabyte that is never rewritten in place, so the old install and
    the new one can share it."""
    count = 0
    for root, dirs, files in os.walk(src, followlinks=False):
        rel = Path(root).relative_to(src)
        if rel.parts[-3:] == ("AppData", "Local", "Temp") or \
                (len(rel.parts) >= 3 and rel.parts[1:4] == ("AppData", "Local", "Temp")):
            dirs[:] = []
            continue
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in list(dirs):
            s = Path(root) / name
            if s.is_symlink():
                dirs.remove(name)
                _replace_link(s, target_dir / name)
                count += 1
        for name in files:
            s = Path(root) / name
            t = target_dir / name
            if t.is_symlink() or (t.exists() and not t.is_dir()):
                t.unlink()
            if s.is_symlink():
                _replace_link(s, t)
            elif "modelcache" in rel.parts:
                try:
                    os.link(s, t)
                except OSError:
                    shutil.copy2(s, t)
            else:
                shutil.copy2(s, t)
            count += 1
    return count


def _login_name() -> str:
    """The name Wine gives the Windows user when nothing fixes it: $USER, as
    Wine reads it, or the login name."""
    name = os.environ.get("USER", "").strip()
    if name:
        return name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def _adopt_profile(old: Path, new: Path) -> list[str]:
    """The old install's Windows profile, under the new install's user name.

    An image that runs under a fixed Windows user name (probe.windows_user),
    upgrading an install that ran under the login name (AppImages up to 3.3.1):
    the settings, the sign-in and the presets are in the old name's profile,
    and Affinity would otherwise start as a new user. Affinity keeps almost no
    paths with the profile's name in them (its download cache, which it
    rebuilds), so the folder is renamed and nothing inside is rewritten."""
    target = probe.windows_user(new)
    if not target:
        return []
    users = new / "drive_c" / "users"
    if (users / target).exists():
        return []
    source = probe.windows_user(old) or _login_name()
    if source == target:
        return []
    src = users / source
    if not src.is_dir() or src.is_symlink():
        # Not the login name (made under another account, or on another
        # machine): the one profile with Affinity settings in it, if only one.
        found = [d for d in users.iterdir()
                 if d.is_dir() and not d.is_symlink()
                 and d.name not in prefsseed.NOT_A_PERSON
                 and d.joinpath(*prefsseed.APPDATA_TAIL).is_dir()]
        if len(found) != 1:
            return []
        src, source = found[0], found[0].name
    src.rename(users / target)
    return [f"Your Windows profile is now C:\\users\\{target} (it was {source})"]


def _replace_link(src: Path, dst: Path) -> None:
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    elif dst.is_dir():
        shutil.rmtree(dst)
    dst.symlink_to(os.readlink(src))


def _carry_fonts(old: Path, new: Path) -> list[str]:
    """Font files the new prefix lacks, and their registrations."""
    notes = []
    src = old / "drive_c" / "windows" / "Fonts"
    dst = new / "drive_c" / "windows" / "Fonts"
    added = 0
    try:
        for f in sorted(src.iterdir()):
            if f.is_file() and not f.is_symlink() and not (dst / f.name).exists():
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst / f.name)
                added += 1
    except OSError:
        pass
    if added:
        notes.append(f"{added} font file(s) carried over")
    notes += regfile.add_font_registrations(new, regfile.font_registrations(old))
    return notes


def _replace_program(old: Path, new: Path, image: Image) -> list[str]:
    """Put the installed Affinity into the new prefix in place of the image's,
    then lay the image's own additions (plugin loader, WineFix, vkd3d-proton)
    over it."""
    source = probe.affinity_dir(old)
    target = probe.affinity_dir(new)
    image_app = probe.affinity_dir(image.prefix_dir)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=True)
    laid = 0
    for rel in image.overlay:
        s = image_app / rel
        t = target / rel
        if s.is_dir() and not s.is_symlink():
            shutil.copytree(s, t, symlinks=True, dirs_exist_ok=True)
        elif s.exists():
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, t, follow_symlinks=False)
        else:
            continue
        laid += 1
    return [f"Kept Affinity {probe.affinity_version(new) or ''}".strip()
            + (f", with {laid} of the AppImage's own files laid over it" if laid else "")]


EXTRA_ROOTS = ("Program Files", "Program Files (x86)", "ProgramData")


def extras(old, image: Image) -> list[str]:
    """What the old install has that an upgrade does not carry and the image
    does not provide: a program somebody installed into the prefix, say.

    Named rather than guessed at. They stay in P.previous, and the message
    says so. Looked for one level into Program Files and ProgramData, and at
    the top of drive_c -- where installs go -- and not in windows, which Wine
    rewrites, or users, which is carried whole."""
    old = Path(old)
    out = []
    drive = old / "drive_c"
    ref = image.prefix_dir / "drive_c"
    try:
        tops = sorted(drive.iterdir())
    except OSError:
        return out
    for top in tops:
        if top.name in ("windows", "users"):
            continue
        if top.name in EXTRA_ROOTS and top.is_dir():
            for child in sorted(top.iterdir()):
                if child.name == "Affinity" and top.name == "Program Files":
                    continue
                if child.name == "Common Files" and child.is_dir():
                    for sub in sorted(child.iterdir()):
                        if not (ref / top.name / child.name / sub.name).exists():
                            out.append(f"{top.name}/{child.name}/{sub.name}")
                    continue
                if not (ref / top.name / child.name).exists():
                    out.append(f"{top.name}/{child.name}")
        elif not (ref / top.name).exists():
            out.append(top.name)
    return out


# ── installing ───────────────────────────────────────────────────────────────


@dataclass
class InstallPlan:
    image: Image
    target: Path
    kind: str
    need: int
    free: int

    def describe(self) -> list[str]:
        return [
            f"Install Affinity {self.image.affinity} (AppImage {self.image.version}, "
            f"Wine {self.image.wine}) into {self.target}.",
            ("Wine is copied into the prefix, so it runs without the AppImage."
             if self.kind == UNPACKED else
             "Wine stays in the AppImage, which starts it."),
            f"Needs about {probe.human_size(self.need)}; "
            f"{probe.human_size(self.free)} is free there.",
        ]


def plan_install(image: Image, target, kind=LINKED) -> InstallPlan:
    target = Path(target).expanduser()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise AppImageError(f"{target} already exists and is not empty.")
    if sibling_role(target):
        raise AppImageError(f"{target.name} ends in a name kept for an install's "
                            "own folders. Choose another name.")
    need = maintenance._du(image.prefix_dir)
    if kind == UNPACKED:
        need += maintenance._du(image.wine_dir)
    free = _free(target.parent)
    if free < need * 1.05:
        raise maintenance.NotEnoughSpace(
            f"Installing needs about {probe.human_size(need)}; "
            f"{target.parent} has {probe.human_size(free)} free.")
    return InstallPlan(image, target, kind, need, free)


def install(plan: InstallPlan, progress=None) -> Install:
    """Build the install under P.installing and rename it into place, so an
    interrupted install never looks like a finished one."""
    target = plan.target
    staging = _sibling(target, INSTALLING)
    _remove(staging)
    try:
        _build_prefix(plan.image, staging, plan.kind, progress)
        write_marker(staging, plan.image, plan.kind, event=f"installed {plan.image.version}",
                     image_path=plan.image.path)
        if target.is_dir() and not any(target.iterdir()):
            target.rmdir()
        staging.rename(target)
    except BaseException:
        try:
            _remove(staging)
        except OSError:
            pass
        raise
    if progress:
        progress(100, "Installed")
    return inspect(target)


# ── upgrading ────────────────────────────────────────────────────────────────


@dataclass
class UpgradePlan:
    image: Image
    install: Install
    keep_previous: bool
    program: str
    kind: str
    need: int
    free: int
    previous_size: int
    replaces_previous: Install | None
    extras: list
    notes: list

    @property
    def prefix(self) -> Path:
        return self.install.path

    def describe(self) -> list[str]:
        i, img = self.install, self.image
        lines = [f"Upgrade {i.path} to AppImage {img.version}: Affinity {img.affinity}, "
                 f"Wine {img.wine}.",
                 f"Now: Affinity {i.affinity or 'not installed'}, from AppImage "
                 f"{i.image_version}."]
        lines.append("Your settings, presets, fonts and sign-in are saved first and "
                     "carried into the new install.")
        if self.program == PROGRAM_KEEP:
            lines.append(f"Your Affinity ({i.affinity}) is kept -- it is newer than the "
                         f"AppImage's ({img.affinity}) -- and everything around it is updated.")
        if self.keep_previous:
            lines.append(f"The current install is kept as {previous_of(i.path).name} "
                         f"({probe.human_size(self.previous_size)}) so you can go back.")
        else:
            lines.append("The current install is deleted once the upgrade has finished.")
        if self.replaces_previous:
            p = self.replaces_previous
            lines.append(f"That replaces the install kept from the last upgrade "
                         f"(Affinity {p.affinity or '?'}), which is deleted.")
        if self.extras:
            lines.append("Not carried over, and kept in the old install: "
                         + ", ".join(self.extras[:8])
                         + ("…" if len(self.extras) > 8 else ""))
        lines += self.notes
        return lines


def _users_size(prefix: Path) -> int:
    """What _copy_users will write: the profiles minus the hard-linked models."""
    total = 0
    for root, dirs, files in os.walk(prefix / "drive_c" / "users", followlinks=False):
        if "modelcache" in Path(root).parts:
            dirs[:] = []
            continue
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                try:
                    total += os.lstat(fp).st_size
                except OSError:
                    pass
    return total


def plan_upgrade(image: Image, prefix, *, keep_previous=True, program=None,
                 kind=None) -> UpgradePlan:
    inst = inspect(prefix)
    if inst is None:
        raise AppImageError(f"{prefix} is not an AppImage install.")
    if interrupted(inst.path):
        raise AppImageError(f"{interrupted(inst.path)} Finish or undo it first.")
    kind = kind or inst.kind
    notes = []
    installed, offered = inst.affinity, image.affinity
    newer_installed = installed and offered and version_key(installed) > version_key(offered)
    if program is None:
        program = PROGRAM_KEEP if newer_installed else PROGRAM_IMAGE
    if program == PROGRAM_KEEP and not probe.has_affinity(inst.path):
        program = PROGRAM_IMAGE
    if program == PROGRAM_IMAGE and newer_installed:
        notes.append(f"This goes back from Affinity {installed} to {offered}. Settings "
                     "saved by a newer Affinity may not all load in an older one.")
    need = maintenance._du(image.prefix_dir) + _users_size(inst.path)
    if kind == UNPACKED:
        need += maintenance._du(image.wine_dir)
    if program == PROGRAM_KEEP:
        need += maintenance._du(probe.affinity_dir(inst.path))
    free = _free(inst.path.parent)
    if free < need * 1.05:
        raise maintenance.NotEnoughSpace(
            f"The upgrade needs about {probe.human_size(need)}; "
            f"{inst.path.parent} has {probe.human_size(free)} free.")
    previous_size = maintenance._du(inst.path) if keep_previous else 0
    return UpgradePlan(image, inst, keep_previous, program, kind, need, free,
                       previous_size, inst.previous, extras(inst.path, image), notes)


def upgrade(plan: UpgradePlan, progress=None) -> list[str]:
    """Do it. Each phase is written to the journal before it starts."""
    P = plan.prefix
    old = plan.install
    require_quiet(P)
    if old.previous:
        require_quiet(old.previous.path)

    def step(pct, text):
        if progress:
            progress(pct, text)

    _journal(P, op="upgrade", started=_now(), phase="start",
             to=plan.image.version, keep_previous=plan.keep_previous)
    notes = []

    _journal(P, phase="snapshot")
    step(2, "Saving your settings")
    try:
        snap = take_snapshot(P, label=f"before-upgrade-{old.affinity or 'unknown'}", auto=True)
        notes.append(f"Settings saved in {snap.path}")
    except snapshots.NoSettings:
        notes.append("No saved settings to carry over yet: Affinity writes them when it closes")

    _journal(P, phase="build")
    new = _sibling(P, UPGRADING)
    _remove(new)
    _build_prefix(plan.image, new, plan.kind,
                  progress=lambda p, t: step(5 + p * 0.75, t))

    _journal(P, phase="carry")
    step(82, "Carrying your settings, fonts and drive letters over")
    users = P / "drive_c" / "users"
    if users.is_dir():
        _copy_users(users, new / "drive_c" / "users")
        notes += _adopt_profile(P, new)
    notes += _carry_fonts(P, new)
    letters = prefsseed.drive_letters(P)
    if letters and prefsseed.is_initialised(new):
        notes += prefsseed.import_drive_letters(new, letters)
    if plan.program == PROGRAM_KEEP:
        step(88, "Keeping your Affinity")
        notes += _replace_program(P, new, plan.image)
    write_marker(new, plan.image, plan.kind, base=old.marker,
                 event=f"upgraded from {old.image_version} to {plan.image.version}",
                 image_path=plan.image.path or old.image_path)

    _journal(P, phase="swap")
    step(94, "Switching to the new install")
    _swap_in(P)

    _journal(P, phase="cleanup")
    notes += _cleanup(P, plan.keep_previous)
    _journal_done(P)
    if plan.extras:
        notes.append("Not carried over (still in " + previous_of(P).name + "): "
                     + ", ".join(plan.extras))
    step(100, "Upgraded")
    return notes


def _swap_in(P: Path) -> None:
    """P.upgrading becomes P; P becomes P.previous; an older P.previous goes.

    Ordered so that each state in between can be recognised on disk, which is
    what resume() relies on: P.removing is deleted only in cleanup."""
    new, prev, gone = _sibling(P, UPGRADING), previous_of(P), _sibling(P, REMOVING)
    if not new.exists():
        return                              # already swapped
    if P.exists():
        if prev.exists():
            _remove(gone)
            prev.rename(gone)
        P.rename(prev)
    new.rename(P)


def _cleanup(P: Path, keep_previous: bool) -> list[str]:
    notes = []
    _remove(_sibling(P, REMOVING))
    if not keep_previous and previous_of(P).exists():
        _discard(previous_of(P))
        notes.append("The old install was deleted, as asked")
    pruned = prune_snapshots(P)
    if pruned:
        notes.append(f"{pruned} older automatic snapshot(s) deleted")
    return notes


# ── going back, and tidying ──────────────────────────────────────────────────


def switch_to_previous(prefix) -> list[str]:
    """Swap the install and P.previous.

    A swap, not a restore, so doing it twice gets back where you started --
    "go back" never destroys the newer install, it only puts it aside."""
    P = Path(prefix).expanduser()
    prev, mid = previous_of(P), _sibling(P, SWITCHING)
    if not probe.is_prefix(prev):
        raise AppImageError(f"There is no previous install beside {P}.")
    require_quiet(P)
    require_quiet(prev)
    _journal(P, op="switch", started=_now(), phase="swap")
    P.rename(mid)
    prev.rename(P)
    mid.rename(prev)
    _journal_done(P)
    now, then = inspect(P), inspect(prev)
    return [f"Now using the install with {_what(now)}; "
            f"the other ({_what(then)}) is kept as {prev.name}."]


def _what(inst) -> str:
    """An install as the switch message names it: Affinity, and the AppImage
    and Wine where known, so two installs of the same Affinity can be told
    apart."""
    if inst is None:
        return "Affinity ?"
    text = f"Affinity {inst.affinity or '?'}"
    extra = [f"AppImage {inst.image_version}" if inst.image_version else "",
             f"Wine {inst.image.get('wine')}" if inst.image.get("wine") else ""]
    extra = [e for e in extra if e]
    return text + (f" ({', '.join(extra)})" if extra else "")


def remove_previous(prefix) -> list[str]:
    P = Path(prefix).expanduser()
    prev = previous_of(P)
    if not prev.exists():
        return ["There is no previous install."]
    require_quiet(prev)
    size = maintenance._du(prev)
    _discard(prev)
    return [f"Deleted {prev.name}, freeing {probe.human_size(size)}."]


def resume(prefix) -> list[str]:
    """Finish what an interrupted upgrade or switch can finish, and undo the
    rest. Called before anything else touches an install, by the AppImage on
    every start and by the manager when it lists one.

    Before the swap nothing about P has changed, so the half-built copy is
    simply thrown away. From the swap on, the new install is complete, so the
    swap is finished and the cleanup run."""
    P = Path(prefix).expanduser()
    j = read_journal(P)
    notes = []
    new, mid, prev = _sibling(P, UPGRADING), _sibling(P, SWITCHING), previous_of(P)
    op = (j or {}).get("op")
    phase = (j or {}).get("phase")

    if op == "switch" or mid.exists():
        if mid.exists():
            if not P.exists():
                prev.rename(P)
            mid.rename(prev)
            notes.append("Finished switching to the previous install")
        _journal_done(P)
        return notes

    if op == "upgrade":
        if phase in ("swap", "cleanup"):
            _swap_in(P)
            notes += _cleanup(P, bool(j.get("keep_previous", True)))
            notes.append("Finished an upgrade that had been interrupted")
        else:
            _remove(new)
            notes.append("An upgrade was interrupted before anything had changed; "
                         "the install is as it was, and the half-built copy is gone")
        _journal_done(P)
        return notes

    if new.exists():
        _remove(new)
        notes.append(f"Removed a half-built {new.name}")
    if _sibling(P, INSTALLING).exists():
        _remove(_sibling(P, INSTALLING))
        notes.append("Removed a half-finished install")
    _remove(_sibling(P, REMOVING))
    return notes


# ── settings snapshots ───────────────────────────────────────────────────────


def _name_for(prefix) -> str:
    return Path(prefix).name.lstrip(".") or "AppImage"


def take_snapshot(prefix, label="", auto=False) -> snapshots.Snapshot:
    return snapshots.take(_name_for(prefix), prefix, label,
                          root=backups_of(prefix), auto=auto)


def list_snapshots(prefix) -> list[snapshots.Snapshot]:
    return snapshots.listing(root=backups_of(prefix))


def prune_snapshots(prefix, keep=KEEP_AUTO_SNAPSHOTS) -> int:
    """Delete automatic snapshots beyond the newest `keep`. Ones somebody took
    by hand are never pruned."""
    autos = [s for s in list_snapshots(prefix) if s.auto]
    gone = 0
    for snap in autos[keep:]:
        try:
            snapshots.remove(snap)
            gone += 1
        except (OSError, snapshots.NotASnapshot):
            pass
    return gone


def restore_snapshot(prefix, name: str) -> list[str]:
    snap = next((s for s in list_snapshots(prefix) if s.name == name), None)
    if snap is None:
        raise AppImageError(f"No snapshot named {name} in {backups_of(prefix)}.")
    require_quiet(prefix)
    plan = snapshots.plan_restore(snap, _name_for(prefix), prefix)
    return snapshots.restore(plan)


# ── space ────────────────────────────────────────────────────────────────────


def space(prefix) -> dict:
    """Bytes used by the install and by what is kept beside it."""
    P = Path(prefix).expanduser()
    return {
        "install": maintenance._du(P) if P.exists() else 0,
        "previous": maintenance._du(previous_of(P)) if previous_of(P).exists() else 0,
        "backups": maintenance._du(backups_of(P)) if backups_of(P).exists() else 0,
    }
