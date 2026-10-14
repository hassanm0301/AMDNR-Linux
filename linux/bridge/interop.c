/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "bridge.h"
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

_Static_assert(sizeof(void *) == 8, "The bridge requires x86-64");
_Static_assert(sizeof(hipDeviceProp_t) == 1472, "HIP R0600 ABI changed");
_Static_assert(offsetof(hipDeviceProp_t, luid) == 272, "HIP LUID ABI changed");
_Static_assert(offsetof(hipDeviceProp_t, gcnArchName) == 1160, "HIP architecture ABI changed");
_Static_assert(sizeof(hipExternalMemoryHandleDesc) == 104, "HIP memory ABI changed");
_Static_assert(sizeof(hipExternalSemaphoreSignalParams) == 144, "HIP fence ABI changed");
_Static_assert(sizeof(hipExternalSemaphoreWaitParams) == 144, "HIP fence ABI changed");

hipError_t WINAPI amdnr_hipGetDevicePropertiesR0600(hipDeviceProp_t *out, int device)
{
    if (!out) return hipErrorInvalidValue;
    REQUIRE_FN(hipGetDevicePropertiesR0600);
    hipDeviceProp_t native;
    hipError_t result = hipGetDevicePropertiesR0600_native(&native, device);
    if (result) return result;
    result = amdnr_fix_identity(&native, device);
    if (result) return result;
    memcpy(out, &native, sizeof(native));
    return hipSuccess;
}

hipError_t WINAPI amdnr_hipDeviceGetName(char *out, int length, int device)
{
    if (!out || length <= 0) return hipErrorInvalidValue;
    hipDeviceProp_t properties;
    hipError_t result = amdnr_hipGetDevicePropertiesR0600(&properties, device);
    if (!result) { strncpy(out, properties.name, length - 1); out[length - 1] = 0; }
    return result;
}

hipError_t WINAPI amdnr_hipImportExternalMemory(hipExternalMemory_t *out,
                                             const hipExternalMemoryHandleDesc *description)
{
    if (!out || !description || !description->size) return hipErrorInvalidValue;
    *out = NULL;
    REQUIRE_FN(hipImportExternalMemory);
    hipExternalMemoryHandleDesc native = *description;
    switch (description->type)
    {
    case hipExternalMemoryHandleTypeOpaqueWin32:
    case hipExternalMemoryHandleTypeD3D12Heap:
    case hipExternalMemoryHandleTypeD3D12Resource:
        if (!description->handle.win32.handle || description->handle.win32.name)
            return hipErrorInvalidValue;
        break;
    default:
        return hipErrorNotSupported;
    }
    if (getenv("AMDNR_HIP_TRACE")) amdnr_log("memory handle=%p type=%d size=%llu flags=%u", description->handle.win32.handle, description->type, description->size, description->flags);
    int fd = amdnr_vulkan_fd(description->handle.win32.handle, description->size);

    if (fd < 0) return hipErrorInvalidHandle;
    memset(&native.handle, 0, sizeof(native.handle));
    native.type = hipExternalMemoryHandleTypeOpaqueFd;
    native.handle.fd = fd;
    hipError_t result = hipImportExternalMemory_native(out, &native);
    /* ROCm duplicates the imported descriptor; unlike Vulkan it does not consume it. */
    close(fd);
    if (result) amdnr_log("HIP memory import failed: %d", result);
    return result;
}

hipError_t WINAPI amdnr_hipImportExternalSemaphore(hipExternalSemaphore_t *out,
                                                const hipExternalSemaphoreHandleDesc *description)
{
    if (!out || !description) return hipErrorInvalidValue;
    *out = NULL;
    if (description->type != hipExternalSemaphoreHandleTypeD3D12Fence)
        return hipErrorNotSupported;
    if (!description->handle.win32.handle || description->handle.win32.name || description->flags)
        return hipErrorInvalidValue;
    return amdnr_fence_import(out, description->handle.win32.handle);
}

hipError_t WINAPI amdnr_hipDestroyExternalSemaphore(hipExternalSemaphore_t semaphore)
{ return amdnr_fence_destroy(semaphore); }

hipError_t WINAPI amdnr_hipSignalExternalSemaphoresAsync(const hipExternalSemaphore_t *semaphores,
    const hipExternalSemaphoreSignalParams *parameters, unsigned count, hipStream_t stream)
{ return amdnr_fence_submit(semaphores, parameters, count, stream, 1); }

hipError_t WINAPI amdnr_hipWaitExternalSemaphoresAsync(const hipExternalSemaphore_t *semaphores,
    const hipExternalSemaphoreWaitParams *parameters, unsigned count, hipStream_t stream)
{ return amdnr_fence_submit(semaphores, parameters, count, stream, 0); }

hipError_t WINAPI amdnr_hipStreamSynchronize(hipStream_t stream)
{ return amdnr_stream_finish(stream, 0); }

hipError_t WINAPI amdnr_hipStreamDestroy(hipStream_t stream)
{ return amdnr_stream_finish(stream, 1); }

hipError_t WINAPI amdnr_hipModuleLoad(hipModule_t *out, const char *path)
{
    if (!out || !path) return hipErrorInvalidValue;
    *out = NULL;
    REQUIRE_FN(hipModuleLoadData);
    /* Read with Win32, so C:\\, Z:\\, relative paths, and non-ASCII paths use
     * exactly the same namespace as the Windows neural runtime. */
    HANDLE file = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) return hipErrorFileNotFound;
    LARGE_INTEGER size;
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 || size.QuadPart > UINT32_MAX)
    { CloseHandle(file); return hipErrorInvalidValue; }
    void *data = malloc((size_t)size.QuadPart);
    if (!data) { CloseHandle(file); return hipErrorOutOfMemory; }
    DWORD read = 0;
    BOOL ok = ReadFile(file, data, (DWORD)size.QuadPart, &read, NULL);
    CloseHandle(file);
    hipError_t result = ok && read == size.QuadPart ? hipModuleLoadData_native(out, data) : hipErrorInvalidValue;
    free(data);
    return result;
}
