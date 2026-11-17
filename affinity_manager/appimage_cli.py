"""The AppImage's own commands, and the manager's way into an AppImage.

The AppImage runs this on every start, before it starts Affinity:

    prepare     finish or undo an interrupted upgrade, install on first run,
                and offer an upgrade when this image is newer than the install.
                Exit 0 means start Affinity; anything else means do not.

And for the user, or for the manager, through `Affinity.AppImage --manage
<command>`:

    status            what the install and this image are (JSON)
    install           install from this image (--unpacked copies Wine in)
    upgrade           upgrade the install to this image
    switch            swap the install and the one kept from the last upgrade
    remove-previous   delete the one kept from the last upgrade
    snapshot          save the settings now
    snapshots         list the saved settings
    restore NAME      put saved settings back
    resume            finish or undo an interrupted upgrade

Commands that need the image's contents (install, upgrade, prepare, status)
are run from inside the mounted AppImage, which passes its own root. The rest
only touch the install and run anywhere.

With --json the result is one line, `RESULT {...}`, and progress is printed as
`PROGRESS <percent> <text>` lines: what the manager reads.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "affinity_manager"

from affinity_manager import appimage, maintenance, probe, snapshots  # noqa: E402

TITLE = "Affinity"
EXIT_LAUNCH = 0
EXIT_STOP = 3
EXIT_ERROR = 1


# ── talking to the user ──────────────────────────────────────────────────────


class Ui:
    """zenity if it is there, else kdialog, else the terminal, else nobody.

    With nobody to ask, every question takes its default -- which is chosen
    to be the safe answer each time."""

    def __init__(self, mode=None):
        if mode is None:
            if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
                mode = "zenity" if shutil.which("zenity") else \
                    "kdialog" if shutil.which("kdialog") else None
            if mode is None:
                mode = "tty" if sys.stdin.isatty() else "none"
        self.mode = mode

    def choose(self, text, options, default=0, title=TITLE):
        """Index of the option chosen; None for cancel. Up to three options;
        the last is the cancel-like one."""
        if self.mode == "zenity":
            args = ["zenity", "--question", "--no-wrap", f"--title={title}",
                    f"--text={text}", f"--ok-label={options[0]}",
                    f"--cancel-label={options[-1]}"]
            for extra in options[1:-1]:
                args.append(f"--extra-button={extra}")
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode == 0:
                return 0
            out = r.stdout.strip()
            for i, opt in enumerate(options[1:-1], 1):
                if out == opt:
                    return i
            return len(options) - 1
        if self.mode == "kdialog":
            if len(options) == 3:
                r = subprocess.run(["kdialog", "--title", title, "--yesnocancel", text,
                                    "--yes-label", options[0], "--no-label", options[1],
                                    "--cancel-label", options[2]])
                return {0: 0, 1: 1}.get(r.returncode, 2)
            r = subprocess.run(["kdialog", "--title", title, "--yesno", text,
                                "--yes-label", options[0], "--no-label", options[-1]])
            return 0 if r.returncode == 0 else len(options) - 1
        if self.mode == "tty":
            print(text, file=sys.stderr)
            for i, opt in enumerate(options, 1):
                print(f"  {i}) {opt}" + ("  [default]" if i - 1 == default else ""),
                      file=sys.stderr)
            try:
                answer = input("> ").strip()
            except EOFError:
                return default
            if not answer:
                return default
            try:
                n = int(answer) - 1
                return n if 0 <= n < len(options) else default
            except ValueError:
                return default
        return default

    def info(self, text, title=TITLE, error=False):
        if self.mode == "zenity":
            subprocess.run(["zenity", "--error" if error else "--info", "--no-wrap",
                            f"--title={title}", f"--text={text}"], capture_output=True)
        elif self.mode == "kdialog":
            subprocess.run(["kdialog", "--title", title,
                            "--error" if error else "--msgbox", text])
        else:
            print(text, file=sys.stderr)

    def progress(self, title=TITLE):
        return _Progress(self.mode, title)


class _Progress:
    def __init__(self, mode, title):
        self.proc = None
        if mode == "zenity":
            try:
                self.proc = subprocess.Popen(
                    ["zenity", "--progress", "--no-cancel", "--auto-close",
                     f"--title={title}", "--text=Starting", "--percentage=0",
                     "--width=460"],
                    stdin=subprocess.PIPE, text=True)
            except OSError:
                self.proc = None

    def __call__(self, pct, text):
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(f"#{text}\n{min(int(pct), 99)}\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        else:
            print(f"[{int(pct):3d}%] {text}", file=sys.stderr)

    def close(self):
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write("100\n")
                self.proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            self.proc.wait()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _json_progress(pct, text):
    print(f"PROGRESS {int(pct)} {text}", flush=True)


def _markup(text: str) -> str:
    """zenity and kdialog read Pango markup; a path with & in it must not."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── prepare: what the AppImage runs on every start ───────────────────────────


