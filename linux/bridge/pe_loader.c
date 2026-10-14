/* SPDX-License-Identifier: GPL-3.0-or-later
 * A real PE module preserves Wine's direct Windows syscall dispatch. Native
 * Linux code is reached exclusively through Wine's supported unixcall gate.
 * Loading an old Winelib .dll.so disables syscall dispatch process-wide. */
#include "bridge.h"
#include <unixlib.h>
#include <stdio.h>
#include <stdarg.h>

static BOOL unix_ready;
static INIT_ONCE lifetime_once = INIT_ONCE_STATIC_INIT;

static BOOL CALLBACK pin_module(INIT_ONCE *once, void *parameter, void **context)
{
    HMODULE module;
    (void)once;
    (void)parameter;
    (void)context;
    /* Native HIP workers, PE fence workers and optional exception diagnostics
     * can retain callbacks after a caller releases its LoadLibrary reference.
     * Match their process lifetime before publishing any such callbacks.
     * This runs on first use, outside DllMain and its loader lock. */
    return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                              (LPCWSTR)(uintptr_t)&amdnr_unix_call, &module);
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, void *reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH)
    {
        DisableThreadLibraryCalls(instance);
        if (__wine_init_unix_call()) return FALSE;
        unix_ready = TRUE;
    }
    /* HIP workers and Unix callbacks live until process exit. Never unload
     * native HIP or synchronize the GPU while holding the Windows loader lock. */
    return TRUE;
}

int32_t amdnr_unix_call(unsigned code, void *args)
{
    if (!unix_ready) return (int32_t)0xc0000002; /* STATUS_NOT_IMPLEMENTED */
    if (!InitOnceExecuteOnce(&lifetime_once, pin_module, NULL, NULL))
        return (int32_t)0xc0000142; /* STATUS_DLL_INIT_FAILED */
    amdnr_diagnostics_init();
    return WINE_UNIX_CALL(code, args);
}

void amdnr_log(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    fprintf(stderr, "amdnr-hip: ");
    vfprintf(stderr, format, args);
    fputc('\n', stderr);
    va_end(args);
}
