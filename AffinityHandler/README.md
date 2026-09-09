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
