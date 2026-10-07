# T.E.M.P.O. — Timing, Engine, Mouse & Physics Overhaul

A [FOSE](https://fose.silverlock.org/) plugin for **Fallout 3** that makes the game framerate-independent. Play at 30, 60, 144, 240+ FPS with correct game speed, smooth frame pacing and stable physics.

**Players:** download from Nexus Mods *(link)*. This repository is the source.

## What it fixes

| Problem in the vanilla engine | TEMPO |
|---|---|
| Every engine frame timer (world, menus, interface) counts in whole milliseconds from a 64 Hz clock and forces each frame to ≥10 ms with `Sleep()`, a hidden, jittery ~100 FPS cap | Replaces `TimeGlobal::Update` for all instances with a `QueryPerformanceCounter` version: no floor, no Sleep |
| ~110 other timers use `GetTickCount` (64 Hz) | Redirects the executable's `GetTickCount` import to a 1 ms QPC clock and sets 1 ms timer resolution |
| Havok steps in fixed slices of `fMaxTime` (1/60 s), giving 0 or 2 steps per frame at other framerates | Sets `fMaxTime` to the real frame time (clamped) every frame, so there is one step per frame |
| Built-in mouse acceleration | Zeroes the four `fForegroundMouse*` settings (optional) |
| No precise frame cap | Waitable-timer plus spin FPS limiter (optional) |
| Two vanilla/DLC scripts count frames instead of seconds | Rebuilt with `GetSecondsPassed` timers in two optional ESPs |

Every patch site is verified byte-for-byte before anything is written. On a mismatch TEMPO logs the reason and leaves the game untouched.

## Requirements

- Fallout 3 / GOTY, patched to **1.7.0.3** without GFWL by the [Fallout Anniversary Patcher](https://www.nexusmods.com/fallout3/mods/24913)
- FOSE 1.2 beta 2 or newer
- Windows 10/11 (Proton is untested; it should work, and reports are welcome)

## Repository layout

```
src/main.cpp           the FOSE plugin (single file)
TEMPO.ini              default configuration shipped with the plugin
build.ps1              builds TEMPO.dll and installs it into ..\Data\FOSE\Plugins
tools/build_esp.py     assembles the script-fix ESPs (see below)
tools/scda.py          structural checker for compiled script bytecode
tools/scriptscan.py    finds scripts in any plugin that count frames instead of seconds
tools/package.py       builds the release zips into dist/
README.txt             player readme shipped in the release zip
nexus_*.txt            Nexus page texts
```

The scripts expect this folder to sit inside the Fallout 3 install folder (`Fallout 3 goty\TEMPO\`). They read the vanilla masters from `..\Data` and install into it.

## Building

1. **Compiler:** [llvm-mingw](https://github.com/mstorsjo/llvm-mingw) (32-bit target). For example: `winget install MartinStorsjo.LLVM-MinGW.UCRT`
2. **Python 3** for the tools (standard library only)
3. Build:

```powershell
.\build.ps1                     # build TEMPO.dll and install it into Data\FOSE\Plugins
python tools\build_esp.py       # assemble both ESPs, install them and activate them in plugins.txt
python tools\package.py         # build everything and write dist\TEMPO-<version>.zip and -source.zip
```

### About the ESPs

There is no command-line compiler for Fallout 3 scripts, so `build_esp.py` contains a small assembler for the GECK's bytecode format. The encodings were derived from vanilla scripts, which ship with both source (`SCTX`) and bytecode (`SCDA`), and `scda.py` validates the structural rules against all 2,320 vanilla scripts. As a self-test, the assembler must reproduce both original scripts byte-for-byte before it writes the modified ones.

## How it works

Addresses are for `Fallout3.exe` 1.7.0.3.

- `TimeGlobal::Update` (`0x0086C280`, `__thiscall`, argument = GetTickCount ms) is redirected with a 5-byte jump. A trampoline keeps the original for paused and `iFPSClamp` fixed-step cases. The main instance at `0x01090BA0` holds the frame delta that nearly the whole game reads (`+0x0C`).
- The Havok step scheduler (`0x008D3C10`) takes `floor(accumulated / fMaxTime)` steps. TEMPO writes the `fMaxTime:HAVOK` setting (`0x010F2BE4`) right before it runs.
- Setting objects are built by static initializers and filled from the INIs after FOSE loads plugins, so they are verified by name and applied on the first main-loop frame.

## Development and AI disclosure

TEMPO was developed with **Claude (Anthropic)**, which did the reverse engineering, wrote the plugin and tools, and wrote the documentation. Henry Carlsson directed the project and tested every feature in-game. On Nexus Mods it carries the *AI-Generated Content* and *AI Media* tags, as the Nexus guidelines require.

## Credits

- The FOSE team, for the Fallout Script Extender
- lStewieAl, for the Fallout Anniversary Patcher
- The authors of Stutter Remover and the High Refresh Rate Guide, for mapping out these problems first

## License

[MIT](LICENSE)
