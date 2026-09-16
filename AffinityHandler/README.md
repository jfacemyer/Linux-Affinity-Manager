# affinity-on-linux.exe

The file-manager handler. It is what makes double-clicking a `.af`, `.afphoto`,
`.afdesign` or `.afpub` file open that document in Affinity.

`Exec=` in `Affinity.desktop` points at this rather than at `Affinity.exe`, and
carries `%F`, so the file manager hands it the selected documents.

## Why it has to exist

Wine converts `argv[0]` to a DOS path but passes **arguments through verbatim**.
A file manager hands over `/home/you/art.afphoto`, Affinity receives that string
unchanged, and cannot open it. Something running inside the prefix has to do the
conversion, which is `kernel32!wine_get_dos_file_name`.

It does three other things that need to happen inside the prefix:

- **Serialises double-clicks** on a named mutex. Two quick double-clicks
  otherwise both decide "not running" and both cold start.
- **Detects a running instance.** Inside the prefix `Process.GetProcessesByName`
  sees only this prefix.
- **Watches for the startup stall.** It waits up to 45s for a titled window,
  then kills the attempt and retries, up to three times.

## Husks

Closing Affinity does not always end its processes. What is left has no window,
no document and a handful of threads, and it lingers -- but it still owns
Affinity's single-instance registration. A document handed to one is silently
discarded, and the forwarding process then starts an instance of its own
without the document: a blank window with no tab, from a double-click that
looks like it did nothing.

So the handler will not take a process list as proof that Affinity is running.
A window proves it, and so does a thread count of 40 or more -- a live instance
runs at 150+, and one still starting passes fifty within seconds. A low count
proves nothing, because a closing instance descends through every value on its
way down; one was caught at 17.

When there are processes but neither of those, it waits out the whole startup
window for a window to appear. Anything genuinely starting produces one -- the
hook exists for about thirty seconds before Affinity.exe does, and missing it
there is what cold starts a rival on a second double-click. If none appears,
what is left is husks, and they are cleared before the cold start so the
handoff cannot land on one.

The asymmetry is deliberate: mistaking a husk for a live instance costs an
unopened document, while mistaking a live instance for a husk kills the user's
session and whatever was unsaved in it. Nothing is declared dead early.

## Warm and cold

- **Warm** (an instance is up): hands the document straight to `Affinity.exe` and
  lets Affinity's own single-instance handoff forward it. This deliberately
  skips `AffinityHook.exe`: the hook exists to inject `AffinityPluginLoader.dll`
  into the process it starts, and on a warm handoff that process forwards the
  document and exits in about a second, so injecting into it achieves nothing.
- **Cold**: goes through `AffinityHook.exe` when it is installed, so plugins
  load, and falls back to `Affinity.exe` when it is not.

A cold start whose path contains a space goes two-stage — start the hook with no
document, wait for the window, then hand the document over as a warm handoff.
`AffinityPluginLoader` forwards its arguments with `string.Join(" ", args)`, so
passing such a path straight through would split it into fragments and produce
one "failed to open" dialog per fragment. Going two-stage behaves the same
against a patched plugin loader and an unpatched one.

8.3 short paths were tried first and rejected: they open the right file, but
Affinity keeps the short name as the document's identity, so the tab and Recent
Documents would read `BRAN~ECI.AF`.

## Raising the window

It does not raise the window, deliberately — Affinity brings itself forward when
it takes the handoff.

That needs a Wine fix. On a stock wineserver, `SetForegroundWindow` is refused
with `ERROR_ACCESS_DENIED` for **every** Wine process whenever the X input focus
is on a window that does not belong to Wine — including an application raising
its own window. Without that fix the document still opens, but the window stays
behind the file manager.

## Rebuilding

```sh
mcs -target:exe -langversion:5 -out:affinity-on-linux.exe Program.cs
```

Mono's `mcs` is the same toolchain `AffinityPluginLoader` is built with. The
committed binary is built from the `Program.cs` beside it.

## Temporary download sources — must be repointed before merging

Three downloads in `AffinityScripts/AffinityLinuxInstaller.py` point at a
personal Forgejo mirror rather than upstream. They have to be *downloads* at all
because the documented install pipes the installer straight into `python3`,
where `__file__` does not exist and there is no checkout to copy from; and they
have to point somewhere other than upstream because none of these files exist
upstream until this branch is merged.

Each site is marked `POC SOURCE`:

```sh
grep -n 'POC SOURCE' AffinityScripts/AffinityLinuxInstaller.py
```

| What | Points at now | Should point at |
|---|---|---|
| Wine 11.16 tarball | `forgejo.facemyer.net/facemyer/Affinity-Wine-Builder/releases/download/11.16/ElementalWarrior-wine-11.16.tar.xz` | `github.com/ryzendew/Affinity-Wine-Builder/releases/download/11.16/ElementalWarrior-wine-11.16.tar.xz` |
| `affinity-on-linux.exe` | `forgejo.facemyer.net/facemyer/AffinityOnLinux/raw/branch/feature/open-documents-from-file-manager/AffinityHandler/` | `raw.githubusercontent.com/ryzendew/AffinityOnLinux/main/AffinityHandler/` |
| MIME definitions | `forgejo.facemyer.net/facemyer/AffinityOnLinux/raw/branch/feature/open-documents-from-file-manager/mime/` | `raw.githubusercontent.com/ryzendew/AffinityOnLinux/main/mime/` |

The Wine one also depends on a release that does not exist upstream yet: 11.16
built with the Affinity patch set, from the matching branch of
`Affinity-Wine-Builder`. Publishing that release is a prerequisite for merging
this, not a follow-up.

The handler and MIME downloads are only reached when the installer was piped
into `python3`. Run from a checkout it copies the files from there, so a
reviewer testing from a clone exercises everything except the URLs themselves.

## Installing from this branch

The published one-liner installs `main`, which has none of this. To test the
branch, install from it instead:

```bash
curl -sSL https://forgejo.facemyer.net/facemyer/AffinityOnLinux/raw/branch/feature/open-documents-from-file-manager/AffinityScripts/AffinityLinuxInstaller.py | python3
```

Then in *Choose Wine Version* pick **Wine 11.16 (opens documents from the file
manager)**. The handler, the MIME definitions and the desktop entry are only
installed on 11.16 — on an older build the installer says so and skips them,
because those Wine versions cannot open a document handed to them and claiming
the file types would make double-clicking do nothing.

On an install that already exists, *File Manager Integration* in the installer
repairs just this part without reinstalling Affinity or Wine.

Or from a clone, which skips the downloads above:

```bash
git clone -b feature/open-documents-from-file-manager \
  https://forgejo.facemyer.net/facemyer/AffinityOnLinux.git
python3 AffinityOnLinux/AffinityScripts/AffinityLinuxInstaller.py
```
