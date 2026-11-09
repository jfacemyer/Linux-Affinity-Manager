#!/usr/bin/env python3
"""Build the Affinity AppImage from a prefix and a Wine build.

    build-appimage.py --prefix PREFIX --wine WINE --manager MANAGER \\
                      --runtime RUNTIME --version 3.3.2-1 --work DIR

  PREFIX    a prefix with Affinity installed by the Affinity on Linux installer
            (Affinity in Program Files/Affinity/Affinity, the plugin loader,
            WineFix and vkd3d-proton beside it). Read, never changed.
  WINE      the Wine build: a directory, or the .tar.xz the builder makes.
  MANAGER   the Affinity on Linux Manager's source (the folder holding
            affinity_manager/). Its install and upgrade code goes into the
            image, so the AppImage and the manager do these things one way.
  RUNTIME   the AppImage runtime: the static one from AppImage/type2-runtime,
            which needs no libfuse2 on the host and reads zstd.

What goes in, and what is left out of the prefix:

  out   the Wine build and its link (the image carries Wine in usr/), the
        Windows user profiles (each user's own, made on first start), the
        Windows Installer cache (a gigabyte, and the 2 GiB GitHub limit), the
        WebView2 installer's cached copies,
        temporary files, drive letters other than C: and Z:, the installer's
        Patch folder, and font registrations naming files on the build machine.
  in    everything else, as it is.

The result is <work>/out/Affinity-<version>-x86_64.AppImage, its .sha256, and
its .zsync when zsyncmake is installed (the zsync package). Nothing here needs
root.

Updates: the image carries update information (in the runtime's .upd_info
section) naming the .zsync of the latest GitHub release, so AppImageUpdate and
AppImageLauncher's "Update" fetch only the blocks that changed. Publish the
.zsync beside the AppImage in the same release, or those tools find nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GITHUB_LIMIT = 2 * 1024 ** 3

# Where AppImageUpdate looks for the next version: the .zsync asset of the
# latest (non-prerelease) release of this repository.
UPDATE_INFO = ("gh-releases-zsync|ryzendew|Linux-Affinity-Installer|latest|"
               "Affinity-*-x86_64.AppImage.zsync")

# Files in Affinity's folder that are not Serif's: laid over a kept newer
# Affinity on upgrade, so keeping it does not mean keeping old fixes. Not
# Serif.Affinity.dll, which the installer patches: that one belongs to one
# Affinity version.
OVERLAY = ("affinity-on-linux.exe", "AffinityHook.exe", "AffinityPluginLoader.dll",
           "AffinityBootstrap.dll", "0Harmony.dll", "apl", "d3d12.dll",
           "d3d12core.dll", "LICENSE", "README.md")

PREFIX_EXCLUDES = (
    "/ElementalWarriorWine", "/drive_c/users/", "/drive_c/windows/Installer/",
    "/drive_c/windows/temp/*", "/Patch/", "/*.log", "/.gpu_config",
    "/drive_c/windows/Fonts/*.tmp",
    # The WebView2 installer, cached twice by its updater (178 MB each). It
    # runs from EdgeWebView/Application; nothing reads these again.
    "/drive_c/Program Files (x86)/Microsoft/EdgeUpdate/Download/*",
    "/drive_c/Program Files (x86)/Microsoft/EdgeUpdate/Install/*",
)


def log(text):
    print(f"==> {text}", flush=True)


def run(argv, **kw):
    print("    $ " + " ".join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), check=True, **kw)


def pe_version(exe: Path) -> str:
    blob = exe.read_bytes()
    at = blob.find("ProductVersion".encode("utf-16-le"))
    if at < 0:
        return "unknown"
    text = blob[at + 28: at + 28 + 128].decode("utf-16-le", "ignore")
    m = re.search(r"\d+\.\d+\.\d+(\.\d+)?", text)
    return m.group(0) if m else "unknown"


def wine_builds_in(prefix: Path) -> list[str]:
    return [d.name for d in prefix.iterdir()
            if d.is_dir() and not d.is_symlink() and (d / "bin" / "wine").is_file()]


# ── the prefix ───────────────────────────────────────────────────────────────


def copy_prefix(src: Path, dst: Path) -> None:
    log(f"Copying the prefix from {src}")
    excludes = list(PREFIX_EXCLUDES) + [f"/{b}/" for b in wine_builds_in(src)]
    argv = ["rsync", "-a", "--info=stats1"]
    for e in excludes:
        argv += ["--exclude", e]
    # Drive letters: only Wine's own two travel.
    argv += ["--include", "/dosdevices/c:", "--include", "/dosdevices/z:",
             "--exclude", "/dosdevices/*"]
    run(argv + [f"{src}/", f"{dst}/"])
    (dst / "drive_c" / "users").mkdir(exist_ok=True)
    (dst / "drive_c" / "windows" / "temp").mkdir(exist_ok=True)
    for f in (dst / ".appimage-setup-complete", dst / ".affinity-appimage.json"):
        if f.exists():
            f.unlink()


FONTS = r"[Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts]"


def scrub_registry(prefix: Path) -> list[str]:
    """Font registrations that name a file on the build machine (Z:\\...)
    would name nothing on anybody else's. Their own fonts are registered on
    their machine."""
    notes = []
    for name in ("system.reg", "user.reg"):
        path = prefix / name
        if not path.is_file():
            continue
        lines = path.read_text(encoding="latin-1").splitlines(keepends=True)
        out, section, dropped = [], "", 0
        for line in lines:
            if line.startswith("["):
                section = line.split("]")[0] + "]"
            if section == FONTS and line.startswith('"') and '"Z:\\\\' in line:
                dropped += 1
                continue
            out.append(line)
        if dropped:
            path.write_text("".join(out), encoding="latin-1")
            notes.append(f"{name}: {dropped} host font registration(s) removed")
    return notes


# ── Wine ─────────────────────────────────────────────────────────────────────


def place_wine(wine: Path, usr: Path) -> str:
    """Wine into usr/, from a directory or the builder's tarball. Returns the
    build's directory name (ElementalWarrior-wine-11.21)."""
    log(f"Placing Wine from {wine}")
    usr.mkdir(parents=True)
    if wine.is_dir():
        run(["rsync", "-a", "--exclude", "/include/", f"{wine}/", f"{usr}/"])
        return wine.name
    with tarfile.open(wine) as tar:
        top = None
        members = []
        for m in tar.getmembers():
            parts = Path(m.name).parts
            if not parts:
                continue
            top = top or parts[0]
            if len(parts) > 1 and parts[1] == "include":
                continue
            m.name = str(Path(*parts[1:])) if len(parts) > 1 else "."
            members.append(m)
        tar.extractall(usr, members=members, filter="tar")
    return top or "wine"


