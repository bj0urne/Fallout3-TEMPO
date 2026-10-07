// TEMPO (Timing, Engine, Mouse & Physics Overhaul) - FOSE plugin that decouples Fallout 3 (1.7.0.3)
// game speed from framerate.
//
// What it does:
//   1. Timer: TimeGlobal::Update (0x86C280) takes GetTickCount() milliseconds (~15.6 ms
//      resolution), floors every frame to 10 ms with a Sleep() (a hidden, jittery 100 FPS cap)
//      and stores the result as the frame delta. The game has several TimeGlobal instances (main
//      loop, interface/menus, ...). We replace Update for all of them with a
//      QueryPerformanceCounter-based version with no floor.
//   2. GetTickCount: the exe's GetTickCount import is redirected to a QPC-based millisecond clock
//      with real 1 ms resolution (the "64 Hz issue"), used by ~110 timers across the game.
//   3. Havok: physics takes floor(accumulated / fMaxTime) fixed steps per frame. With the
//      default 1/60 s step, any framerate other than 60 gives 0 or 2 steps on some frames
//      (judder, speed-ups, slow motion). We set fMaxTime to the real frame time every frame
//      (clamped) so Havok takes exactly one step per frame.
//   4. Optional precise FPS limiter.
//   5. Mouse: zeroes the fForegroundMouse* settings so mouse look is linear (no acceleration),
//      without anyone having to edit Fallout.ini.
//
// All patch sites are verified byte-for-byte before anything is written.

#include <windows.h>
#include <mmsystem.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <vector>

#define PLUGIN_NAME    "TEMPO"
#define PLUGIN_VERSION 1            // FOSE plugin version (major)
#define PLUGIN_VERSION_STR "1.0.0"

// ---------------------------------------------------------------------------------------------
// FOSE plugin interface (subset of fose/PluginAPI.h; only the leading fields are used)

struct PluginInfo {
	uint32_t    infoVersion;   // must be 1
	const char* name;
	uint32_t    version;
};

struct FOSEInterface {
	uint32_t foseVersion;
	uint32_t runtimeVersion;
	uint32_t editorVersion;
	uint32_t isEditor;
	// RegisterCommand, SetOpcodeBase, QueryInterface, GetPluginHandle follow (unused)
};

// ---------------------------------------------------------------------------------------------
// Fallout3.exe 1.7.0.3 addresses

namespace addr {
	constexpr uintptr_t kTimeGlobalUpdate = 0x0086C280;  // void __thiscall TimeGlobal::Update(UInt32 tickMs)
	constexpr uintptr_t kMainTimeGlobal   = 0x01090BA0;  // main loop instance (frame delta for the world)
	constexpr uintptr_t kGlobalTimeMult   = 0x00F7544C;  // float, current global time multiplier
	constexpr uintptr_t kSettingMaxTime   = 0x010F2BE4;  // Setting fMaxTime:HAVOK {vtbl, value, name}
	constexpr uintptr_t kHavokReadMaxTime = 0x008D3CF4;  // fld [fMaxTime value] in Havok step scheduler

	// Mouse acceleration settings, read every time mouse input is processed (0x0061F8C0).
	// Engine defaults: Mult 4.0, Base 0.2, AccelTop 50, AccelBase 0.2. All zero = linear input.
	constexpr uintptr_t kSettingMouseMult      = 0x011795EC;
	constexpr uintptr_t kSettingMouseBase      = 0x011795F8;
	constexpr uintptr_t kSettingMouseAccelTop  = 0x01179604;
	constexpr uintptr_t kSettingMouseAccelBase = 0x01179610;
}

