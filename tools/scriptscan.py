"""Find Fallout 3 scripts whose timing depends on framerate.

Object and effect scripts run their GameMode / MenuMode / ScriptEffectUpdate / OnTrigger blocks
once per frame. A script that counts frames ("set timer to timer + 1") or moves things by a fixed
amount per frame runs faster at higher FPS. Scripts that time things with GetSecondsPassed are fine.

Usage:
    python scriptscan.py                 scan every active plugin from plugins.txt
    python scriptscan.py Foo.esp Bar.esm scan specific files (names resolve against Data\\)
    python scriptscan.py --all ...       also list quest scripts and blocks that use GetSecondsPassed

This is a heuristic: it reads script source (SCTX), so it finds candidates for a human to check.
"""
import os, re, struct, sys, zlib

GAME = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DATA = os.path.join(GAME, "Data")
PLUGINS_TXT = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Fallout3", "plugins.txt")

PER_FRAME = {"gamemode", "menumode", "scripteffectupdate", "ontrigger"}
BLOCK = re.compile(r"^\s*begin\s+(\w+)(.*?)^\s*end\b", re.I | re.M | re.S)
COUNTER = re.compile(r"^\s*set\s+(\w+)\s+to\s+\(?\s*\1\s*([+-])\s*([0-9]*\.?[0-9]+)\s*\)?\s*(?:;.*)?$", re.I | re.M)
MOVE = re.compile(r"^\s*(?:\w+\.)?(setpos|setangle|setscale)\b.*$", re.I | re.M)
SECONDS = re.compile(r"getsecondspassed", re.I)


def records(data, off, end):
    while off < end:
        typ = data[off:off + 4]
        size = struct.unpack_from("<I", data, off + 4)[0]
        if typ == b"GRUP":
            yield from records(data, off + 24, off + size)
            off += size
        else:
            flags = struct.unpack_from("<I", data, off + 8)[0]
            yield typ, flags, data[off + 24:off + 24 + size]
            off += 24 + size


def subrecords(body):
    off, out = 0, {}
    while off + 6 <= len(body):
        typ = body[off:off + 4].decode("latin1")
        size = struct.unpack_from("<H", body, off + 4)[0]
        out.setdefault(typ, body[off + 6:off + 6 + size])
        off += 6 + size
    return out


def scan_source(src, show_all):
    findings = []
    for m in BLOCK.finditer(src):
        block, body = m.group(1).lower(), m.group(2)
        if block not in PER_FRAME:
            continue
        timed = bool(SECONDS.search(body))
        if timed and not show_all:
            continue
        tag = f"{m.group(1)}{' (has GetSecondsPassed)' if timed else ''}"
        for c in COUNTER.finditer(body):
            findings.append(f"{tag}: {c.group(0).strip()}")
        # Per-frame SetPos/SetAngle/SetScale fed by an accumulating variable.
        for mv in MOVE.finditer(body):
            line = mv.group(0).strip()
            if any(c.group(1).lower() in line.lower() for c in COUNTER.finditer(body)):
                findings.append(f"{tag}: {line}")
    return findings


def scan_plugin(path, show_all=False):
    data = open(path, "rb").read()
    first = struct.unpack_from("<I", data, 4)[0]
    total = with_source = 0
    hits = []
    for typ, flags, body in records(data, 24 + first, len(data)):
        if typ != b"SCPT":
            continue
        total += 1
        if flags & 0x00040000:
            body = zlib.decompress(body[4:])
        sub = subrecords(body)
        src = sub.get("SCTX", b"").decode("latin1")
        if not src:
            continue
        with_source += 1
        # SCHR: unused, refCount, compiledSize, varCount (4 bytes each), then type: 0 object, 1 quest, 0x100 effect.
        # Quest scripts run every fQuestDelayTime (seconds), not every frame.
        schr = sub.get("SCHR", b"")
        stype = struct.unpack_from("<H", schr, 16)[0] if len(schr) >= 18 else 0
        if stype == 1 and not show_all:
            continue
        src = "\n".join(l.split(";", 1)[0] for l in src.splitlines())
        findings = scan_source(src, show_all)
        if findings:
            hits.append((sub.get("EDID", b"?").rstrip(b"\0").decode("latin1"), sorted(set(findings))))
    return total, with_source, hits


def active_plugins():
    names = []
    with open(PLUGINS_TXT, encoding="latin1") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(line)
    return names


def main(argv):
    show_all = "--all" in argv
    names = [a for a in argv if a != "--all"] or active_plugins()
    for name in names:
        path = name if os.path.isabs(name) else os.path.join(DATA, name)
        if not os.path.exists(path):
            print(f"== {name}: not found")
            continue
        total, with_source, hits = scan_plugin(path, show_all)
        note = "" if with_source == total else f" ({total - with_source} without source: not checkable)"
        print(f"== {name}: {total} scripts, {len(hits)} flagged{note}")
        for edid, findings in hits:
            print(f"   {edid}")
            for f in findings[:6]:
                print(f"      {f}")
            if len(findings) > 6:
                print(f"      ... +{len(findings) - 6} more")


if __name__ == "__main__":
    main(sys.argv[1:])