def _image(args) -> appimage.Image:
    root = args.image_root or os.environ.get("APPDIR")
    if not root:
        raise appimage.NotAnImage("No image to work from: run this from the AppImage.")
    return appimage.Image.load(root, args.image_path or os.environ.get("APPIMAGE"))


def prepare(args, ui: Ui) -> int:
    image = _image(args)
    P = Path(args.prefix).expanduser()

    try:
        notes = appimage.resume(P)
    except (OSError, appimage.AppImageError) as exc:
        ui.info(_markup(f"An interrupted upgrade of {P} could not be finished:\n\n{exc}\n\n"
                        f"Nothing has been deleted. The files are in {P.parent}."), error=True)
        return EXIT_STOP
    if notes:
        ui.info(_markup("\n".join(notes)))

    inst = appimage.inspect(P)
    if inst is None:
        if P.exists() and (not P.is_dir() or any(P.iterdir())):
            ui.info(_markup(f"{P} exists but was not made by an Affinity AppImage, so "
                            "it is left alone.\n\nSet AFFINITY_APPIMAGE_PREFIX to use "
                            "another folder."), error=True)
            return EXIT_STOP
        return _first_install(image, P, ui)

    appimage.remember_image(P, image.path)
    relation = appimage.compare(image, inst)
    if relation == "older" and not args.quiet_older:
        pick = ui.choose(_markup(
            f"This AppImage ({image.version}, built {image.built[:10]}) is older than "
            f"the one your install came from ({inst.image_version}).\n\n"
            "Starting it runs your install on its older Wine."),
            ["Start anyway", "Cancel"], default=1)
        return EXIT_LAUNCH if pick == 0 else EXIT_STOP
    if not appimage.should_offer(image, inst):
        return EXIT_LAUNCH
    return _offer_upgrade(image, inst, ui)


def _first_install(image, P, ui) -> int:
    try:
        plan = appimage.plan_install(image, P, appimage.LINKED)
    except (appimage.AppImageError, maintenance.NotEnoughSpace) as exc:
        ui.info(_markup(str(exc)), error=True)
        return EXIT_STOP
    with ui.progress("Setting up Affinity") as bar:
        try:
            appimage.install(plan, progress=bar)
        except Exception as exc:  # noqa: BLE001 - shown, and nothing half-done remains
            bar.close()
            ui.info(_markup(f"Setting up {P} failed:\n\n{exc}"), error=True)
            return EXIT_STOP
    return EXIT_LAUNCH


def _offer_upgrade(image, inst, ui) -> int:
    try:
        appimage.require_quiet(inst.path)
    except appimage.Busy:
        return EXIT_LAUNCH          # the running one is handed the document
    try:
        plan = appimage.plan_upgrade(image, inst.path, keep_previous=True)
    except maintenance.NotEnoughSpace as exc:
        ui.info(_markup(f"{exc}\n\nAffinity starts as it is. Free some space and "
                        "start the AppImage again to upgrade."))
        return EXIT_LAUNCH
    except appimage.AppImageError as exc:
        ui.info(_markup(f"{exc}\n\nAffinity starts as it is."))
        return EXIT_LAUNCH

    lines = [f"<b>This AppImage has Affinity {image.affinity} and Wine {image.wine}.</b>",
             f"Your install has Affinity {inst.affinity or '?'}, from AppImage "
             f"{inst.image_version}.", "",
             "Your settings, presets, fonts and sign-in are saved first, and carried "
             "into the upgraded install."]
    if plan.program == appimage.PROGRAM_KEEP:
        lines.append(f"Your Affinity is newer than this AppImage's, so it is kept and "
                     "everything around it is updated.")
    lines += ["", f"<b>Keep the old install</b> so you can go back to it: "
              f"{probe.human_size(plan.previous_size)} on disk."]
    if plan.replaces_previous:
        lines.append("It replaces the install kept from the last upgrade.")
    if plan.extras:
        lines.append("Not carried over, and kept only in the old install: "
                     + ", ".join(plan.extras[:5]) + ("…" if len(plan.extras) > 5 else ""))
    for note in plan.notes:
        lines.append(note)
    text = "\n".join(_markup(l) if not l.startswith("<b>") else
                     "<b>" + _markup(l[3:-4]) + "</b>" for l in lines)
    pick = ui.choose(text, ["Upgrade and keep the old install",
                            "Upgrade, don't keep it", "Not now"], default=0)
    if pick == 2 or pick is None:
        appimage.decline(inst.path, image)
        return EXIT_LAUNCH
    if pick == 1:
        plan = appimage.plan_upgrade(image, inst.path, keep_previous=False,
                                     program=plan.program)
    with ui.progress("Upgrading Affinity") as bar:
        try:
            notes = appimage.upgrade(plan, progress=bar)
        except Exception as exc:  # noqa: BLE001
            bar.close()
            try:
                undo = appimage.resume(inst.path)
            except Exception as again:  # noqa: BLE001
                undo = [f"Undoing it failed too: {again}"]
            ui.info(_markup(f"The upgrade failed:\n\n{exc}\n\n" + "\n".join(undo)),
                    error=True)
            return EXIT_STOP if not probe.has_affinity(inst.path) else EXIT_LAUNCH
    ui.info(_markup("Upgraded.\n\n" + "\n".join(notes)))
    return EXIT_LAUNCH


