"""Build the TEMPO script-fix plugins: the two vanilla scripts that count frames instead of seconds.

  TEMPO - Script Fixes.esp   FFER02AntSCRIPT (Fallout3.esm)
      Random-encounter ant grew by 0.002 scale per frame. Now grows 0.12 per second (the same speed
      it had at 60 FPS) and clamps at its target size.
  TEMPO - Zeta Fix.esp       DLC05TriggerCreateDetectionEventSCRIPT (Zeta.esm)
      Fired a stealth detection event every 50 frames while the player stands in the trigger.
      Now every 0.83 seconds (50 frames at 60 FPS).

There is no command-line script compiler, so the bytecode is assembled here from the encodings the
GECK uses (verified against every vanilla script by scda.py). As a self-test the assembler must
reproduce both original scripts byte-for-byte before the modified versions are written.

    python build_esp.py              build and install into Data\\, activate in plugins.txt
    python build_esp.py --no-install build into TEMPO\\build only
"""
import os, struct, sys, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scriptscan import DATA, PLUGINS_TXT
from scda import check

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# ------------------------------------------------------------------------------------------------
# Plugin reading


def read_records(path):
    """Yield (header24, body, groupHeader24) for every record; body is decompressed."""
    data = open(path, "rb").read()

    def walk(off, end, grp):
        while off < end:
            typ = data[off:off + 4]
            size = struct.unpack_from("<I", data, off + 4)[0]
            if typ == b"GRUP":
                yield from walk(off + 24, off + size, data[off:off + 24])
                off += size
            else:
                hdr = data[off:off + 24]
                body = data[off + 24:off + 24 + size]
                if struct.unpack_from("<I", hdr, 8)[0] & 0x00040000:
                    body = zlib.decompress(body[4:])
                yield hdr, body, grp
                off += 24 + size

    first = struct.unpack_from("<I", data, 4)[0]
    yield from walk(24 + first, len(data), None)


def subrecords(body):
    off, out = 0, []
    while off + 6 <= len(body):
        typ = body[off:off + 4].decode("latin1")
        size = struct.unpack_from("<H", body, off + 4)[0]
        out.append([typ, body[off + 6:off + 6 + size]])
        off += 6 + size
    return out


def pack_subrecords(subs):
    return b"".join(t.encode("latin1") + struct.pack("<H", len(d)) + d for t, d in subs)


def find_script(plugin, edid):
    for hdr, body, grp in read_records(os.path.join(DATA, plugin)):
        if hdr[:4] == b"SCPT":
            subs = subrecords(body)
            if subs and subs[0][0] == "EDID" and subs[0][1].rstrip(b"\0").decode("latin1") == edid:
                return hdr, subs, grp
    raise KeyError(f"{edid} not found in {plugin}")


def get(subs, typ):
    return next(d for t, d in subs if t == typ)


def put(subs, typ, data):
    for s in subs:
        if s[0] == typ:
            s[1] = data
            return
    raise KeyError(typ)

# ------------------------------------------------------------------------------------------------
# Script assembler (FO3 compiled script format)

u16 = lambda v: struct.pack("<H", v)
u32 = lambda v: struct.pack("<I", v)


# Expression tokens (postfix, each preceded by a space)
def short(i): return b"s" + u16(i)      # short/int local
def flt(i):   return b"f" + u16(i)      # float local
def num(s):   return s.encode("ascii")
def op(s):    return s.encode("ascii")
def func(opcode): return b"X" + u16(opcode) + u16(0)  # call with no parameters


def expr(*tokens):
    return b"".join(b" " + t for t in tokens)


GET_SECONDS_PASSED = 0x100C
GET_SCALE = 0x1018


