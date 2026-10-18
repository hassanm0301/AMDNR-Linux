// SPDX-License-Identifier: GPL-3.0-or-later
// CPU-only forwarding probe: no D3D device, game, network request or HIP work.
#include <windows.h>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <vector>
#if PROXY_KIND == 1
#include <mmsystem.h>
#define PROXY_NAME L"winmm.dll"
#elif PROXY_KIND == 2
#include <winver.h>
#define PROXY_NAME L"version.dll"
#elif PROXY_KIND == 3
#include <dbghelp.h>
#define PROXY_NAME L"dbghelp.dll"
#elif PROXY_KIND == 4
#include <wininet.h>
#define PROXY_NAME L"wininet.dll"
#elif PROXY_KIND == 5
#include <winhttp.h>
#define PROXY_NAME L"winhttp.dll"
#else
#error Select a PROXY_KIND from 1 through 5
#endif

#define CHECK(condition) do { if (!(condition)) { \
    std::fprintf(stderr, "FAIL: %s (Win32 error %lu)\n", #condition, GetLastError()); return 1; } } while (0)
#ifdef PROXY_STATIC
#define PROC(name) auto call_##name = &name
#define LOAD_MODE "static-import"
#else
#define PROC(name) auto call_##name = reinterpret_cast<decltype(&name)>( \
    reinterpret_cast<void *>(GetProcAddress(module, #name))); CHECK(call_##name)
#define LOAD_MODE "explicit-load"
#endif

int main()
{
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    std::printf("START: proxy kind %d, %s\n", PROXY_KIND, LOAD_MODE);
#ifdef PROXY_STATIC
    HMODULE module = GetModuleHandleW(PROXY_NAME);
#else
    HMODULE module = LoadLibraryW(PROXY_NAME);
#endif
    CHECK(module);
    // A builtin fallback does not establish that AMDNR was successfully loaded.
    CHECK(GetProcAddress(module, "NVSDK_NGX_D3D12_Init"));
    wchar_t module_path[32768]{};
    CHECK(GetModuleFileNameW(module, module_path, 32768));
    std::wprintf(L"HOST: %ls\n", module_path);

#if PROXY_KIND == 1
    PROC(timeGetTime); PROC(timeGetDevCaps);
    TIMECAPS capabilities{};
    CHECK(call_timeGetDevCaps(&capabilities, sizeof(capabilities)) == TIMERR_NOERROR);
    CHECK(capabilities.wPeriodMin > 0 && capabilities.wPeriodMin <= capabilities.wPeriodMax);
    DWORD start = call_timeGetTime();
    Sleep(20);
    CHECK(call_timeGetTime() - start >= 10);
#elif PROXY_KIND == 2
    PROC(GetFileVersionInfoSizeW); PROC(GetFileVersionInfoW); PROC(VerQueryValueW);
    wchar_t system_path[32768]{};
    UINT length = GetSystemDirectoryW(system_path, 32768);
    CHECK(length && length < 32740);
    std::wcscat(system_path, L"\\kernel32.dll");
    DWORD ignored = 0, size = call_GetFileVersionInfoSizeW(system_path, &ignored);
    CHECK(size > 0 && size < 16 * 1024 * 1024);
    std::vector<BYTE> data(size);
    CHECK(call_GetFileVersionInfoW(system_path, 0, size, data.data()));
    VS_FIXEDFILEINFO *version = nullptr;
    UINT version_size = 0;
    CHECK(call_VerQueryValueW(data.data(), L"\\", reinterpret_cast<void **>(&version), &version_size));
    CHECK(version && version_size >= sizeof(*version) && version->dwSignature == 0xfeef04bd);
#elif PROXY_KIND == 3
    PROC(SymGetOptions); PROC(SymSetOptions); PROC(UnDecorateSymbolName);
    DWORD original = call_SymGetOptions();
    DWORD requested = SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS;
    CHECK(call_SymSetOptions(requested) == requested);
    CHECK(call_SymGetOptions() == requested);
    char undecorated[1024]{};
    CHECK(call_UnDecorateSymbolName("?proxy_probe@@YAHH@Z", undecorated, sizeof(undecorated), UNDNAME_COMPLETE));
    CHECK(std::strstr(undecorated, "proxy_probe"));
    call_SymSetOptions(original);
#elif PROXY_KIND == 4
    PROC(InternetCrackUrlW); PROC(InternetOpenW); PROC(InternetCloseHandle);
    wchar_t host[128]{}, path[128]{};
    URL_COMPONENTSW parts{};
    parts.dwStructSize = sizeof(parts);
    parts.lpszHostName = host; parts.dwHostNameLength = 128;
    parts.lpszUrlPath = path; parts.dwUrlPathLength = 128;
    CHECK(call_InternetCrackUrlW(L"https://example.invalid:8443/proxy/check", 0, 0, &parts));
    CHECK(!std::wcscmp(host, L"example.invalid") && !std::wcscmp(path, L"/proxy/check") && parts.nPort == 8443);
    // Opening a session creates local state; no connection or request is made.
    HINTERNET session = call_InternetOpenW(L"AMDNR proxy probe", INTERNET_OPEN_TYPE_DIRECT, nullptr, nullptr, 0);
    CHECK(session); CHECK(call_InternetCloseHandle(session));
#elif PROXY_KIND == 5
    PROC(WinHttpCrackUrl); PROC(WinHttpOpen); PROC(WinHttpCloseHandle);
    wchar_t host[128]{}, path[128]{};
    URL_COMPONENTS parts{};
    parts.dwStructSize = sizeof(parts);
    parts.lpszHostName = host; parts.dwHostNameLength = 128;
    parts.lpszUrlPath = path; parts.dwUrlPathLength = 128;
    CHECK(call_WinHttpCrackUrl(L"https://example.invalid:8443/proxy/check", 0, 0, &parts));
    CHECK(!std::wcscmp(host, L"example.invalid") && !std::wcscmp(path, L"/proxy/check") && parts.nPort == 8443);
    HINTERNET session = call_WinHttpOpen(L"AMDNR proxy probe", WINHTTP_ACCESS_TYPE_NO_PROXY, nullptr, nullptr, 0);
    CHECK(session); CHECK(call_WinHttpCloseHandle(session));
#endif
    std::printf("PASS: proxy kind %d %s loaded AMDNR and forwarded representative APIs\n", PROXY_KIND, LOAD_MODE);
    return 0;
}
