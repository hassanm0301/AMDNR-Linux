/* SPDX-License-Identifier: GPL-3.0-or-later
 * ROCm callbacks execute on native pthreads. They must never call Win32/COM or
 * re-enter HIP. The PE worker owns all Direct3D work and acknowledges it through
 * these Unix-call endpoints while the callback holds the HIP stream in order. */
#define __HIP_PLATFORM_AMD__
#include <hip/hip_runtime_api.h>
#include <pthread.h>
#include <stdlib.h>
#include "fence_shared.h"

extern void *amdnr_symbol(const char *name);

struct native_token {
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    int ready, finished;
};

static void native_callback(void *argument)
{
    struct native_token *token = argument;
    pthread_mutex_lock(&token->mutex);
    token->ready = 1;
    pthread_cond_broadcast(&token->condition);
    while (!token->finished) pthread_cond_wait(&token->condition, &token->mutex);
    pthread_mutex_unlock(&token->mutex);
    /* complete has relinquished PE ownership before unlocking. Nothing on PE
     * may touch this token now; returning lets the stream execute its next job. */
    pthread_cond_destroy(&token->condition);
    pthread_mutex_destroy(&token->mutex);
    free(token);
}

int32_t amdnr_unix_fence_launch(void *arguments)
{
    struct amdnr_fence_launch_args *args = arguments;
    if (!args) return (int32_t)0xc000000d; /* STATUS_INVALID_PARAMETER */
    args->token = NULL;
    __typeof__(&hipLaunchHostFunc) launch = (__typeof__(&hipLaunchHostFunc))amdnr_symbol("hipLaunchHostFunc");
    if (!launch) { args->result = hipErrorNotSupported; return 0; }
    struct native_token *token = calloc(1, sizeof(*token));
    if (!token) { args->result = hipErrorOutOfMemory; return 0; }
    if (pthread_mutex_init(&token->mutex, NULL)) {
        free(token); args->result = hipErrorOutOfMemory; return 0;
    }
    if (pthread_cond_init(&token->condition, NULL)) {
        pthread_mutex_destroy(&token->mutex);
        free(token); args->result = hipErrorOutOfMemory; return 0;
    }
    args->result = launch((hipStream_t)args->stream, native_callback, token);
    if (args->result) {
        /* A failed HIP enqueue did not transfer ownership to a callback. */
        pthread_cond_destroy(&token->condition);
        pthread_mutex_destroy(&token->mutex);
        free(token);
    } else {
        /* The callback may already be waiting. It cannot free the token until
         * the PE worker explicitly completes it after launch has returned. */
        args->token = token;
    }
    return 0;
}

int32_t amdnr_unix_fence_wait(void *arguments)
{
    struct amdnr_fence_token_args *args = arguments;
    if (!args) return (int32_t)0xc000000d;
    struct native_token *token = args->token;
    if (!token) { args->result = hipErrorInvalidHandle; return 0; }
    pthread_mutex_lock(&token->mutex);
    while (!token->ready) pthread_cond_wait(&token->condition, &token->mutex);
    pthread_mutex_unlock(&token->mutex);
    args->result = hipSuccess;
    return 0;
}

int32_t amdnr_unix_fence_complete(void *arguments)
{
    struct amdnr_fence_token_args *args = arguments;
    if (!args) return (int32_t)0xc000000d;
    struct native_token *token = args->token;
    if (!token) { args->result = hipErrorInvalidHandle; return 0; }
    pthread_mutex_lock(&token->mutex);
    token->finished = 1;
    pthread_cond_broadcast(&token->condition);
    pthread_mutex_unlock(&token->mutex);
    /* The callback can free token immediately after unlock: do not inspect it
     * or call any synchronization operation on it beyond this point. */
    args->result = hipSuccess;
    return 0;
}
