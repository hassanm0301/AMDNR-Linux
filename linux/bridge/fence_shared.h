/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#include <stdint.h>

/* Fixed x86-64 shared address space: these structs contain no Win32 or HIP
 * objects that the Unix side dereferences. A successful launch transfers one
 * opaque token to PE. Wait observes callback readiness; complete consumes the
 * token. PE must call complete exactly once, including after a COM failure. */
struct amdnr_fence_launch_args {
    void *stream;
    void *token;
    int32_t result;
};
struct amdnr_fence_token_args {
    void *token;
    int32_t result;
};

/* Unix-call transport status is zero on normal dispatch; result is HIP status. */
int32_t amdnr_unix_fence_launch(void *args);
int32_t amdnr_unix_fence_wait(void *args);
int32_t amdnr_unix_fence_complete(void *args);
