/* SPDX-License-Identifier: GPL-3.0-or-later
 * D3D12 fences are virtual timelines in Wine. Importing their raw fd as a HIP
 * timeline would use the wrong values. Preserve D3D12 semantics on a Wine
 * thread, ordered with the HIP stream through native host callbacks. */
#include "bridge.h"
#include <dxgi1_4.h>
#include <initguid.h>
#include <d3d12.h>
#include <stdlib.h>

struct fence {
    ID3D12Fence *object;
    unsigned references;
    struct fence *next;
};
struct worker;
struct job {
    struct worker *worker;
    struct fence *fence;
    uint64_t value;
    int signal, ready, finished;
    struct job *next;
};
struct worker {
    hipStream_t stream;
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    HANDLE thread;
    struct job *head, *tail;
    int stop;
    /* Protected by registry. Destroy closes admission before draining calls. */
    unsigned active_calls;
    int closing;
    hipError_t error;
    struct worker *next;
};
static pthread_mutex_t registry = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t registry_changed = PTHREAD_COND_INITIALIZER;
static struct fence *fences;
static struct worker *workers;

/* Called with registry held. Keep an entry even before its first fence job so
 * synchronize/destroy cannot race the creation of a worker for the stream. */
static struct worker *get_worker_locked(hipStream_t stream)
{
    struct worker *worker = workers;
    while (worker && worker->stream != stream) worker = worker->next;
    if (worker) return worker;
    worker = calloc(1, sizeof(*worker));
    if (!worker) return NULL;
    worker->stream = stream;
    if (pthread_mutex_init(&worker->mutex, NULL)) { free(worker); return NULL; }
    if (pthread_cond_init(&worker->condition, NULL))
    { pthread_mutex_destroy(&worker->mutex); free(worker); return NULL; }
    worker->next = workers;
    workers = worker;
    return worker;
}

static void finish_worker_call(struct worker *worker)
{
    pthread_mutex_lock(&registry);
    --worker->active_calls;
    pthread_cond_broadcast(&registry_changed);
    pthread_mutex_unlock(&registry);
}

static void release_fence(struct fence *fence)
{
    pthread_mutex_lock(&registry);
    unsigned remaining = --fence->references;
    pthread_mutex_unlock(&registry);
    if (!remaining) { ID3D12Fence_Release(fence->object); free(fence); }
}

static DWORD WINAPI worker_main(void *argument)
{
    struct worker *worker = argument;
    HANDLE event = CreateEventW(NULL, FALSE, FALSE, NULL);
    pthread_mutex_lock(&worker->mutex);
    for (;;)
    {
        while (!worker->head && !worker->stop)
            pthread_cond_wait(&worker->condition, &worker->mutex);
        if (!worker->head) break;
        struct job *job = worker->head;
        while (!job->ready) pthread_cond_wait(&worker->condition, &worker->mutex);
        worker->head = job->next;
        if (!worker->head) worker->tail = NULL;
        pthread_mutex_unlock(&worker->mutex);

        HRESULT result = E_FAIL;
        if (job->signal)
            result = ID3D12Fence_Signal(job->fence->object, job->value);
        else if (event)
        {
            result = ID3D12Fence_SetEventOnCompletion(job->fence->object, job->value, event);
            if (SUCCEEDED(result) && WaitForSingleObject(event, INFINITE) != WAIT_OBJECT_0)
                result = E_FAIL;
        }
        release_fence(job->fence);
        pthread_mutex_lock(&worker->mutex);
        if (FAILED(result))
        {
            amdnr_log("D3D12 fence %s failed: %#x", job->signal ? "signal" : "wait", (unsigned)result);
            worker->error = hipErrorUnknown;
        }
        job->finished = 1;
        pthread_cond_broadcast(&worker->condition);
        /* The native callback owns and frees job after this notification. */
    }
    pthread_mutex_unlock(&worker->mutex);
    if (event) CloseHandle(event);
    return 0;
}

/* Called on a ROCm pthread: no Win32 or HIP API calls are permitted here. */
static void native_callback(void *argument)
{
    struct job *job = argument;
    struct worker *worker = job->worker;
    pthread_mutex_lock(&worker->mutex);
    job->ready = 1;
    pthread_cond_broadcast(&worker->condition);
    while (!job->finished) pthread_cond_wait(&worker->condition, &worker->mutex);
    pthread_mutex_unlock(&worker->mutex);
    free(job);
}