struct TimeGlobal {
	uint8_t  pauseCount;      // 00  nonzero = paused (delta forced to 0)
	uint8_t  pad01[3];
	float    fixedFrameMs;    // 04  1000/iFPSClamp, 0 = variable timestep
	float    fixedRemainder;  // 08  fractional ms carry for fixed mode
	float    frameDelta;      // 0C  seconds, already scaled by the global time multiplier
	uint32_t lastTimeMs;      // 10  clock in ms (tick - pausedOffset)
	uint32_t pausedOffsetMs;  // 14
	uint8_t  unk18;           // 18
	uint8_t  zeroDeltaFlag;   // 19
};
static_assert(sizeof(TimeGlobal) == 0x1C, "TimeGlobal layout");

struct SettingFloat {
	void*       vtbl;
	float       value;
	const char* name;
};

// ---------------------------------------------------------------------------------------------
// Config (Data\FOSE\Plugins\TEMPO.ini)

static struct Config {
	bool   fixTimer        = true;
	bool   fixTickCount    = true;
	bool   timerResolution = true;
	double maxFrameTime    = 0.166;   // seconds; longer frames are clamped (engine default 166 ms)
	double havokMinFPS     = 30.0;    // physics step never larger than 1/this (substeps below)
	double havokMaxFPS     = 500.0;   // physics step never smaller than 1/this
	double fpsLimit        = 0.0;     // 0 = unlimited
	bool   debugStats      = false;
	int    traceKey        = VK_F11;
	double traceSeconds    = 20.0;
	bool   fixHavok        = true;
	bool   noMouseAccel    = true;
} g_cfg;

static char  g_dir[MAX_PATH];
static FILE* g_log;

