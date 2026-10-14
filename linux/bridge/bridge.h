/* SPDX-License-Identifier: GPL-3.0-or-later
 * Linux ROCm bridge for the modified AMDNR fork. */
#pragma once
#define COBJMACROS
#include <windows.h>
#include <winternl.h>
#include "hip_types.h"
#include "native_api.h"

int32_t amdnr_unix_call(unsigned code, void *args);
void amdnr_log(const char *format, ...);
void amdnr_diagnostics_init(void);
int amdnr_vulkan_fd(HANDLE handle, uint64_t size);
hipError_t amdnr_fix_identity(hipDeviceProp_t *properties, int ordinal);
hipError_t amdnr_fence_import(hipExternalSemaphore_t *out, HANDLE handle);
hipError_t amdnr_fence_destroy(hipExternalSemaphore_t semaphore);
hipError_t amdnr_fence_submit(const hipExternalSemaphore_t *semaphores,
                            const void *parameters, unsigned count, hipStream_t stream, int signal);
hipError_t amdnr_stream_finish(hipStream_t stream, int destroy);