hipError_t amdnr_fence_import(hipExternalSemaphore_t *out, HANDLE handle)
{
    REQUIRE_FN(hipGetDevice);
    NATIVE_FN(hipGetDevicePropertiesR0600);
    /* hipGetDeviceProperties is an SDK macro; always request the versioned ABI. */
    hipGetDevicePropertiesR0600_native = (__typeof__(&hipGetDeviceProperties))amdnr_symbol("hipGetDevicePropertiesR0600");
    if (!hipGetDevicePropertiesR0600_native) return hipErrorNotSupported;
    int ordinal = 0;
    hipDeviceProp_t properties;
    hipError_t error = hipGetDevice_native(&ordinal);
    if (!error) error = hipGetDevicePropertiesR0600_native(&properties, ordinal);
    if (!error) error = amdnr_fix_identity(&properties, ordinal);
    if (error) return error;
    IDXGIFactory4 *factory = NULL;
    IDXGIAdapter1 *adapter = NULL;
    ID3D12Device *device = NULL;
    LUID luid;
    memcpy(&luid, properties.luid, sizeof(luid));
    HRESULT hr = CreateDXGIFactory1(&IID_IDXGIFactory4, (void **)&factory);
    if (SUCCEEDED(hr)) hr = IDXGIFactory4_EnumAdapterByLuid(factory, luid, &IID_IDXGIAdapter1, (void **)&adapter);
    if (SUCCEEDED(hr)) hr = D3D12CreateDevice((IUnknown *)adapter, D3D_FEATURE_LEVEL_11_0, &IID_ID3D12Device, (void **)&device);
    struct fence *fence = calloc(1, sizeof(*fence));
    if (!fence) hr = E_OUTOFMEMORY;
    if (SUCCEEDED(hr)) hr = ID3D12Device_OpenSharedHandle(device, handle, &IID_ID3D12Fence, (void **)&fence->object);
    if (device) ID3D12Device_Release(device);
    if (adapter) IDXGIAdapter1_Release(adapter);
    if (factory) IDXGIFactory4_Release(factory);
    if (FAILED(hr))
    {
        amdnr_log("cannot open D3D12 fence: %#x", (unsigned)hr);
        free(fence);
        return hr == E_OUTOFMEMORY ? hipErrorOutOfMemory : hipErrorInvalidHandle;
    }
    fence->references = 1;
    pthread_mutex_lock(&registry);
    fence->next = fences;
    fences = fence;
    pthread_mutex_unlock(&registry);
    *out = (hipExternalSemaphore_t)fence;
    return hipSuccess;
}

hipError_t amdnr_fence_destroy(hipExternalSemaphore_t semaphore)
{
    pthread_mutex_lock(&registry);
    struct fence **entry = &fences;
    while (*entry && (void *)*entry != (void *)semaphore) entry = &(*entry)->next;
    struct fence *fence = *entry;
    if (fence) *entry = fence->next;
    pthread_mutex_unlock(&registry);
    if (!fence) return hipErrorInvalidHandle;
    release_fence(fence);
    return hipSuccess;
}

