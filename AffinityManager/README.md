# Affinity on Linux Manager

Several Affinity prefixes, tracked.

[AffinityOnLinux](https://github.com/ryzendew/AffinityOnLinux) installs one
Affinity, into `~/.AffinityLinux`, and remembers one custom location. That is
the right shape for most people and the wrong shape the moment you need two: a
working install you cannot afford to break, and a new release to try.

This manages the set. **It does not install anything itself** — installing
Affinity under Wine is a long, distro-specific job that AffinityOnLinux already
does well, and a second implementation of it would drift. What this adds is the
part that was missing: *which prefix*, and *which build*.

## Running it

### Straight from the repository

The same way the installer is run — nothing to clone, always the latest:

```sh
curl -sSL https://forgejo.facemyer.net/facemyer/AffinityOnLinux/raw/branch/experimental/affinity-3.3/AffinityManager/run.py | python3
```

The installer can be piped into `python3` because it is one file. The manager
is a package, so what that pipes is `run.py`, a single file whose only job is
to fetch the rest: it downloads the branch as a tarball, unpacks it, and starts
the manager from there. It needs Python 3.10+ and PyQt6, and nothing else — no
`git`.

- **Every run fetches the branch again.** If the commit has not changed, the
  copy already unpacked is used and nothing is rewritten.
- **Offline**, it starts the newest copy it already has and says so.
- **Nothing is installed** — no launcher, nothing on your `PATH`, no package
  manager, no `sudo`. What it leaves is a cache, one folder per commit with the
  newest few kept: `~/.cache/AffinityOnLinux/manager/experimental-affinity-3.3/`.
  Deleting that folder is always safe; the next run downloads again.
- **Which build is running** is shown at the top of the manager's window, as
  the branch and commit it unpacked.
- If PyQt6 is missing it stops and prints the command for your distribution
  rather than running a package manager on your behalf. The installer offers to
  install it for you, so running the installer once first also works.

Arguments after `python3 -` are passed through to the manager.
`AFFINITY_MANAGER_REPO` and `AFFINITY_MANAGER_BRANCH` point it at another fork
or branch.

### From a clone

```sh
git clone -b experimental/affinity-3.3 https://forgejo.facemyer.net/facemyer/AffinityOnLinux.git
python3 AffinityOnLinux/AffinityManager/AffinityLinuxManager.py
```

### From this repository

`./AffinityLinuxManager.py`. It looks for the installer beside a sibling
`AffinityOnLinux` checkout; see *Requirements* below for the full search
order.

## What it does

- **Lists** every managed prefix with its real state — Affinity version read
  from `Affinity.exe`, the Wine build it is *actually running*, size on disk,
  whether it is up, and whether it is protected.
- **Creates** a prefix under a name you choose and opens **Setup** for it —
  the AffinityOnLinux installer, inside this window. Names default to
  `AffinityLinux YYYY-MM-DD 0N`, numbered in order and skipping anything
  already taken.
- **Finds** Wine prefixes already on this machine and takes them over,
  optionally moving them into the base directory and rewriting the launchers
  that named the old path. Prefixes with no Affinity in them are listed too:
  an interrupted install looks exactly like that, and hiding it meant the one
  prefix you needed help with was the one this said it could not see.
- **Hosts the installer** as a page of its own window rather than starting a
  second one, so the two agree about which prefix is being worked on and
  whether anything is already running.
- **Runs one operation at a time**, across every prefix. Not a preference:
  provisioning runs the distro package manager, and two prefixes installing at
  once collide there whatever this application believes. The prefix being
  worked on is named at the foot of the window, with how long it has been
  going and a way out if it never finishes.
- **Copies your settings** into a prefix — preferences, workspaces, shortcuts,
  `RecentFiles.xml` and **drive letters** — from another prefix, or from a
  folder you point it at. See *Copying settings*.
- **Backs up** a prefix, and the desktop files an install rewrites, to wherever
  you choose — and restores it. See *Three kinds of copy*.
- **Pins a build.** `downloads.affinity.studio` has no versioned URL, so an
  unpinned install always gets whatever the current release is. Point at a kept
  installer to reproduce an older one.
- **Lists every way a prefix can be run** — each Wine build against each of the
  three ways to start Affinity, plus winecfg, regedit, winedbg, winetricks and
  the rest. Copy the command, run it, or put it in your application menu.
- **Launches** a prefix, refusing to start a second instance in one already
  running — that kills the first, and takes unsaved work with it.
- **Removes** a prefix item by item — the directory, its menu entries, its
  document associations, its log — showing the whole list before anything
  goes, and never one you have marked protected.

## Layout

You pick a base directory. Everything lives under it:

```
<base>/
  Manager/                      this application's metadata and config
    prefixes.json               the registry
    settings.json               theme, backup location, installer path
    logs/                       one log per prefix
    snapshots/                  settings snapshots, per prefix
    backups.json                where backups are (they live where you put them)
    restores/                   what each restore replaced, by date
  AffinityLinux_2026-09-15_01/  a managed prefix
  My_Test_Build/                another
```

Prefix directories are the prefix's name with spaces as underscores.
Substituted rather than stripped, so `Affinity 33` and `Affinity33` cannot
collapse onto the same directory.

The base directory defaults to `~/.AffinityLinuxManager` and is changed under
**Settings** — not demanded on first run, because an application that opens with
a directory chooser is one that gets cancelled. It is the one thing that cannot
live inside itself, so it is remembered in
`~/.config/AffinityLinuxManager/base_dir`; everything else, theme included, is
in `<base>/Manager/settings.json`. `$AFFINITY_MANAGER_HOME` overrides the base,
which is what the tests use.

Adopted prefixes keep their own paths — the registry stores absolute paths, and
an install on another disk is a normal reason to be running several.

**The registry is not the source of truth about a prefix's contents.** Which
Affinity is installed, which Wine it uses, whether it is running — all of that
is read off the filesystem every time it is asked. A cached answer would be
wrong more often than right, and wrong in the direction that gets a second
instance launched.

## Three kinds of copy

All three "copy a prefix", and choosing the wrong one is found out at the worst
moment — usually after an upgrade, when what was kept turns out not to be able
to bring back what was lost. Each dialog opens with this table, its own row
marked:

| | What it is | For | Size |
|---|---|---|---|
| **Back up** | An exact copy of the whole prefix, plus the desktop files around it: the menu entry, what opens `.afphoto`/`.afdesign`/`.afpub`, Wine's file-type definitions, and launchers that name the prefix. Kept wherever you choose. Never launched. | Before an install or an upgrade. Restoring puts all of it back. | The prefix's full size |
| **Clone** | A second prefix, added to the list, that you launch and change. | Trying something without touching the original. It is live — using it changes it — so it is not something to fall back to. | Full size, in the base directory |
| **Snapshot** | Just the settings: preferences, workspaces, shortcuts, recent files. | Before changing settings. | A few MB, kept by the manager |

### Backups

**Where it goes matters as much as that it exists.** Every location the dialog
offers — the default, ones used before, or any folder you pick — shows its free
space and **whether it is on the same physical disk as the prefix**. Two
partitions of one drive are two filesystems, and a backup that is "somewhere
else" by filesystem is not somewhere else when that drive fails. Locations that
cannot work are refused, with the reason: exFAT and FAT store no symlinks and a
prefix is full of them; tmpfs reports success and is gone at the next reboot
(`/tmp` is one); a folder you cannot write to is named, with its owner. The
default location is set under **Settings**.

A backup is a folder: `prefix/` (the prefix, copied exactly — symlinks are not
repointed, because a backup goes back where it came from), `host/` (the desktop
files), and `backup.json`. It is built under a `.partial` name and renamed at
the end, so an interrupted one is listed as *did not finish* and can be deleted
— never offered as usable. **Verify** checks the copy still matches its record.
It refuses while Wine is running in the prefix: a copy taken under a running
Wine can capture a registry half-written.

**Restore** puts the prefix back, the desktop files, or both. Nothing is
deleted. The prefix that was there is renamed aside and added to the list, so it
can still be launched or removed; every desktop file replaced goes into a dated
folder under `Manager/restores/`, so a restore can itself be undone; files the
install *added* since the backup are moved aside too, or the old and new would
compete. Only the Affinity lines of `mimeapps.list` are restored — putting back
the whole file would silently undo every other default-app choice made since.

**Backups…** lists every backup the manager knows of, including ones whose
prefix has gone and ones on a disk that is not plugged in.

### Cloning

**Clone** copies a prefix to a new managed one. Two things make it worth having
as a button rather than a `cp -a`:

* symlinks are kept as symlinks. Dereferencing them would turn a symlinked Wine
  build into hundreds of megabytes of duplicate, and `dosdevices` into copies of
  whole filesystems.
* absolute symlinks that pointed *into the original* are repointed into the
  copy, so the clone cannot write back into the prefix it was meant to protect.

It offers to copy only the Wine build the prefix is set up to use, which is
usually the difference between 3 GB and 11 GB. The copy is registered, so it
appears in the list and can be launched, cleaned or deleted like any other.

**Clean** reclaims space: Wine builds the prefix is not using, archives the
installer downloaded and already unpacked, saved-aside copies, and Wine's
temporary files. Everything is listed with its size and chosen individually.

Two rules it will not break. A Wine build is never offered when the prefix is
running it or when `ElementalWarriorWine` points at it -- and that is re-checked
at the moment of removal, not just when the list was drawn, because the symlink
can move in between. And a build the prefix simply is not using is *offered*
but never ticked by default: a spare build is often kept on purpose.

Both refuse outright while Affinity is running in the prefix. A prefix copied
mid-write has a registry that does not match the files beside it.

## When a directory is already there

Creating a prefix whose directory exists is common — names repeat, an earlier
attempt failed halfway. You get three choices and nothing is ever deleted:

| Choice | What happens |
|---|---|
| Use it anyway | Installs into it. Existing contents are left in place. |
| Rename the existing one | Moves it to `<name>.old-YYYYMMDD-HHMMSS`, then installs fresh. |
| Choose a different name | Back to the name field. |

## Copying settings

**Copy settings…** brings preferences, workspaces, shortcuts, recent files and
drive letters into a prefix from another one. Use it **after Setup and before
Affinity's first launch** in the new prefix.

After Setup, because of drive letters. Recent files are Windows paths — most of
a typical list is on a mapped drive like `W:` — and a fresh prefix has only `C:`
and `Z:`. Wine creates those two only when it creates the drive list itself, so
adding `W:` to a prefix Wine has not set up yet would leave it with no `C:` at
all; the dialog greys drive letters out until Setup has run. Each letter is
shown with how many recent files depend on it, and a letter the prefix already
maps is never changed.

Only configuration is copied. The same folder also holds crash-recovery
autosaves (hundreds of MB on a prefix that has been through some crashes), the
WebView2 profile, temp files, logs and a pid file that exists only while
Affinity runs — none of which belongs in a clean prefix. What is left behind is
named in the dialog. What was in the destination is renamed aside with the
date, never deleted.

If Affinity is running in the source, the dialog says so: it rewrites its
preferences and recent files as it goes, so closing it first copies exactly
what it saves on exit.

## Removing

**Delete** shows everything the prefix put on this machine — the directory,
its menu entries, its document-type definitions, its icon, the document
associations it holds, its log, its snapshots — each with its size, and the
list is the confirmation. Snapshots are unticked by default: losing the backups
of a thing along with the thing is the wrong default. Anything without this
application's marker, such as AffinityOnLinux's own `Affinity.desktop`, is
named as left alone and never touched. It refuses while Affinity is running in
the prefix, checked again at the moment of removal.

**Protect** marks a prefix undeletable; unprotecting has its own confirmation,
because a mark you can toggle without noticing is worth nothing.

## Menu entries

Any command can be added to the application menu. Entries carry an
`X-AffinityManager-Prefix` key so this only ever edits or removes its own, never
one AffinityOnLinux or you installed. `Exec=` goes through `env` with everything
shlex-quoted, because a prefix path with a space in it otherwise produces a menu
entry that fails silently. The icon is referenced by name if one is already
installed and otherwise omitted — the Affinity artwork is Serif's and is not
bundled here.

## Living inside AffinityOnLinux

This is also carried in the AffinityOnLinux fork as `AffinityManager/`, beside
`AffinityScripts/`. Nothing about AffinityOnLinux changes by its being there:
the installer is still a single file you can run with `curl … | python3`, and
it neither imports nor knows about this. The dependency points one way only,
and this is the optional half.

**This repository is upstream of that copy.** It is carried there with
`git subtree`, squashed so the AffinityOnLinux branch stays reviewable, and
re-synced deliberately rather than by hand:

```sh
# from the AffinityOnLinux checkout
git subtree pull --prefix=AffinityManager --squash <path-to-this-repo> main
```

Two copies of two thousand lines will drift if the only thing keeping them in
step is somebody remembering. The subtree link is what makes the resync a
command rather than an act of memory.

In that layout the installer beside it is found first and outranks any fetched
checkout — a copy shipped inside AffinityOnLinux should drive the installer it
shipped with — and the branch warning below is suppressed, because there the
branch is whatever AffinityOnLinux is on and warning about it every time is how
a real warning stops being read. It is still checked for `AFFINITY_INSTALL_DIR`
support like any other: being adjacent buys it no trust it has not earned.

## Requirements

- Python 3.10+, PyQt6
- The AffinityOnLinux installer, **new enough to understand
  `AFFINITY_INSTALL_DIR`**. Run from the repository or from a clone, it is the
  one beside the manager and nothing needs setting up.

Installs are run by AffinityOnLinux itself, from a checkout on a branch that
understands `AFFINITY_INSTALL_DIR` — currently **`experimental/affinity-3.3`**,
until that work is upstream. Which one is in use is shown in the window and in
Settings, branch and commit included, because it is not a detail: an installer
without that support ignores the prefix it is handed and installs into
`~/.AffinityLinux`, over the very prefix you were protecting. The manager checks
before running it and refuses rather than finding out afterwards.

It is looked for in this order, most deliberate first:

1. `$AFFINITY_INSTALLER_SCRIPT`
2. the path set in Settings
3. beside the manager, in `AffinityOnLinux/AffinityScripts/` — the case when it
   is run from the repository or a clone
4. `<base>/Manager/AffinityOnLinux/` — the manager's own checkout, pinned to
   that branch, which **Settings → Fetch branch** clones or updates
5. beside this project, then a sibling `AffinityOnLinux/AffinityScripts/`,
   `~/.AffinityLinuxManager/`, `~/.local/share/AffinityOnLinux/`

Changing it in Settings takes effect at the next start. The installer is loaded
into this process, and a second copy cannot be loaded beside the first.

Fetching its own checkout is the option to use if you also develop on
AffinityOnLinux — otherwise switching that checkout to another branch silently
changes what the manager installs with, and the window will tell you so.

## Running the tests

```sh
python3 -m pytest tests/ -q
```

They cover the parts that must not lose track of a prefix, or lose a prefix:
duplicate names, one directory under two names, a corrupt registry, that
removing a prefix from the list never touches the directory, that a clone
cannot write back into its original, that the operation lock refuses a second
operation and that its watchdog never opens itself on silence, that a hosted
installer which settled on the wrong directory is refused rather than shown,
that rewriting launchers for a moved prefix leaves
`~/.AffinityLinuxManager` and `~/.AffinityLinux-winetest` alone, that a backup
and restore round trip undoes an install without undoing anything else, that
drive letters are never written into a prefix Wine has not set up, and that
`run.py` refuses a tarball that is not the repository.

Several of them were checked by breaking the thing they test and watching them
fail; where that was done it is said in the commit.

## Licence and provenance

**No AffinityOnLinux code is copied into this project.** There was once a
1,410-line `affinity_manager/aol_ui.py`, extracted verbatim so this would look
like the installer rather than merely resemble it. It is gone. The installer is
now loaded as a module from the checkout you already have, and the themes, the
shared widgets and the window class come from that file at runtime.

That is possible because of an asymmetry worth stating plainly: the installer
must stay a single file, since its documented install is `curl … | python3` and
it can therefore never import a sibling. The manager has no such constraint and
is never piped itself — `run.py`, the one file that is, only downloads it. So
the dependency can only point one way, and that way happens to be the useful
one.

Importing it was measured rather than assumed. Every column-zero statement in
those 19,000 lines is an import, a def, a class, the PyQt6 try/except, or the
`if __name__ == "__main__"` guard, so loading it starts no GUI, no threads and
no probing, and takes about 0.05s.

**AffinityOnLinux carries no licence file** — none in its history, and its
README's "License" section is about Affinity's own commercial licence rather
than the project's code. Nothing here redistributes any of it, which is the
position this arrangement was chosen for.
