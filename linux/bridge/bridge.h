/* SPDX-License-Identifier: GPL-3.0-or-later
 * Linux ROCm bridge for the modified AMDNR fork. */
#pragma once
#define COBJMACROS
#define __HIP_PLATFORM_AMD__
#include <windows.h>
#include <winternl.h>
#include <hip/hip_runtime_api.h>
#include <pthread.h>
#include <stdint.h>

void *amdnr_symbol(const char *name);
void amdnr_log(const char *format, ...);
void amdnr_diagnostics_init(void);
int amdnr_vulkan_fd(HANDLE handle, uint64_t size);
hipError_t amdnr_fix_identity(hipDeviceProp_t *properties, int ordinal);
hipError_t amdnr_fence_import(hipExternalSemaphore_t *out, HANDLE handle);
hipError_t amdnr_fence_destroy(hipExternalSemaphore_t semaphore);
hipError_t amdnr_fence_submit(const hipExternalSemaphore_t *semaphores,
                            const void *parameters, unsigned count, hipStream_t stream, int signal);
hipError_t amdnr_stream_finish(hipStream_t stream, int destroy);

#define NATIVE_FN(name) __typeof__(&name) name##_native = (__typeof__(&name))amdnr_symbol(#name)
#define REQUIRE_FN(name) NATIVE_FN(name); if (!name##_native) return hipErrorNotSupported