# ── the rest ─────────────────────────────────────────────────────────────────


def _result(args, data, text_lines=()) -> int:
    if args.json:
        print("RESULT " + json.dumps(data), flush=True)
    else:
        for line in text_lines or ([data] if isinstance(data, str) else
                                   [json.dumps(data, indent=2)]):
            print(line)
    return 0


def _snap_dict(s: snapshots.Snapshot) -> dict:
    return {"name": s.name, "when": s.when, "label": s.label, "auto": s.auto,
            "bytes": s.bytes, "parts": s.parts, "version": s.version}


def run(args) -> int:
    ui = Ui("none" if args.json else None)
    P = Path(args.prefix).expanduser()
    progress = _json_progress if args.json else (
        lambda pct, text: print(f"[{int(pct):3d}%] {text}", file=sys.stderr))
    cmd = args.command

    if cmd == "prepare":
        return prepare(args, Ui())
    if cmd == "status":
        inst = appimage.inspect(P)
        data = {"prefix": str(P), "install": inst.describe() if inst else None}
        try:
            image = _image(args)
            data["image"] = image.describe()
            if inst:
                data["relation"] = appimage.compare(image, inst)
        except appimage.NotAnImage:
            data["image"] = None
        data["space"] = appimage.space(P)
        return _result(args, data)
    if cmd == "install":
        plan = appimage.plan_install(_image(args), P,
                                     appimage.UNPACKED if args.unpacked else appimage.LINKED)
        inst = appimage.install(plan, progress=progress)
        return _result(args, inst.describe(), plan.describe())
    if cmd == "upgrade":
        appimage.resume(P)
        plan = appimage.plan_upgrade(
            _image(args), P, keep_previous=not args.discard_previous,
            program=args.program,
            kind=appimage.UNPACKED if args.unpacked else None)
        if not args.json:
            print("\n".join(plan.describe()), file=sys.stderr)
        notes = appimage.upgrade(plan, progress=progress)
        return _result(args, {"notes": notes}, notes)
    if cmd == "switch":
        notes = appimage.switch_to_previous(P)
        return _result(args, {"notes": notes}, notes)
    if cmd == "remove-previous":
        notes = appimage.remove_previous(P)
        return _result(args, {"notes": notes}, notes)
    if cmd == "resume":
        notes = appimage.resume(P) or ["Nothing was interrupted."]
        return _result(args, {"notes": notes}, notes)
    if cmd == "snapshot":
        snap = appimage.take_snapshot(P, args.label or "")
        return _result(args, _snap_dict(snap), [f"Saved {snap.path}"])
    if cmd == "snapshots":
        snaps = appimage.list_snapshots(P)
        return _result(args, [_snap_dict(s) for s in snaps],
                       [f"{s.name}  {probe.human_size(s.bytes)}"
                        + ("  (automatic)" if s.auto else "") for s in snaps]
                       or ["No saved settings."])
    if cmd == "restore":
        notes = appimage.restore_snapshot(P, args.name)
        return _result(args, {"notes": notes}, notes)
    raise SystemExit(f"unknown command {cmd}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="Affinity.AppImage --manage",
                                description="Manage an Affinity AppImage install.")
    p.add_argument("--prefix", default=str(appimage.prefix_from_env()),
                   help="the install (default: $AFFINITY_APPIMAGE_PREFIX or "
                        "~/.affinity-appimage-wineprefix)")
    p.add_argument("--image-root", help="the mounted image (default: $APPDIR)")
    p.add_argument("--image-path", help="the .AppImage file (default: $APPIMAGE)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--quiet-older", action="store_true")
    sub.add_parser("status")
    inst = sub.add_parser("install")
    inst.add_argument("--unpacked", action="store_true",
                      help="copy Wine into the prefix, so it runs without the AppImage")
    up = sub.add_parser("upgrade")
    up.add_argument("--discard-previous", action="store_true",
                    help="delete the old install once the upgrade has finished")
    up.add_argument("--program", choices=[appimage.PROGRAM_IMAGE, appimage.PROGRAM_KEEP],
                    help="the image's Affinity, or keep the installed one")
    up.add_argument("--unpacked", action="store_true")
    sub.add_parser("switch")
    sub.add_parser("remove-previous")
    sub.add_parser("resume")
    snap = sub.add_parser("snapshot")
    snap.add_argument("--label", default="")
    sub.add_parser("snapshots")
    res = sub.add_parser("restore")
    res.add_argument("name")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (appimage.AppImageError, maintenance.NotEnoughSpace,
            maintenance.CloneFailed, snapshots.NoSettings, OSError) as exc:
        if args.json:
            print("RESULT " + json.dumps({"error": str(exc)}), flush=True)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
