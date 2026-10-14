/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#define __HIP_PLATFORM_AMD__
#include <hip/hip_runtime_api.h>
#include <stdint.h>
#include <stddef.h>
/* PE and Unix halves share only fixed x86-64 HIP ABI structures and pointers. */
_Static_assert(sizeof(void *) == 8, "The bridge requires x86-64");
_Static_assert(sizeof(hipDeviceProp_t) == 1472, "HIP R0600 ABI changed");
_Static_assert(offsetof(hipDeviceProp_t, luid) == 272, "HIP LUID ABI changed");
_Static_assert(offsetof(hipDeviceProp_t, gcnArchName) == 1160, "HIP architecture ABI changed");
_Static_assert(sizeof(hipExternalMemoryHandleDesc) == 104, "HIP memory ABI changed");
_Static_assert(sizeof(hipExternalSemaphoreSignalParams) == 144, "HIP fence ABI changed");
_Static_assert(sizeof(hipExternalSemaphoreWaitParams) == 144, "HIP fence ABI changed");