# Statements: ("raw", bytes) | ("begin", blockType, params) | ("end",) | ("if", expr) |
#             ("else",) | ("endif",) | ("set", varToken, expr)
def assemble(program):
    stmts = []  # (kind, bytes) with jumps filled in later
    for s in program:
        kind = s[0]
        if kind == "raw":
            stmts.append(["raw", s[1]])
        elif kind == "begin":
            stmts.append(["begin", s[1], s[2]])
        elif kind == "end":
            stmts.append(["end", b"\x11\x00\x00\x00"])
        elif kind == "if":
            stmts.append(["if", s[1]])
        elif kind == "else":
            stmts.append(["else"])
        elif kind == "endif":
            stmts.append(["endif", b"\x19\x00\x00\x00"])
        elif kind == "set":
            data = s[1] + u16(len(s[2])) + s[2]
            stmts.append(["raw", b"\x15\x00" + u16(len(data)) + data])
        else:
            raise ValueError(kind)

    # If/Else jump = number of statements strictly between it and the matching Else/EndIf.
    def jump_from(i):
        depth, k = 0, i + 1
        while True:
            kind = stmts[k][0]
            if kind == "if":
                depth += 1
            elif kind == "endif":
                if depth == 0:
                    return k - i - 1
                depth -= 1
            elif kind == "else" and depth == 0:
                return k - i - 1
            k += 1

    encoded = []
    for i, s in enumerate(stmts):
        if s[0] == "if":
            data = u16(jump_from(i)) + u16(len(s[1])) + s[1]
            encoded.append(b"\x16\x00" + u16(len(data)) + data)
        elif s[0] == "else":
            encoded.append(b"\x17\x00\x02\x00" + u16(jump_from(i)))
        elif s[0] == "begin":
            encoded.append(None)  # filled below
        else:
            encoded.append(s[1])

    # Begin jump = bytes from the end of the Begin statement through the end of its End.
    for i, s in enumerate(stmts):
        if s[0] == "begin":
            j = next(k for k in range(i + 1, len(stmts)) if stmts[k][0] == "end")
            size = sum(len(encoded[k]) for k in range(i + 1, j + 1))
            data = u16(s[1]) + u32(size) + s[2]
            encoded[i] = b"\x10\x00" + u16(len(data)) + data
    return b"".join(encoded)


SCRIPT_NAME = ("raw", b"\x1D\x00\x00\x00")
BLOCK_GAMEMODE, BLOCK_ONLOAD, BLOCK_ONTRIGGER = 0x00, 0x15, 0x18

# ------------------------------------------------------------------------------------------------
# FFER02AntSCRIPT   locals: 1 startGrow (short), 2 myScale (float), 4 targetScale (float)

ANT_ONLOAD = [
    ("begin", BLOCK_ONLOAD, b""),
    ("if", expr(short(1), num("0"), op("=="))),
    ("set", short(1), expr(num("1"))),
    ("raw", bytes.fromhex("0F1009000200 0000 6E02000000".replace(" ", ""))),  # setav aggression 2
    ("set", flt(2), expr(func(GET_SCALE))),
    ("set", flt(4), expr(flt(2), num("1.5"), op("*"))),
    ("endif",),
    ("end",),
]
SET_SCALE_MYSCALE = ("raw", bytes.fromhex("3C1105000100660200"))
SET_STARTGROW_2 = ("set", short(1), expr(num("2")))
EVP = ("raw", bytes.fromhex("5E100000"))

ANT_ORIGINAL = [SCRIPT_NAME] + ANT_ONLOAD + [
    ("begin", BLOCK_GAMEMODE, b""),
    ("if", expr(short(1), num("1"), op("=="))),
    ("if", expr(flt(2), flt(4), op("<"))),
    ("set", flt(2), expr(flt(2), num(".002"), op("+"))),
    SET_SCALE_MYSCALE,
    ("else",),
    SET_STARTGROW_2,
    EVP,
    ("endif",),
    ("endif",),
    ("end",),
]

ANT_FIXED = [SCRIPT_NAME] + ANT_ONLOAD + [
    ("begin", BLOCK_GAMEMODE, b""),
    ("if", expr(short(1), num("1"), op("=="))),
    ("if", expr(flt(2), flt(4), op("<"))),
    ("set", flt(2), expr(flt(2), func(GET_SECONDS_PASSED), num("0.12"), op("*"), op("+"))),
    ("if", expr(flt(2), flt(4), op(">"))),
    ("set", flt(2), expr(flt(4))),
    ("endif",),
    SET_SCALE_MYSCALE,
    ("else",),
    SET_STARTGROW_2,
    EVP,
    ("endif",),
    ("endif",),
    ("end",),
]