static void Log(const char* fmt, ...)
{
	if (!g_log) return;
	SYSTEMTIME st;
	GetLocalTime(&st);
	fprintf(g_log, "[%02u:%02u:%02u.%03u] ", st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
	va_list ap;
	va_start(ap, fmt);
	vfprintf(g_log, fmt, ap);
	va_end(ap);
	fputc('\n', g_log);
	fflush(g_log);
}

static double IniDouble(const char* path, const char* sec, const char* key, double def)
{
	char def_s[64], buf[64];
	snprintf(def_s, sizeof(def_s), "%g", def);
	GetPrivateProfileStringA(sec, key, def_s, buf, sizeof(buf), path);
	return atof(buf);
}

static void LoadConfig()
{
	char path[MAX_PATH];
	snprintf(path, sizeof(path), "%s" PLUGIN_NAME ".ini", g_dir);
	auto b = [&](const char* sec, const char* key, bool def) {
		return GetPrivateProfileIntA(sec, key, def ? 1 : 0, path) != 0;
	};
	g_cfg.fixTimer        = b("Timer", "bFixTimer", g_cfg.fixTimer);
	g_cfg.fixTickCount    = b("Timer", "bFixGetTickCount", g_cfg.fixTickCount);
	g_cfg.timerResolution = b("Timer", "bTimerResolution", g_cfg.timerResolution);
	g_cfg.maxFrameTime    = IniDouble(path, "Timer", "fMaxFrameTimeMs", g_cfg.maxFrameTime * 1000.0) / 1000.0;
	g_cfg.fpsLimit        = IniDouble(path, "Limiter", "fFPSLimit", g_cfg.fpsLimit);
	g_cfg.fixHavok        = b("Havok", "bFixHavok", g_cfg.fixHavok);
	g_cfg.havokMinFPS     = IniDouble(path, "Havok", "fHavokMinFPS", g_cfg.havokMinFPS);
	g_cfg.havokMaxFPS     = IniDouble(path, "Havok", "fHavokMaxFPS", g_cfg.havokMaxFPS);
	g_cfg.noMouseAccel    = b("Mouse", "bDisableMouseAcceleration", g_cfg.noMouseAccel);
	g_cfg.debugStats      = b("Debug", "bDebugStats", g_cfg.debugStats);
	g_cfg.traceKey        = GetPrivateProfileIntA("Debug", "iTraceKey", g_cfg.traceKey, path);
	g_cfg.traceSeconds    = IniDouble(path, "Debug", "fTraceSeconds", g_cfg.traceSeconds);

	if (g_cfg.maxFrameTime < 0.010) g_cfg.maxFrameTime = 0.010;
	if (g_cfg.maxFrameTime > 1.0) g_cfg.maxFrameTime = 1.0;
	if (g_cfg.havokMinFPS < 10.0) g_cfg.havokMinFPS = 10.0;
	if (g_cfg.havokMaxFPS < g_cfg.havokMinFPS) g_cfg.havokMaxFPS = g_cfg.havokMinFPS;
	if (g_cfg.fpsLimit < 0.0) g_cfg.fpsLimit = 0.0;

	Log("config: bFixTimer=%d bFixGetTickCount=%d bTimerResolution=%d fMaxFrameTimeMs=%.1f "
	    "fFPSLimit=%.2f bFixHavok=%d fHavokMinFPS=%.1f fHavokMaxFPS=%.1f "
	    "bDisableMouseAcceleration=%d bDebugStats=%d iTraceKey=0x%X fTraceSeconds=%.1f",
	    g_cfg.fixTimer, g_cfg.fixTickCount, g_cfg.timerResolution, g_cfg.maxFrameTime * 1000.0,
	    g_cfg.fpsLimit, g_cfg.fixHavok, g_cfg.havokMinFPS, g_cfg.havokMaxFPS,
	    g_cfg.noMouseAccel, g_cfg.debugStats, g_cfg.traceKey, g_cfg.traceSeconds);
}

// ---------------------------------------------------------------------------------------------
// Precise timing

static double  g_qpcFreq;        // ticks per second
static int64_t g_qpcFreqInt;
static int64_t g_limitTicks;     // frame period for the limiter, 0 = off
static HANDLE  g_waitTimer;

static inline int64_t Now()
{
	LARGE_INTEGER li;
	QueryPerformanceCounter(&li);
	return li.QuadPart;
}

// Sleep most of the way with a high-resolution waitable timer, spin the last stretch.
static void WaitUntil(int64_t target)
{
	const int64_t spinTicks = (int64_t)(g_qpcFreq * 0.0015);  // spin the final 1.5 ms
	for (;;) {
		int64_t remaining = target - Now();
		if (remaining <= spinTicks) break;
		int64_t sleepTicks = remaining - spinTicks;
		if (g_waitTimer) {
			LARGE_INTEGER due;
			due.QuadPart = -(int64_t)((double)sleepTicks * 1e7 / g_qpcFreq);  // relative, 100 ns units
			if (SetWaitableTimer(g_waitTimer, &due, 0, nullptr, nullptr, FALSE))
				WaitForSingleObject(g_waitTimer, INFINITE);
			else
				Sleep(1);
		} else {
			Sleep(1);
		}
	}
	while (Now() < target)
		YieldProcessor();
}

// GetTickCount replacement: same epoch as the real one, but 1 ms resolution.
static DWORD   g_tickBase;
static int64_t g_tickQpcBase;

static DWORD WINAPI Hook_GetTickCount()
{
	return g_tickBase + (DWORD)((Now() - g_tickQpcBase) * 1000 / g_qpcFreqInt);
}

// ---------------------------------------------------------------------------------------------
// Stats (bDebugStats) and frame trace (iTraceKey)

static struct Stats {
	int64_t            windowStart = 0;
	std::vector<float> dts;
	float              lastHavokStep = 0;
} g_stats;

static void RecordStats(int64_t now, double dt)
{
	if (!g_stats.windowStart) {
		g_stats.windowStart = now;
		g_stats.dts.reserve(8192);
	}
	g_stats.dts.push_back((float)dt);
	if ((double)(now - g_stats.windowStart) / g_qpcFreq < 5.0)
		return;

	std::vector<float>& v = g_stats.dts;
	double sum = 0;
	for (float x : v) sum += x;
	const double avg = sum / v.size();
	double var = 0;
	for (float x : v) var += (x - avg) * (x - avg);
	const double sd = std::sqrt(var / v.size());
	// Frames that differ from the previous one by more than 50%: a direct microstutter count.
	int jumps = 0;
	for (size_t i = 1; i < v.size(); i++)
		if (std::fabs(v[i] - v[i - 1]) > 0.5f * std::max(v[i], v[i - 1]))
			jumps++;
	std::sort(v.begin(), v.end());
	auto pct = [&](double p) { return v[std::min(v.size() - 1, (size_t)(p * v.size()))] * 1000.0; };
	Log("stats: %zu frames, avg %.1f fps | ms: min %.2f p1 %.2f med %.2f p99 %.2f max %.2f sd %.2f | "
	    "1%% low %.0f fps | jumps %d | havok step %.2f ms",
	    v.size(), 1.0 / avg, v.front() * 1000.0, pct(0.01), pct(0.5), pct(0.99), v.back() * 1000.0,
	    sd * 1000.0, 1000.0 / pct(0.99), jumps, g_stats.lastHavokStep * 1000.0);
	v.clear();
	g_stats.windowStart = now;
}

static struct Trace {
	FILE*   file = nullptr;
	int64_t start = 0;
	int64_t frameStart = 0;
	bool    keyWasDown = false;
	int     count = 0;
} g_trace;

static void TraceFrame(int64_t now, double rawDt, double dt, double waited)
{
	const bool down = g_cfg.traceKey && (GetAsyncKeyState(g_cfg.traceKey) & 0x8000);
	if (down && !g_trace.keyWasDown && !g_trace.file) {
		char path[MAX_PATH];
		snprintf(path, sizeof(path), "%s" PLUGIN_NAME "_trace.csv", g_dir);
		g_trace.file = fopen(path, "w");
		if (g_trace.file) {
			setvbuf(g_trace.file, nullptr, _IOFBF, 1 << 20);
			fprintf(g_trace.file, "t_ms,raw_dt_ms,dt_ms,limiter_wait_ms,havok_step_ms\n");
			g_trace.start = now;
			g_trace.count = 0;
			Log("trace started -> %s", path);
		}
	}
	g_trace.keyWasDown = down;
	if (!g_trace.file) return;

	const double t = (double)(now - g_trace.start) / g_qpcFreq;
	fprintf(g_trace.file, "%.3f,%.3f,%.3f,%.3f,%.3f\n", t * 1000.0, rawDt * 1000.0, dt * 1000.0,
	        waited * 1000.0, g_stats.lastHavokStep * 1000.0);
	g_trace.count++;
	if (t >= g_cfg.traceSeconds) {
		fclose(g_trace.file);
		g_trace.file = nullptr;
		Log("trace finished: %d frames", g_trace.count);
	}
}

// ---------------------------------------------------------------------------------------------
// TimeGlobal::Update replacement (all instances)

typedef void(__thiscall* TimeGlobalUpdate_t)(TimeGlobal*, uint32_t);
static TimeGlobalUpdate_t OrigTimeGlobalUpdate;  // trampoline: original prologue + jmp back

// Per-instance QPC timestamp of the previous update.
struct InstanceClock { TimeGlobal* self; int64_t last; };
static InstanceClock g_clocks[16];

static int64_t& LastFor(TimeGlobal* self)
{
	for (InstanceClock& c : g_clocks) {
		if (c.self == self) return c.last;
		if (!c.self) {
			c.self = self;
			c.last = 0;
			Log("timer instance %p registered", (void*)self);
			return c.last;
		}
	}
	static int64_t overflow;  // more instances than expected: share one slot (still precise)
	return overflow;
}

static int g_havokState;  // 0 = not yet verified, 1 = active, -1 = disabled

// Setting objects are built by static initializers that run after FOSE loads plugins, and their
// values come from the INI files read during startup, so they are checked on the first main-loop
// frame instead of at load time.
static SettingFloat* GetSetting(uintptr_t at, const char* expectedName)
{
	SettingFloat* s = (SettingFloat*)at;
	const size_t len = strlen(expectedName) + 1;
	if (!IsBadReadPtr(s->name, len) && memcmp(s->name, expectedName, len) == 0)
		return s;
	Log("ERROR: setting %s not found at %08X", expectedName, (unsigned)at);
	return nullptr;
}

static void VerifyHavokSetting()
{
	SettingFloat* s = GetSetting(addr::kSettingMaxTime, "fMaxTime:HAVOK");
	if (s) Log("havok fix active (fMaxTime:HAVOK was %.5f)", s->value);
	g_havokState = s ? 1 : -1;
}

static void ApplyMouseSettings()
{
	static const struct { uintptr_t at; const char* name; } kMouse[] = {
		{ addr::kSettingMouseMult,      "fForegroundMouseMult:Controls" },
		{ addr::kSettingMouseBase,      "fForegroundMouseBase:Controls" },
		{ addr::kSettingMouseAccelTop,  "fForegroundMouseAccelTop:Controls" },
		{ addr::kSettingMouseAccelBase, "fForegroundMouseAccelBase:Controls" },
	};
	SettingFloat* found[4];
	for (int i = 0; i < 4; i++) {
		found[i] = GetSetting(kMouse[i].at, kMouse[i].name);
		if (!found[i]) {
			Log("mouse acceleration fix disabled");
			return;
		}
	}
	for (int i = 0; i < 4; i++) {
		Log("%s: %g -> 0", kMouse[i].name, found[i]->value);
		found[i]->value = 0.0f;
	}
	Log("mouse acceleration disabled");
}

static void __thiscall Hook_TimeGlobalUpdate(TimeGlobal* self, uint32_t tickMs)
{
	const bool isMain = (uintptr_t)self == addr::kMainTimeGlobal;
	int64_t& last = LastFor(self);

	double waited = 0;
	if (isMain && g_limitTicks && last) {
		const int64_t before = Now();
		WaitUntil(last + g_limitTicks);
		waited = (double)(Now() - before) / g_qpcFreq;
	}

	const int64_t now = Now();
	const double rawDt = last ? (double)(now - last) / g_qpcFreq : 1.0 / 60.0;
	double dt = rawDt;
	last = now;

	const float timeMult = *(float*)addr::kGlobalTimeMult;

	if (!g_cfg.fixTimer || self->pauseCount || self->fixedFrameMs != 0.0f) {
		// Paused, a fixed timestep requested via iFPSClamp, or timer fix off: keep the engine's
		// own behavior (fixed mode never hits the 10 ms Sleep floor).
		OrigTimeGlobalUpdate(self, tickMs);
		if (self->pauseCount || timeMult <= 0.0f)
			return;
		dt = self->frameDelta / timeMult;
	} else {
		if (dt > g_cfg.maxFrameTime) dt = g_cfg.maxFrameTime;
		if (dt < 1e-5) dt = 1e-5;
		self->lastTimeMs    = tickMs - self->pausedOffsetMs;
		self->zeroDeltaFlag = 0;
		self->frameDelta    = (float)dt * timeMult;
	}

	if (!isMain)
		return;

	static bool firstFrame = true;
	if (firstFrame) {
		firstFrame = false;
		if (g_cfg.noMouseAccel) ApplyMouseSettings();
	}

	if (g_cfg.fixHavok) {
		if (!g_havokState) VerifyHavokSetting();
		if (g_havokState > 0) {
			double step = dt;
			if (step > 1.0 / g_cfg.havokMinFPS) step = 1.0 / g_cfg.havokMinFPS;
			if (step < 1.0 / g_cfg.havokMaxFPS) step = 1.0 / g_cfg.havokMaxFPS;
			((SettingFloat*)addr::kSettingMaxTime)->value = (float)step;
			g_stats.lastHavokStep = (float)step;
		}
	}

	if (g_cfg.debugStats)
		RecordStats(now, dt);
	TraceFrame(now, rawDt, dt, waited);
}

// ---------------------------------------------------------------------------------------------
// Patching

static bool BytesMatch(uintptr_t at, const uint8_t* expect, size_t n)
{
	return memcmp((const void*)at, expect, n) == 0;
}

static void WriteRelJump(uintptr_t at, const void* target)
{
	DWORD old;
	VirtualProtect((void*)at, 5, PAGE_EXECUTE_READWRITE, &old);
	*(uint8_t*)at = 0xE9;
	*(int32_t*)(at + 1) = (int32_t)((uintptr_t)target - (at + 5));
	VirtualProtect((void*)at, 5, old, &old);
	FlushInstructionCache(GetCurrentProcess(), (void*)at, 5);
}

static bool VerifyGame()
{
	// fldz ; sub esp,8 ; push esi ; push edi ; mov edi,[esp+14] ; mov esi,ecx ; sub edi,[esi+14]
	const uint8_t update[] = { 0xD9, 0xEE, 0x83, 0xEC, 0x08, 0x56, 0x57, 0x8B,
	                           0x7C, 0x24, 0x14, 0x8B, 0xF1, 0x2B, 0x7E, 0x14 };
	if (!BytesMatch(addr::kTimeGlobalUpdate, update, sizeof(update))) {
		Log("ERROR: TimeGlobal::Update prologue does not match (wrong Fallout3.exe version?)");
		return false;
	}
	// fmul dword ptr [0x00F7544C] inside TimeGlobal::Update
	const uint8_t mult[] = { 0xD8, 0x0D, 0x4C, 0x54, 0xF7, 0x00 };
	if (!BytesMatch(0x0086C354, mult, sizeof(mult))) {
		Log("ERROR: global time multiplier reference does not match");
		return false;
	}
	// fld dword ptr [0x010F2BE8] in the Havok step scheduler
	const uint8_t fld[] = { 0xD9, 0x05, 0xE8, 0x2B, 0x0F, 0x01 };
	if (!BytesMatch(addr::kHavokReadMaxTime, fld, sizeof(fld))) {
		Log("ERROR: Havok scheduler fMaxTime reference does not match; havok fix disabled");
		g_havokState = -1;
	}
	return true;
}

static bool HookTimeGlobalUpdate()
{
	// Trampoline: the first 5 bytes (fldz ; sub esp,8) are position independent.
	uint8_t* tramp = (uint8_t*)VirtualAlloc(nullptr, 16, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
	if (!tramp) return false;
	memcpy(tramp, (const void*)addr::kTimeGlobalUpdate, 5);
	tramp[5] = 0xE9;
	*(int32_t*)(tramp + 6) = (int32_t)((addr::kTimeGlobalUpdate + 5) - ((uintptr_t)tramp + 10));
	FlushInstructionCache(GetCurrentProcess(), tramp, 16);
	OrigTimeGlobalUpdate = (TimeGlobalUpdate_t)tramp;

	WriteRelJump(addr::kTimeGlobalUpdate, (const void*)&Hook_TimeGlobalUpdate);
	return true;
}

// Redirect one import of the main executable, found by DLL + function name.
static void** FindImport(const char* dll, const char* func)
{
	uint8_t* base = (uint8_t*)GetModuleHandleA(nullptr);
	auto dos = (IMAGE_DOS_HEADER*)base;
	auto nt = (IMAGE_NT_HEADERS*)(base + dos->e_lfanew);
	const IMAGE_DATA_DIRECTORY& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
	if (!dir.VirtualAddress) return nullptr;
	for (auto imp = (IMAGE_IMPORT_DESCRIPTOR*)(base + dir.VirtualAddress); imp->Name; imp++) {
		if (_stricmp((const char*)(base + imp->Name), dll) != 0) continue;
		auto names = (IMAGE_THUNK_DATA*)(base + (imp->OriginalFirstThunk ? imp->OriginalFirstThunk : imp->FirstThunk));
		auto iat = (IMAGE_THUNK_DATA*)(base + imp->FirstThunk);
		for (; names->u1.AddressOfData; names++, iat++) {
			if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
			auto byName = (IMAGE_IMPORT_BY_NAME*)(base + names->u1.AddressOfData);
			if (strcmp((const char*)byName->Name, func) == 0)
				return (void**)&iat->u1.Function;
		}
	}
	return nullptr;
}

static bool PatchImport(void** slot, void* fn)
{
	DWORD old;
	if (!VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) return false;
	*slot = fn;
	VirtualProtect(slot, sizeof(void*), old, &old);
	return true;
}

// ---------------------------------------------------------------------------------------------
// FOSE entry points

static void InitPaths()
{
	HMODULE self = nullptr;
	GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
	                   (LPCSTR)&InitPaths, &self);
	GetModuleFileNameA(self, g_dir, sizeof(g_dir));
	char* slash = strrchr(g_dir, '\\');
	if (slash) slash[1] = '\0';

	char logPath[MAX_PATH];
	snprintf(logPath, sizeof(logPath), "%s" PLUGIN_NAME ".log", g_dir);
	g_log = fopen(logPath, "w");
}

extern "C" {

__declspec(dllexport) bool FOSEPlugin_Query(const FOSEInterface* fose, PluginInfo* info)
{
	info->infoVersion = 1;
	info->name        = PLUGIN_NAME;
	info->version     = PLUGIN_VERSION;

	InitPaths();
	Log(PLUGIN_NAME " " PLUGIN_VERSION_STR ": FOSE %08X, runtime %08X, editor=%u",
	    fose->foseVersion, fose->runtimeVersion, fose->isEditor);
	return !fose->isEditor;
}

__declspec(dllexport) bool FOSEPlugin_Load(const FOSEInterface* fose)
{
	(void)fose;
	LoadConfig();

	if (!VerifyGame()) {
		Log("Plugin disabled; no changes made.");
		return false;
	}

	LARGE_INTEGER freq;
	QueryPerformanceFrequency(&freq);
	g_qpcFreq = (double)freq.QuadPart;
	g_qpcFreqInt = freq.QuadPart;

	if (g_cfg.timerResolution) {
		timeBeginPeriod(1);
		Log("timer resolution set to 1 ms");
	}

	if (g_cfg.fixTickCount) {
		g_tickBase = GetTickCount();
		g_tickQpcBase = Now();
		void** slot = FindImport("KERNEL32.dll", "GetTickCount");
		if (slot && PatchImport(slot, (void*)&Hook_GetTickCount))
			Log("GetTickCount import at %p redirected to 1 ms QPC clock", (void*)slot);
		else
			Log("WARNING: GetTickCount import not found; left unchanged");
	}

	if (g_cfg.fpsLimit > 0.0) {
		g_limitTicks = (int64_t)(g_qpcFreq / g_cfg.fpsLimit);
		g_waitTimer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
		                                     TIMER_ALL_ACCESS);
		if (!g_waitTimer)
			g_waitTimer = CreateWaitableTimerExW(nullptr, nullptr, 0, TIMER_ALL_ACCESS);
		Log("FPS limiter: %.2f fps (%s waitable timer)", g_cfg.fpsLimit,
		    g_waitTimer ? "high-resolution" : "no");
	}

	if (!HookTimeGlobalUpdate()) {
		Log("ERROR: could not allocate trampoline; plugin disabled");
		return false;
	}
	Log("hooked TimeGlobal::Update at %08X (QPC frequency %.0f Hz)", (unsigned)addr::kTimeGlobalUpdate, g_qpcFreq);
	return true;
}

}  // extern "C"
