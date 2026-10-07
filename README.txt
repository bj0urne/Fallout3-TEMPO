T.E.M.P.O. - Timing, Engine, Mouse & Physics Overhaul
Version {VERSION} for Fallout 3
=========================================================

Play Fallout 3 at any framerate (30, 60, 144, 240+) with the game running at the right speed and
feeling smooth at every framerate.


WHAT IT DOES
------------
* Precise engine timer
  Replaces every engine frame timer (world, menus, interface). The vanilla ones count in whole
  milliseconds from a 64 Hz clock and force each frame to last at least 10 ms with Sleep(), which
  is a hidden, jittery ~100 FPS cap. The replacement uses QueryPerformanceCounter.
* "64 Hz" fix
  The game's other ~110 timers get a clock accurate to 1 ms, and Windows timer resolution is set
  to 1 ms.
* Havok physics at any framerate
  The physics step (fMaxTime) is matched to the real frame time every frame, so ragdolls, thrown
  items and falling objects move at the right speed and stay smooth.
* No mouse acceleration (optional, on by default)
  Mouse look is 1:1 with your hand. No INI editing needed.
* Precise FPS limiter (optional, off by default)
* Script fixes (optional plugins)
  The only two vanilla/DLC scripts that counted frames instead of seconds now use real time:
    TEMPO - Script Fixes.esp   random-encounter giant ant growth   (requires Fallout3.esm)
    TEMPO - Zeta Fix.esp       Mothership Zeta detection trigger   (requires Zeta.esm)
* Safety checks
  Every patch location is verified byte-for-byte first. On an unexpected game version or a
  conflicting mod, TEMPO logs the reason and leaves the game untouched.


REQUIREMENTS
------------
* Fallout 3 or Fallout 3 GOTY (tested on Steam GOTY)
* Fallout Anniversary Patcher (game version 1.7.0.3, no Games for Windows LIVE)
    https://www.nexusmods.com/fallout3/mods/24913
* Fallout Script Extender (FOSE) 1.2 beta 2 or newer
    https://fose.silverlock.org/
* Windows 10 or 11
* Mothership Zeta, only for "TEMPO - Zeta Fix.esp"


INSTALLATION
------------
Mod manager (Vortex / MO2): install the archive and enable the mod and its plugins.

Manual: extract into "Fallout 3\Data\" so you get:
    Data\FOSE\Plugins\TEMPO.dll
    Data\FOSE\Plugins\TEMPO.ini
    Data\TEMPO - Script Fixes.esp      (optional)
    Data\TEMPO - Zeta Fix.esp          (optional)
Then activate the .esp files in your load order.

Start the game with Fallout3.exe (the Anniversary Patcher loads FOSE) or fose_loader.exe.

To check that it works, open Data\FOSE\Plugins\TEMPO.log after starting the game. It should
contain "hooked TimeGlobal::Update", "havok fix active" and "mouse acceleration disabled".

Remove these old tweaks from Documents\My Games\Fallout3\Fallout.ini if you added them:
* iFPSClamp must be 0 (a non-zero value makes TEMPO step aside)
* custom fMaxTime values (TEMPO overrides them)
* custom fForegroundMouse* values (overridden while bDisableMouseAcceleration=1)


RECOMMENDED SETUP
-----------------
* VSync off for the lowest input lag: iPresentInterval=0 in Fallout.ini and FalloutPrefs.ini.
* G-Sync/FreeSync: set fFPSLimit a few FPS under your refresh rate (for example 141 at 144 Hz).
* No VRR: uncapped is fine, or cap at your refresh rate. Use only one limiter.


CONFIGURATION
-------------
All options are in Data\FOSE\Plugins\TEMPO.ini and are explained there.


COMPATIBILITY
-------------
Don't combine TEMPO with other mods that change the same timing code:
* Fallout Stutter Remover
* lStewieAl's Tweaks "Tick Fix" (set bTickFix=0; the rest of lStewieAl's Tweaks is fine)
* INI settings from high refresh rate guides (iFPSClamp, fMaxTime)

A mod script that counts frames instead of seconds will run faster at high framerates. That is a
problem in the mod's script; please report such mods.

Fallout 3 only. Not for New Vegas or Tale of Two Wastelands.


UNINSTALLING
------------
Remove the files above. The DLL can be removed at any time. If you remove the .esp files, the
game warns once about missing content when you load a save that used them, which is harmless.


CHANGELOG
---------
1.0.0  First release.
