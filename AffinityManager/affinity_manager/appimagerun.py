"""The manager's side of an AppImage: finding one, running it, asking it.

An install or an upgrade needs the image's contents, and those are only
reachable from inside the running AppImage, so the manager asks the image to
do it -- `Affinity.AppImage --manage --json ...` -- and reads the progress and
the result off its output. The image runs its own copy of appimage.py, which
knows its own layout. Everything that touches only the install (switching to
the previous one, snapshots, deleting the previous one) the manager does
itself.

AppImageLauncher, where it is installed, intercepts every AppImage start to
offer "integrating" it. APPIMAGELAUNCHER_DISABLE turns that off for the starts
made here.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import appimage, commands

QUIET_ENV = {"APPIMAGELAUNCHER_DISABLE": "1"}
SEARCH_DIRS = ("Applications", "AppImages", "Downloads", ".local/bin", "bin", "")


class ImageFailed(RuntimeError):
    pass


def candidates(home=None) -> list[Path]:
    """Affinity AppImages in the places people keep them, newest first."""
    home = Path(home) if home else Path.home()
    found = {}
    for sub in SEARCH_DIRS:
        d = home / sub if sub else home
        try:
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() == ".appimage" \
                        and "affinity" in f.name.lower():
                    found[str(f.resolve())] = f
        except OSError:
            continue
    out = list(found.values())
    out.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return out


def image_for(inst: appimage.Install) -> Path | None:
    """The AppImage that runs a linked install, if it is still where it was."""
    p = inst.image_path
    return p if p and p.is_file() else None


def _env(prefix=None) -> dict:
    env = dict(os.environ)
    env.update(QUIET_ENV)
    if prefix is not None:
        env[appimage.ENV_PREFIX] = str(Path(prefix).expanduser())
    return env


def manage(image, prefix, command, *args, progress=None, timeout=None) -> dict:
    """Run one `--manage` command in an AppImage; the RESULT it printed.

    `progress(percent, text)` is called for each PROGRESS line."""
    argv = [str(image), "--manage", "--json", "--prefix", str(Path(prefix).expanduser()),
            command, *map(str, args)]
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=_env(), start_new_session=True)
    except OSError as exc:
        raise ImageFailed(f"Could not start {image}: {exc}") from exc
    result = None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line.startswith("PROGRESS ") and progress:
            parts = line.split(" ", 2)
            try:
                progress(int(parts[1]), parts[2] if len(parts) > 2 else "")
            except ValueError:
                pass
        elif line.startswith("RESULT "):
            try:
                result = json.loads(line[len("RESULT "):])
            except ValueError:
                result = None
    err = proc.stderr.read() if proc.stderr else ""
    proc.wait(timeout=timeout)
    if result is None:
        tail = err.strip().splitlines()[-3:]
        hint = ""
        if "--manage" in err or "unrecognized" in err or proc.returncode == 127:
            hint = (" This AppImage may be too old: only AppImages built after "
                    "3.3.1 can install and upgrade from the manager.")
        raise ImageFailed(f"{Path(image).name} exited {proc.returncode} without a "
                          f"result.{hint}" + (f"\n\n{chr(10).join(tail)}" if tail else ""))
    if isinstance(result, dict) and result.get("error"):
        raise ImageFailed(result["error"])
    return result


def status(image, prefix) -> dict:
    return manage(image, prefix, "status", timeout=120)


def can_launch(inst: appimage.Install) -> str | None:
    """Why this linked install cannot be started from here, or None."""
    if inst.kind != appimage.LINKED:
        return None
    image = image_for(inst)
    if image is None:
        return ("The AppImage that runs it is not where it was"
                + (f" ({inst.image_path})" if inst.image_path else "")
                + ". Use AppImage… > Locate to point at it.")
    if inst.legacy and inst.path != appimage.default_prefix():
        return ("AppImages up to 3.3.1 only run ~/.affinity-appimage-wineprefix, "
                "so this copy cannot be started by its AppImage. Upgrade it from a "
                "newer AppImage first.")
    return None


def launch_command(prefix, documents=()) -> commands.Command:
    """How a linked install is started: the AppImage, told which prefix."""
    inst = appimage.inspect(prefix)
    if inst is None:
        raise FileNotFoundError(f"{prefix} is not an AppImage install.")
    why = can_launch(inst)
    if why:
        raise FileNotFoundError(why)
    env = dict(QUIET_ENV)
    if Path(prefix).expanduser() != appimage.default_prefix():
        env[appimage.ENV_PREFIX] = str(Path(prefix).expanduser())
    return commands.Command(
        group="Affinity (AppImage)", label="Affinity",
        detail=f"Starts {image_for(inst).name}, which checks for an upgrade and "
               "then starts Affinity in this install.",
        argv=[image_for(inst), *documents], env=env)


def takes_documents(prefix) -> bool:
    """AppImages after 3.3.1 hand a document to Affinity's own handler; the
    older ones passed a Linux path straight to Affinity.exe."""
    inst = appimage.inspect(prefix)
    return bool(inst and not inst.legacy and inst.built)


def tool_commands(prefix) -> list[commands.Command]:
    """Wine's tools, run with the AppImage's Wine against this install."""
    inst = appimage.inspect(prefix)
    if inst is None or inst.kind != appimage.LINKED or inst.legacy or not image_for(inst):
        return []
    base = launch_command(prefix)
    out = []
    for tool, label, detail, risky in (
            ("winecfg", "Wine configuration", "Drives, graphics, DLL overrides.", False),
            ("regedit", "Registry editor", "The DLL overrides and the DPI setting.", False),
            ("winefile", "Wine file manager", "Browse the prefix as Windows sees it.", False),
            ("wineserver -k", "Shut the prefix down",
             "Ends every process in this install, unsaved work included.", True)):
        out.append(commands.Command(
            group="Wine tools (the AppImage's Wine)", label=label, detail=detail,
            argv=[base.argv[0], "--wine", *tool.split()], env=base.env, risky=risky))
    return out