ANT_SOURCE = """scn FFER02AntSCRIPT

short startGrow		; set to 1 to start growing
float myScale		; current scale
float targetScale	; how big do I want to get?

begin OnLoad
	if startGrow == 0
		set startGrow to 1
		setav aggression 2
		set myScale to GetScale
		set targetScale to myScale * 1.5
	endif
end

begin gamemode
	if startGrow == 1
		if myScale < targetScale
			; TEMPO: was "+ .002" per frame, which grew faster at higher framerates.
			; 0.12 per second is the speed it had at 60 FPS.
			set myScale to myScale + GetSecondsPassed * 0.12
			if myScale > targetScale
				set myScale to targetScale
			endif
			setScale myScale
		else
			set startGrow to 2
			evp
		endif
	endif
end
"""

# ------------------------------------------------------------------------------------------------
# DLC05TriggerCreateDetectionEventSCRIPT   locals: 1 delay (int -> float); ref 1 = player

ON_TRIGGER_PLAYER = ("begin", BLOCK_ONTRIGGER, bytes.fromhex("0100720100"))
CREATE_DETECTION_EVENT = ("raw", bytes.fromhex("68100A0002007201006E64000000"))  # CreateDetectionEvent player 100

ZETA_ORIGINAL = [
    SCRIPT_NAME,
    ON_TRIGGER_PLAYER,
    ("if", expr(short(1), num("1"), op("<"))),
    ("set", short(1), expr(num("50"))),
    CREATE_DETECTION_EVENT,
    ("else",),
    ("set", short(1), expr(short(1), num("1"), op("-"))),
    ("endif",),
    ("end",),
]

ZETA_FIXED = [
    SCRIPT_NAME,
    ON_TRIGGER_PLAYER,
    ("if", expr(flt(1), num("0"), op("<="))),
    ("set", flt(1), expr(num("0.83"))),
    CREATE_DETECTION_EVENT,
    ("else",),
    ("set", flt(1), expr(flt(1), func(GET_SECONDS_PASSED), op("-"))),
    ("endif",),
    ("end",),
]

ZETA_SOURCE = """scn DLC05TriggerCreateDetectionEventSCRIPT

float delay	; TEMPO: seconds (was an int counting frames)

begin onTrigger Player

	if delay <= 0
		; TEMPO: was 50 frames, which fired more often at higher framerates.
		; 0.83 seconds is 50 frames at 60 FPS.
		set delay to 0.83
;		showWarning "Detection Event"
		CreateDetectionEvent player 100
	else
		set delay to delay - GetSecondsPassed
	endif

end
"""

# ------------------------------------------------------------------------------------------------


def make_override(plugin, edid, original, fixed, source, float_vars=()):
    hdr, subs, grp = find_script(plugin, edid)
    code = get(subs, "SCDA")
    rebuilt = assemble(original)
    if rebuilt != code:
        raise SystemExit(f"self-test failed: {edid} original does not reassemble identically\n"
                         f"  orig {code.hex()}\n  ours {rebuilt.hex()}")
    new_code = assemble(fixed)
    problems = check(new_code)
    if problems:
        raise SystemExit(f"{edid}: assembled bytecode fails structural check: {problems}")

    schr = bytearray(get(subs, "SCHR"))
    struct.pack_into("<I", schr, 8, len(new_code))  # compiled size
    put(subs, "SCHR", bytes(schr))
    put(subs, "SCDA", new_code)
    put(subs, "SCTX", source.replace("\n", "\r\n").encode("latin1"))
    # Retype locals: SLSD is {index u32, 12 unused, flags u8 (1 = integer), 7 unused}
    for t, d in subs:
        if t == "SLSD" and struct.unpack_from("<I", d, 0)[0] in float_vars:
            d2 = bytearray(d)
            d2[16] = 0
            subs[subs.index([t, d])][1] = bytes(d2)

    body = pack_subrecords(subs)
    flags = struct.unpack_from("<I", hdr, 8)[0] & ~0x00040000
    new_hdr = hdr[:4] + u32(len(body)) + u32(flags) + hdr[12:]
    print(f"  {edid} [{struct.unpack_from('<I', hdr, 12)[0]:08X}]: {len(code)} -> {len(new_code)} bytes of bytecode")
    return new_hdr + body, grp


