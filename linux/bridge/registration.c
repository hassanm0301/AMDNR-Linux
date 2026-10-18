/* SPDX-License-Identifier: GPL-3.0-or-later
 * hipcc-compiled modules (danielblnc's runtime) register their embedded code
 * objects, kernels and device variables from static constructors, before any
 * HIP API call. The wrapper, its offload bundle, kernel names and host stub
 * addresses all live in this process's memory, which the native runtime
 * shares, so registration forwards the pointers unchanged. The bundle's
 * amdgcn code objects are the same for Windows and Linux. */
#include "bridge.h"

/* clang's HIP fat-binary wrapper: magic "HIPF", version 1, offload bundle. */
struct fat_binary_wrapper { uint32_t magic, version; const void *binary; void *reserved; };

void ** WINAPI amdnr___hipRegisterFatBinary(const void *data)
{
    const struct fat_binary_wrapper *wrapper = data;
    void **modules;
    if (!wrapper || wrapper->magic != 0x48495046 || wrapper->version != 1 || !wrapper->binary)
    {
        amdnr_log("rejected an unrecognized HIP fat binary registration at %p", data);
        return NULL;
    }
    modules = amdnr_native___hipRegisterFatBinary(data);
    if (!modules) amdnr_log("Linux HIP did not register the fat binary at %p", data);
    return modules;
}

void WINAPI amdnr___hipRegisterFunction(void **modules, const void *host_function, char *device_function,
                                        const char *device_name, unsigned thread_limit, void *tid, void *bid,
                                        dim3 *block, dim3 *grid, int *warp_size)
{
    /* A rejected fat binary has no handle; its launches then fail with an error. */
    if (!modules || !host_function || !device_name) return;
    amdnr_native___hipRegisterFunction(modules, host_function, device_function, device_name,
                                       thread_limit, tid, bid, block, grid, warp_size);
}

void WINAPI amdnr___hipRegisterVar(void **modules, void *variable, char *host_name, char *device_name,
                                   int external, size_t size, int constant, int global)
{
    if (!modules || !variable || !device_name) return;
    amdnr_native___hipRegisterVar(modules, variable, host_name, device_name, external, size, constant, global);
}

void WINAPI amdnr___hipUnregisterFatBinary(void **modules)
{
    /* Modules unregister from their CRT teardown. At process exit Wine has
     * already ended the other Windows threads, possibly inside HIP calls, so
     * native unregistration could wait on locks they held. The exiting
     * process releases the code objects; a module unloaded earlier still
     * unregisters normally. */
    if (!modules || RtlDllShutdownInProgress()) return;
    amdnr_native___hipUnregisterFatBinary(modules);
}
