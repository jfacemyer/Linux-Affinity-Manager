# The Affinity AppImage

One file that installs and runs Affinity: a ready-made prefix, the Wine that
runs it, and the code that installs, upgrades and goes back.

## For users

Start it. The first time, it copies its prefix to
`~/.affinity-appimage-wineprefix` (a few minutes, once) and starts Affinity.

When a **newer** AppImage is started, it offers to upgrade:

- Your settings, presets, fonts and sign-in are saved first (always), in
  `~/.affinity-appimage-wineprefix.backups`, and carried into the upgraded
  install.
- The old install can be kept as `~/.affinity-appimage-wineprefix.previous`. It
  is renamed, not copied, so keeping it costs no time. Only one is kept.
- If your Affinity is newer than the AppImage's, yours is kept and everything
  around it is updated.
- "Not now" is remembered for that AppImage.

An upgrade that is cut short (power, a full disk) is undone or finished the next
time the AppImage starts. Before the switch the old install is untouched; after
it the new one is complete.

Other commands:

```sh
Affinity.AppImage --manage status            # what is installed, from which AppImage
Affinity.AppImage --manage switch            # go back to the previous install (and again to return)
Affinity.AppImage --manage remove-previous   # delete it
Affinity.AppImage --manage snapshot          # save your settings now
Affinity.AppImage --manage snapshots         # list them
Affinity.AppImage --manage restore <name>    # put one back
Affinity.AppImage --wine winecfg             # Wine's tools, against the install
Affinity.AppImage --update setup.exe         # install a newer Affinity into it
Affinity.AppImage --dpi                      # choose the interface scaling again
```

`AFFINITY_APPIMAGE_PREFIX=/some/folder` runs a different install, so several
can sit side by side.

**Getting the next version.** AppImageLauncher's *Update* and AppImageUpdate
know where releases are published and download only the parts of the file that
changed. Starting the new file then offers the upgrade as above. Downloading it
by hand from the releases page works the same way.

Installing, upgrading and going back need `python3` (3.10 or newer), which
desktop Linux has. Without it the AppImage still installs and starts Affinity.

## With the Affinity on Linux Manager

The manager does all of the above with buttons (**AppImage…**), lists AppImage
installs with the others, and backs them up, clones them and copies settings
between them and other prefixes. **Install from AppImage…** puts an AppImage's
Affinity into a prefix with a name, in the manager's folder or anywhere:
*self-contained* (the AppImage's Wine is copied in) or *linked* (the AppImage
runs it).

The two share one implementation: the AppImage carries the manager's
`affinity_manager` package in `usr/share/affinity-manager`.

## Building

```sh
AppImage/build-appimage.py \
    --prefix  ~/.AffinityLinuxManager/<a fresh install> \
    --wine    ElementalWarrior-wine-11.22.tar.xz --wine-version 11.22-r1 \
    --manager <Affinity on Linux Manager checkout> \
    --runtime runtime-x86_64 \
    --opencl  libOpenCL.so.1 \
    --version 3.3.3-1 \
    --work    /some/empty/folder
```

- **The prefix** is a fresh install made by the Affinity on Linux installer,
  started once. The build leaves out its Wine (carried in `usr/` instead), the
  Windows user profiles, the Windows Installer cache, the WebView2 installer's
  cached copies, drive letters other than C: and Z:, and font registrations
  that name files on the build machine. The source prefix is only read.
- **The runtime** is the static one from
  [AppImage/type2-runtime](https://github.com/AppImage/type2-runtime/releases):
  it needs no libfuse2 on the host, and it reads zstd, which the image uses.
- **The size** must stay under GitHub's 2 GiB per file. A build with WebView2
  and .NET is about 1.7 GiB.
- `-processors` defaults to three quarters of the cores, at `nice 10`.

The output is `Affinity-<version>-x86_64.AppImage`, its `.sha256`, its
`.zsync` (when `zsyncmake`, from the zsync package, is installed; or
`--zsyncmake PATH`), and the manifest as `.json`.

**Releasing.** Upload the AppImage **and its `.zsync`** to a release that is
not marked pre-release: the image's update information points at the `.zsync`
of the latest release of ryzendew/Linux-Affinity-Installer
(`--update-info` changes that; `--update-info ''` leaves it out). A release
without the `.zsync` is one that AppImageUpdate cannot see.
