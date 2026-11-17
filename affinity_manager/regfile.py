"""Reading and adding values in Wine's registry files, with Wine not running.

Wine keeps the registry in three text files in the prefix -- system.reg,
user.reg, userdef.reg -- and rewrites them from memory when the wineserver
exits. So these functions are only for a prefix nobody is running: an edit
made under a live wineserver is silently overwritten when it stops.

Deliberately small. It reads one key's values and adds values that are
missing. It never changes or removes a value: the one job it has is carrying
font registrations into a prefix that lacks them, and "fill in what is
missing" is the only rule that cannot break a registration the destination
already has.

The files are read as Latin-1, which maps every byte to one character and
back, so whatever Wine wrote -- its \\x escapes, or bytes from an older Wine --
survives a round trip unchanged.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# As the key is written in the file: backslashes doubled.
FONTS_KEY = r"Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts"

ENCODING = "latin-1"


def _lines(path: Path) -> list[str]:
    return Path(path).read_text(encoding=ENCODING).splitlines(keepends=True)


def _is_header(line: str) -> bool:
    return line.startswith("[")


def _header_key(line: str) -> str:
    """The key in a section header: '[A\\\\B] 1792441856' -> 'A\\\\B'."""
    end = line.find("]")
    return line[1:end] if end > 0 else ""


def _value_name(line: str) -> str | None:
    """The name of the value a line starts, as written (quoted), or None.

    '"Arial (TrueType)"="arial.ttf"' -> '"Arial (TrueType)"'. '@=' is the
    default value. A quote inside a name is escaped as \\", and a backslash
    as \\\\, so the closing quote is the first one not escaped."""
    if line.startswith("@="):
        return "@"
    if not line.startswith('"'):
        return None
    i = 1
    while i < len(line):
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return line[: i + 1] if line[i + 1: i + 2] == "=" else None
        i += 1
    return None


def _section(lines: list[str], key: str) -> tuple[int, int] | None:
    """(first line after the header, end) of a key's section, or None."""
    wanted = key.lower()
    for i, line in enumerate(lines):
        if _is_header(line) and _header_key(line).lower() == wanted:
            j = i + 1
            while j < len(lines) and not _is_header(lines[j]):
                j += 1
            return i + 1, j
    return None


def read_values(path, key: str) -> dict[str, str]:
    """{name as written: the whole entry as written} for one key.

    The entry is kept as text -- including continuation lines, for a hex value
    long enough that Wine wrapped it -- so add_values can write it back
    exactly. Empty when the file or the key is missing."""
    try:
        lines = _lines(Path(path))
    except OSError:
        return {}
    span = _section(lines, key)
    if span is None:
        return {}
    out: dict[str, str] = {}
    i, end = span
    while i < end:
        line = lines[i]
        name = _value_name(line)
        if name is None:
            i += 1
            continue
        entry = [line]
        while entry[-1].rstrip("\r\n").endswith("\\") and i + 1 < end:
            i += 1
            entry.append(lines[i])
        text = "".join(entry)
        if not text.endswith("\n"):
            text += "\n"
        out[name] = text
        i += 1
    return out


def add_values(path, key: str, entries: dict[str, str]) -> list[str]:
    """Add each entry whose name the key does not have yet. Returns the names
    added. Existing values are never touched.

    The key's section is created at the end of the file if it is missing.
    Written to a temporary file and renamed over the original, so an
    interrupted write leaves the old file whole."""
    path = Path(path)
    lines = _lines(path)
    have = {n.lower() for n in read_values(path, key)}
    new = {n: e for n, e in entries.items() if n.lower() not in have}
    if not new:
        return []
    block = [e if e.endswith("\n") else e + "\n" for e in new.values()]
    span = _section(lines, key)
    if span is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append(f"[{key}] {int(time.time())}\n")
        lines.extend(block)
    else:
        start, end = span
        # After the section's last non-blank line, so the blank line Wine
        # puts between sections stays between them.
        at = end
        while at > start and not lines[at - 1].strip():
            at -= 1
        lines[at:at] = block
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(lines), encoding=ENCODING)
    try:
        os.chmod(tmp, path.stat().st_mode & 0o7777)
    except OSError:
        pass
    tmp.replace(path)
    return list(new)


# ── font registrations ───────────────────────────────────────────────────────

FONT_FILES = ("system.reg", "user.reg")


def font_registrations(prefix) -> dict[str, dict[str, str]]:
    """{"system.reg": {...}, "user.reg": {...}}: the Fonts key in each."""
    root = Path(prefix)
    return {name: read_values(root / name, FONTS_KEY) for name in FONT_FILES}


def _string_data(entry: str) -> str | None:
    """The data of a string value, unescaped: '"A"="Z:\\\\x.ttf"' -> 'Z:\\x.ttf'."""
    name = _value_name(entry)
    if name is None:
        return None
    rest = entry[len(name) + 1:].strip()
    if not (rest.startswith('"') and rest.endswith('"')):
        return None
    return rest[1:-1].replace("\\\\", "\\")


def font_file_exists(entry: str, prefix) -> bool:
    """Does the file a font registration names exist, here and now?

    A registration naming nothing is worse than none: Wine keeps the name and
    finds no face. And one naming a font inside another Wine's own folder is
    the bug that put Microsoft Yahei into Affinity's bold interface text -- so
    those are refused even when the file is there."""
    data = _string_data(entry)
    if data is None:
        return True                     # not a plain string: leave it to Wine
    low = data.lower().replace("/", "\\")
    if "\\share\\wine\\fonts\\" in low:
        return False
    root = Path(prefix)
    if len(data) > 2 and data[1] == ":":
        letter, rest = data[0].lower(), data[2:].lstrip("\\").replace("\\", "/")
        if letter == "z":
            return Path("/", rest).exists()
        if letter == "c":
            return (root / "drive_c" / rest).exists()
        link = root / "dosdevices" / f"{letter}:"
        return (link / rest).exists()
    return (root / "drive_c" / "windows" / "Fonts" / data).exists()


def add_font_registrations(prefix, regs: dict) -> list[str]:
    """Add the registrations a prefix is missing, for files that exist.
    Returns notes."""
    notes = []
    root = Path(prefix)
    for name in FONT_FILES:
        wanted = {n: e for n, e in (regs.get(name) or {}).items()
                  if font_file_exists(e, root)}
        target = root / name
        if not wanted or not target.is_file():
            continue
        added = add_values(target, FONTS_KEY, wanted)
        if added:
            notes.append(f"{len(added)} font registration(s) added to {name}")
    return notes