# ── the AppDir ───────────────────────────────────────────────────────────────


def build_appdir(args, appdir: Path) -> dict:
    prefix_src = Path(args.prefix).expanduser()
    app = prefix_src / "drive_c" / "Program Files" / "Affinity" / "Affinity"
    if not (app / "Affinity.exe").is_file():
        sys.exit(f"{app}/Affinity.exe is missing: the prefix must be one the Affinity "
                 "on Linux installer made.")

    copy_prefix(prefix_src, appdir / "wineprefix")
    for note in scrub_registry(appdir / "wineprefix"):
        print("    " + note)
    build = place_wine(Path(args.wine).expanduser(), appdir / "usr")

    if args.opencl:
        lib = appdir / "usr" / "lib"
        shutil.copy2(Path(args.opencl).resolve(), lib / "libOpenCL.so.1")
        (lib / "libOpenCL.so").symlink_to("libOpenCL.so.1")

    log("Adding the install and upgrade code")
    share = appdir / "usr" / "share" / "affinity-manager"
    pkg = share / "affinity_manager"
    pkg.mkdir(parents=True)
    src_pkg = Path(args.manager).expanduser() / "affinity_manager"
    for f in sorted(src_pkg.glob("*.py")):
        shutil.copy2(f, pkg / f.name)
    (share / "appimage-cli.py").write_text(
        "#!/usr/bin/env python3\n"
        "# The AppImage's install and upgrade commands. python3 -I does not put\n"
        "# this folder on the path, so it is added here.\n"
        "import os, sys\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "from affinity_manager.appimage_cli import main\n"
        "sys.exit(main())\n")
    manager_rev = subprocess.run(["git", "-C", str(src_pkg.parent), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()

    log("AppRun, desktop entry, icon")
    shutil.copy2(HERE / "AppRun", appdir / "AppRun")
    os.chmod(appdir / "AppRun", 0o755)
    shutil.copy2(HERE / "affinity.svg", appdir / "affinity.svg")
    (appdir / ".DirIcon").symlink_to("affinity.svg")
    (appdir / "affinity.desktop").write_text(
        "[Desktop Entry]\nName=Affinity\nComment=Affinity, under Wine\n"
        "Icon=affinity\nExec=affinity %F\nTerminal=false\nType=Application\n"
        "Categories=Graphics;\nStartupNotify=true\nStartupWMClass=affinity.exe\n")

    affinity = pe_version(app / "Affinity.exe")
    manifest = {
        "format": 1,
        "image_version": args.version,
        "built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "affinity": affinity,
        "wine": args.wine_version or build,
        "wine_build": build,
        "prefix_dir": "wineprefix",
        "wine_dir": "usr",
        "overlay": [n for n in OVERLAY if (app / n).exists()],
        "manager": manager_rev,
    }
    (appdir / "affinity-appimage.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def elf_section(path: Path, name: str) -> tuple[int, int] | None:
    """(file offset, size) of a section in a 64-bit little-endian ELF, or None."""
    import struct
    with open(path, "rb") as f:
        head = f.read(64)
        if head[:4] != b"\x7fELF" or head[4] != 2 or head[5] != 1:
            return None
        shoff, = struct.unpack_from("<Q", head, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", head, 0x3A)
        f.seek(shoff)
        table = f.read(shentsize * shnum)
        sections = [struct.unpack_from("<IIQQQQ", table, i * shentsize) for i in range(shnum)]
        _, _, _, _, str_off, str_size = sections[shstrndx]
        f.seek(str_off)
        names = f.read(str_size)
    for name_at, _, _, _, offset, size in sections:
        end = names.find(b"\0", name_at)
        if names[name_at:end].decode() == name:
            return offset, size
    return None


def embed_update_info(image: Path, runtime: Path, info: str) -> None:
    """Write the update information into the image's copy of the runtime.

    The runtime is the start of the image, so the section is at the same
    offset in both."""
    where = elf_section(runtime, ".upd_info")
    if where is None:
        sys.exit(f"{runtime} has no .upd_info section: use the type2 runtime.")
    offset, size = where
    data = info.encode()
    if len(data) >= size:
        sys.exit(f"The update information is longer than the runtime's {size} bytes.")
    with open(image, "r+b") as f:
        f.seek(offset)
        f.write(data + b"\0" * (size - len(data)))


def make_zsync(args, image: Path) -> Path | None:
    tool = args.zsyncmake or shutil.which("zsyncmake")
    if not tool:
        print("    zsyncmake not found (the zsync package): no .zsync made, so "
              "AppImageUpdate will not find this version", file=sys.stderr)
        return None
    target = image.with_name(image.name + ".zsync")
    log(f"Writing {target.name}")
    # -u: the AppImage's URL relative to the .zsync, so both sit in one release.
    run(["nice", "-n", "10", tool, "-u", image.name, "-o", target, image])
    return target


def squash(args, appdir: Path, out: Path, manifest: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    fs = out / "image.squashfs"
    if fs.exists():
        fs.unlink()
    log("Compressing (zstd)")
    run(["nice", "-n", "10", "mksquashfs", appdir, fs, "-comp", "zstd",
         "-Xcompression-level", str(args.level), "-b", "1M", "-root-owned",
         "-noappend", "-no-xattrs", "-processors", str(args.processors), "-quiet"])
    image = out / f"Affinity-{args.version}-x86_64.AppImage"
    log(f"Writing {image.name}")
    with open(image, "wb") as dst:
        for part in (Path(args.runtime), fs):
            with open(part, "rb") as src:
                shutil.copyfileobj(src, dst, 16 * 1024 * 1024)
    os.chmod(image, 0o755)
    fs.unlink()
    if args.update_info:
        embed_update_info(image, Path(args.runtime), args.update_info)
        print(f"    update information: {args.update_info}")
    digest = hashlib.sha256()
    with open(image, "rb") as f:
        for block in iter(lambda: f.read(16 * 1024 * 1024), b""):
            digest.update(block)
    (out / (image.name + ".sha256")).write_text(f"{digest.hexdigest()}  {image.name}\n")
    size = image.stat().st_size
    print(f"    {size / 1024 ** 3:.2f} GiB, sha256 {digest.hexdigest()}")
    if size > GITHUB_LIMIT:
        print("    WARNING: over GitHub's 2 GiB limit for a release asset", file=sys.stderr)
    (out / (image.name + ".json")).write_text(json.dumps(manifest, indent=2) + "\n")
    if args.update_info:
        make_zsync(args, image)
    return image


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--prefix", required=True)
    p.add_argument("--wine", required=True)
    p.add_argument("--wine-version", help="what to call the Wine, e.g. 11.21-r6")
    p.add_argument("--manager", required=True)
    p.add_argument("--runtime", required=True)
    p.add_argument("--version", required=True, help="the image's version, e.g. 3.3.2-1")
    p.add_argument("--work", required=True, help="an empty or new folder to build in")
    p.add_argument("--opencl", help="an OpenCL ICD loader (libOpenCL.so.1) to include")
    p.add_argument("--level", type=int, default=19, help="zstd level (default 19)")
    p.add_argument("--update-info", default=UPDATE_INFO,
                   help="where AppImageUpdate looks for the next version "
                        f"(default: {UPDATE_INFO}); '' for none")
    p.add_argument("--zsyncmake", help="the zsyncmake to use (default: the one on PATH)")
    p.add_argument("--processors", type=int, default=max(1, (os.cpu_count() or 4) * 3 // 4))
    args = p.parse_args(argv)

    work = Path(args.work).expanduser()
    appdir = work / "AppDir"
    if appdir.exists():
        sys.exit(f"{appdir} exists: use a new --work folder.")
    appdir.mkdir(parents=True)
    manifest = build_appdir(args, appdir)
    image = squash(args, appdir, work / "out", manifest)
    log(f"Done: {image}")


if __name__ == "__main__":
    main()
