"""Build the Nexus release archives for TEMPO.

    python package.py

Produces in TEMPO\\dist\\:
    TEMPO-<version>.zip          main file: Data-relative layout for Vortex / MO2 / manual install
    TEMPO-<version>-source.zip   optional file: source code and tools

Rebuilds the DLL and both ESPs first, then checks the result: required files present, debug
options off in the shipped INI, DLL exports the FOSE entry points.
"""
import hashlib, os, re, struct, subprocess, sys, time, zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(TOOLS, ".."))
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, TOOLS)
import build_esp


def version():
    src = open(os.path.join(ROOT, "src", "main.cpp"), encoding="utf-8").read()
    return re.search(r'#define PLUGIN_VERSION_STR "([^"]+)"', src).group(1)


def build_all():
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(ROOT, "build.ps1"), "-NoInstall"], check=True)
    build_esp.main(["--no-install"])


def dll_exports(path):
    """Names exported by a PE32 DLL (enough to confirm the FOSE entry points)."""
    data = open(path, "rb").read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt = pe + 24
    exp_rva = struct.unpack_from("<I", data, opt + 96)[0]
    secs = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]

    def off(rva):  # section header: name[8], VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData
        for i in range(nsec):
            vsize, vaddr, _, raw = struct.unpack_from("<IIII", data, secs + i * 40 + 8)
            if vaddr <= rva < vaddr + vsize:
                return rva - vaddr + raw
        raise ValueError(hex(rva))

    if not exp_rva:
        return []
    e = off(exp_rva)
    count, names = struct.unpack_from("<I", data, e + 24)[0], struct.unpack_from("<I", data, e + 32)[0]
    out = []
    for i in range(count):
        p = off(struct.unpack_from("<I", data, off(names) + 4 * i)[0])
        out.append(data[p:data.index(b"\0", p)].decode())
    return out


def main():
    ver = version()
    print(f"TEMPO {ver}: building")
    build_all()

    readme = open(os.path.join(ROOT, "README.txt"), encoding="utf-8").read().replace("{VERSION}", ver)
    ini = open(os.path.join(ROOT, "TEMPO.ini"), "rb").read()

    # Release checks
    problems = []
    for key in (b"bDebugStats=0", b"fFPSLimit=0", b"bFixTimer=1", b"bFixHavok=1", b"bDisableMouseAcceleration=1"):
        if key not in ini:
            problems.append(f"TEMPO.ini: expected {key.decode()}")
    exports = dll_exports(os.path.join(BUILD, "TEMPO.dll"))
    for name in ("FOSEPlugin_Query", "FOSEPlugin_Load"):
        if name not in exports:
            problems.append(f"TEMPO.dll does not export {name}")
    if problems:
        raise SystemExit("release checks failed:\n  " + "\n  ".join(problems))

    main_files = [
        ("FOSE/Plugins/TEMPO.dll", open(os.path.join(BUILD, "TEMPO.dll"), "rb").read()),
        ("FOSE/Plugins/TEMPO.ini", ini),
        ("FOSE/Plugins/TEMPO - Readme.txt", readme.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")),
    ] + [(p["name"], open(os.path.join(BUILD, p["name"]), "rb").read()) for p in build_esp.PLUGINS]

    source_files = []
    for rel in ["README.md", "README.txt", "LICENSE", "TEMPO.ini", "build.ps1", "nexus_description.txt", "nexus_summary.txt", "src/main.cpp",
                "tools/build_esp.py", "tools/package.py", "tools/scda.py", "tools/scriptscan.py"]:
        source_files.append((f"TEMPO-{ver}-source/{rel}", open(os.path.join(ROOT, rel), "rb").read()))

    os.makedirs(DIST, exist_ok=True)
    stamp = time.localtime()[:6]
    for name, files in ((f"TEMPO-{ver}.zip", main_files), (f"TEMPO-{ver}-source.zip", source_files)):
        path = os.path.join(DIST, name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for arc, data in files:
                info = zipfile.ZipInfo(arc, date_time=stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, data)
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        print(f"\n{path}  ({os.path.getsize(path):,} bytes)\n  sha256 {digest}")
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                raise SystemExit(f"corrupt entry {bad}")
            for i in z.infolist():
                print(f"  {i.file_size:>9,}  {i.filename}")


if __name__ == "__main__":
    main()