# Each plugin carries only the masters its records need, so players without Mothership Zeta can
# still use the base-game fix.
PLUGINS = [
    {
        "name": "TEMPO - Script Fixes.esp",
        "masters": ["Fallout3.esm"],
        "desc": "TEMPO: the random-encounter giant ant grows by seconds instead of frames.",
        "records": lambda: [
            make_override("Fallout3.esm", "FFER02AntSCRIPT", ANT_ORIGINAL, ANT_FIXED, ANT_SOURCE),
        ],
    },
    {
        "name": "TEMPO - Zeta Fix.esp",
        "masters": ["Fallout3.esm", "Zeta.esm"],
        "desc": "TEMPO: a Mothership Zeta stealth-detection trigger fires by seconds instead of frames.",
        "records": lambda: [
            make_override("Zeta.esm", "DLC05TriggerCreateDetectionEventSCRIPT", ZETA_ORIGINAL, ZETA_FIXED,
                          ZETA_SOURCE, float_vars={1}),
        ],
    },
]


def build(plugin):
    recs = plugin["records"]()
    # Form IDs keep their original load-order byte, so the master list must match the source file's
    # own position: Fallout3.esm = 00, Zeta.esm = 01 (Zeta's only master is Fallout3.esm).
    for rec, _ in recs:
        index = struct.unpack_from("<I", rec, 12)[0] >> 24
        if index >= len(plugin["masters"]):
            raise SystemExit(f"{plugin['name']}: record {struct.unpack_from('<I', rec, 12)[0]:08X} needs master #{index}")
    grp_hdr = recs[0][1]  # top-level SCPT group header from the source master
    content = b"".join(r for r, _ in recs)
    group = grp_hdr[:4] + u32(24 + len(content)) + grp_hdr[8:] + content

    def sub(t, d): return t.encode() + u16(len(d)) + d
    tes4_body = (sub("HEDR", struct.pack("<fII", 0.94, len(recs) + 1, 0x800)) +
                 sub("CNAM", b"TEMPO\0") +
                 sub("SNAM", plugin["desc"].encode("latin1") + b"\0") +
                 b"".join(sub("MAST", m.encode("latin1") + b"\0") + sub("DATA", bytes(8)) for m in plugin["masters"]))
    tes4 = b"TES4" + u32(len(tes4_body)) + u32(0) + u32(0) + u32(0) + u16(15) + u16(0) + tes4_body
    return tes4 + group


def activate(names):
    raw = open(PLUGINS_TXT, "rb").read()
    active = [l.strip().lower() for l in raw.decode("latin1").splitlines()]
    missing = [n for n in names if n.lower() not in active]
    if missing:
        sep = b"" if not raw or raw.endswith(b"\n") else b"\r\n"
        open(PLUGINS_TXT, "ab").write(sep + b"".join(n.encode("latin1") + b"\r\n" for n in missing))
        print(f"activated in {PLUGINS_TXT}: {', '.join(missing)}")


def main(argv):
    built = []
    for plugin in PLUGINS:
        print(f"assembling {plugin['name']}:")
        esp = build(plugin)
        out = os.path.join(ROOT, "build", plugin["name"])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "wb").write(esp)
        print(f"  built {out} ({len(esp)} bytes)")
        built.append((plugin["name"], esp))
    if "--no-install" in argv:
        return
    for name, esp in built:
        dest = os.path.join(DATA, name)
        open(dest, "wb").write(esp)
        print(f"installed {dest}")
    activate([name for name, _ in built])


if __name__ == "__main__":
    main(sys.argv[1:])