hipError_t amdnr_fence_submit(const hipExternalSemaphore_t *semaphores, const void *parameters,
                            unsigned count, hipStream_t stream, int signal)
{
    if (!count) return hipSuccess;
    if (!semaphores || !parameters) return hipErrorInvalidValue;
    REQUIRE_FN(hipLaunchHostFunc);
    REQUIRE_FN(hipStreamIsCapturing);
    hipError_t result = hipSuccess;

    /* Validate the entire batch before submitting any side effects. */
    struct job **batch = calloc(count, sizeof(*batch));
    if (!batch) return hipErrorOutOfMemory;
    pthread_mutex_lock(&registry);
    for (unsigned i = 0; i < count; i++)
    {
        const hipExternalSemaphoreSignalParams *sp = parameters;
        const hipExternalSemaphoreWaitParams *wp = parameters;
        if ((signal ? sp[i].flags : wp[i].flags)) { result = hipErrorInvalidValue; break; }
        struct fence *f = fences;
        while (f && (void *)f != (void *)semaphores[i]) f = f->next;
        if (!f) { result = hipErrorInvalidHandle; break; }
        batch[i] = calloc(1, sizeof(**batch));
        if (!batch[i]) { result = hipErrorOutOfMemory; break; }
        f->references++;
        batch[i]->fence = f;
        batch[i]->value = signal ? sp[i].params.fence.value : wp[i].params.fence.value;
        batch[i]->signal = signal;
    }
    struct worker *worker = NULL;
    if (!result)
    {
        worker = get_worker_locked(stream);
        if (!worker) result = hipErrorOutOfMemory;
        else if (worker->closing) { worker = NULL; result = hipErrorInvalidHandle; }
        else ++worker->active_calls;
    }
    pthread_mutex_unlock(&registry);
    if (!result)
    {
        hipStreamCaptureStatus capture;
        result = hipStreamIsCapturing_native(stream, &capture);
        if (!result && capture != hipStreamCaptureStatusNone) result = hipErrorStreamCaptureUnsupported;
    }
    for (unsigned i = 0; i < count; i++)
    {
        struct job *job = batch[i];
        if (!job) continue;
        if (!result)
        {
            job->worker = worker;
            pthread_mutex_lock(&worker->mutex);
            result = worker->error;
            if (!result && !worker->thread)
            {
                worker->thread = CreateThread(NULL, 0, worker_main, worker, 0, NULL);
                if (!worker->thread) result = hipErrorOutOfMemory;
            }
            if (!result) result = hipLaunchHostFunc_native(stream, native_callback, job);
            if (!result)
            {
                if (worker->tail) worker->tail->next = job;
                else worker->head = job;
                worker->tail = job;
                pthread_cond_broadcast(&worker->condition);
            }
            pthread_mutex_unlock(&worker->mutex);
        }
        if (result) { release_fence(job->fence); free(job); }
    }
    free(batch);
    if (worker) finish_worker_call(worker);
    return result;
}

hipError_t amdnr_stream_finish(hipStream_t stream, int destroy)
{
    REQUIRE_FN(hipStreamSynchronize);
    NATIVE_FN(hipStreamDestroy);
    if (destroy && !hipStreamDestroy_native) return hipErrorNotSupported;
    pthread_mutex_lock(&registry);
    struct worker *worker = get_worker_locked(stream);
    if (!worker) { pthread_mutex_unlock(&registry); return hipErrorOutOfMemory; }
    if (worker->closing) { pthread_mutex_unlock(&registry); return hipErrorInvalidHandle; }
    if (destroy)
    {
        worker->closing = 1;
        /* The condition wait drops registry. Submitters and callbacks may need
         * it to release fences; never hold either mutex across a HIP drain. */
        while (worker->active_calls) pthread_cond_wait(&registry_changed, &registry);
    }
    else ++worker->active_calls;
    pthread_mutex_unlock(&registry);

    hipError_t result = hipStreamSynchronize_native(stream);
    if (!destroy)
    {
        if (!result)
        {
            pthread_mutex_lock(&worker->mutex);
            result = worker->error;
            pthread_mutex_unlock(&worker->mutex);
        }
        finish_worker_call(worker);
        return result;
    }
    if (!result) result = hipStreamDestroy_native(stream);
    if (result)
    {
        /* A failed drain may leave native callbacks using this worker. Retain
         * it and permit a later retry; freeing it here would be a use-after-free. */
        pthread_mutex_lock(&registry);
        worker->closing = 0;
        pthread_cond_broadcast(&registry_changed);
        pthread_mutex_unlock(&registry);
        return result;
    }

    pthread_mutex_lock(&worker->mutex);
    result = worker->error;
    worker->stop = 1;
    pthread_cond_broadcast(&worker->condition);
    pthread_mutex_unlock(&worker->mutex);
    if (worker->thread)
    {
        WaitForSingleObject(worker->thread, INFINITE);
        CloseHandle(worker->thread);
    }
    pthread_mutex_lock(&registry);
    struct worker **entry = &workers;
    while (*entry != worker) entry = &(*entry)->next;
    *entry = worker->next;
    pthread_mutex_unlock(&registry);
    pthread_cond_destroy(&worker->condition);
    pthread_mutex_destroy(&worker->mutex);
    free(worker);
    return result;
}
