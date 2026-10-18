/* SPDX-License-Identifier: GPL-3.0-or-later
 * Stand-in for a hipcc-compiled DLL such as danielblnc's runtime: registers
 * its offload bundle, kernel and variables from a static constructor during
 * DLL_PROCESS_ATTACH (under the loader lock) and unregisters from its
 * destructor, as compiler-generated code does. */
#include "hip_registration.h"

static unsigned scale_shadow, launches_shadow;
static void kernel_stub(void) {}
static struct fat_binary wrapper;
static struct registered_module module = {NULL, (const void *)kernel_stub, &scale_shadow, &launches_shadow};
static HMODULE hip;

__attribute__((constructor)) static void register_at_load(void)
{
    char path[MAX_PATH];
    DWORD length = GetEnvironmentVariableA("AMDNR_TEST_BUNDLE", path, sizeof(path));
    hip = GetModuleHandleW(L"amdhip64_7.dll");
    if (!hip || !length || length >= sizeof(path)) return;
    wrapper = (struct fat_binary){FAT_BINARY_MAGIC, 1, read_bundle(path), NULL};
    if (wrapper.binary && !register_module(hip, &wrapper, &module)) module.handle = NULL;
}

__attribute__((destructor)) static void unregister_at_unload(void)
{
    unregister_fat_binary_fn unregister = hip ? (unregister_fat_binary_fn)(void *)GetProcAddress(
        hip, "__hipUnregisterFatBinary") : NULL;
    if (unregister && module.handle) unregister(module.handle);
}

__declspec(dllexport) const struct registered_module *registered_module(void)
{
    return module.handle ? &module : NULL;
}
