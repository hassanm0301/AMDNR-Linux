/* SPDX-License-Identifier: GPL-3.0-or-later
 * D3D12 fences are virtual timelines in Wine. Importing their raw fd as a HIP
 * timeline would use the wrong values. Preserve D3D12 semantics on a Wine
 * thread, ordered with the HIP stream through native host callbacks.
 * All Win32/COM work stays in this PE module. The Unix side sees only an
 * opaque synchronization token and never calls into this worker. */
#include "bridge.h"
#include "fence_shared.h"
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
    void *token;
    struct fence *fence;
    uint64_t value;
    int signal;
    struct job *next;
};
struct worker {
    hipStream_t stream;
    SRWLOCK mutex;
    CONDITION_VARIABLE condition;
    HANDLE thread;
    struct job *head, *tail;
    int stop;
    /* Protected by registry. Destroy closes admission before draining calls. */
    unsigned active_calls;
    int closing;
    hipError_t error;
    struct worker *next;
};
static SRWLOCK registry = SRWLOCK_INIT;
static CONDITION_VARIABLE registry_changed = CONDITION_VARIABLE_INIT;
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
    InitializeSRWLock(&worker->mutex);
    InitializeConditionVariable(&worker->condition);
    worker->next = workers;
    workers = worker;
    return worker;
}

static void finish_worker_call(struct worker *worker)
{
    AcquireSRWLockExclusive(&registry);
    --worker->active_calls;
    WakeAllConditionVariable(&registry_changed);
    ReleaseSRWLockExclusive(&registry);
}

static void release_fence(struct fence *fence)
{
    AcquireSRWLockExclusive(&registry);
    unsigned remaining = --fence->references;
    ReleaseSRWLockExclusive(&registry);
    if (!remaining) { ID3D12Fence_Release(fence->object); free(fence); }
}

static DWORD WINAPI worker_main(void *argument)
{
    struct worker *worker = argument;
    HANDLE event = CreateEventW(NULL, FALSE, FALSE, NULL);
    AcquireSRWLockExclusive(&worker->mutex);
    for (;;)
    {
        while (!worker->head && !worker->stop)
            SleepConditionVariableSRW(&worker->condition, &worker->mutex, INFINITE, 0);
        if (!worker->head) break;
        struct job *job = worker->head;
        worker->head = job->next;
        if (!worker->head) worker->tail = NULL;
        ReleaseSRWLockExclusive(&worker->mutex);

        hipError_t error = amdnr_native_fence_wait(job->token);
        HRESULT result = E_FAIL;
        if (!error && job->signal)
            result = ID3D12Fence_Signal(job->fence->object, job->value);
        else if (!error && event)
        {
            result = ID3D12Fence_SetEventOnCompletion(job->fence->object, job->value, event);
            if (SUCCEEDED(result) && WaitForSingleObject(event, INFINITE) != WAIT_OBJECT_0)
                result = E_FAIL;
        }
        release_fence(job->fence);
        AcquireSRWLockExclusive(&worker->mutex);
        if (error || FAILED(result))
        {
            amdnr_log("D3D12 fence %s failed: %#x", job->signal ? "signal" : "wait", (unsigned)result);
            worker->error = error ? error : hipErrorUnknown;
        }
        /* Acknowledgement comes only after the actual COM operation and COM
         * reference release. Native stream progress never substitutes for this
         * acknowledgement. Preserve COM errors for HIP stream sync/destroy. */
        error = amdnr_native_fence_complete(job->token);
        if (error) worker->error = error;
        free(job);
    }
    ReleaseSRWLockExclusive(&worker->mutex);
    if (event) CloseHandle(event);
    return 0;
}

hipError_t amdnr_fence_import(hipExternalSemaphore_t *out, HANDLE handle)
{
    if (!out || !handle) return hipErrorInvalidValue;
    *out = NULL;
    int ordinal = 0;
    hipDeviceProp_t properties;
    hipError_t error = amdnr_native_hipGetDevice(&ordinal);
    if (!error) error = amdnr_native_hipGetDevicePropertiesR0600(&properties, ordinal);
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
    AcquireSRWLockExclusive(&registry);
    fence->next = fences;
    fences = fence;
    ReleaseSRWLockExclusive(&registry);
    *out = (hipExternalSemaphore_t)fence;
    return hipSuccess;
}

hipError_t amdnr_fence_destroy(hipExternalSemaphore_t semaphore)
{
    AcquireSRWLockExclusive(&registry);
    struct fence **entry = &fences;
    while (*entry && (void *)*entry != (void *)semaphore) entry = &(*entry)->next;
    struct fence *fence = *entry;
    if (fence) *entry = fence->next;
    ReleaseSRWLockExclusive(&registry);
    if (!fence) return hipErrorInvalidHandle;
    release_fence(fence);
    return hipSuccess;
}

hipError_t amdnr_fence_submit(const hipExternalSemaphore_t *semaphores, const void *parameters,
                            unsigned count, hipStream_t stream, int signal)
{
    if (!count) return hipSuccess;
    if (!semaphores || !parameters) return hipErrorInvalidValue;
    hipError_t result = hipSuccess;

    /* Validate the entire batch before submitting any side effects. */
    struct job **batch = calloc(count, sizeof(*batch));
    if (!batch) return hipErrorOutOfMemory;
    AcquireSRWLockExclusive(&registry);
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
    ReleaseSRWLockExclusive(&registry);
    if (!result)
    {
        hipStreamCaptureStatus capture;
        result = amdnr_native_hipStreamIsCapturing(stream, &capture);
        if (!result && capture != hipStreamCaptureStatusNone) result = hipErrorStreamCaptureUnsupported;
    }
    for (unsigned i = 0; i < count; i++)
    {
        struct job *job = batch[i];
        if (!job) continue;
        if (!result)
        {
            AcquireSRWLockExclusive(&worker->mutex);
            result = worker->error;
            if (!result && !worker->thread)
            {
                worker->thread = CreateThread(NULL, 0, worker_main, worker, 0, NULL);
                if (!worker->thread) result = hipErrorOutOfMemory;
            }
            if (!result) result = amdnr_native_fence_launch(stream, &job->token);
            if (!result)
            {
                if (worker->tail) worker->tail->next = job;
                else worker->head = job;
                worker->tail = job;
                WakeAllConditionVariable(&worker->condition);
            }
            ReleaseSRWLockExclusive(&worker->mutex);
        }
        if (result) { release_fence(job->fence); free(job); }
    }
    free(batch);
    if (worker) finish_worker_call(worker);
    return result;
}

hipError_t amdnr_stream_finish(hipStream_t stream, int destroy)
{
    AcquireSRWLockExclusive(&registry);
    struct worker *worker = get_worker_locked(stream);
    if (!worker) { ReleaseSRWLockExclusive(&registry); return hipErrorOutOfMemory; }
    if (worker->closing) { ReleaseSRWLockExclusive(&registry); return hipErrorInvalidHandle; }
    if (destroy)
    {
        worker->closing = 1;
        /* The condition wait drops registry. Submitters and callbacks may need
         * it to release fences; never hold either mutex across a HIP drain. */
        while (worker->active_calls) SleepConditionVariableSRW(&registry_changed, &registry, INFINITE, 0);
    }
    else ++worker->active_calls;
    ReleaseSRWLockExclusive(&registry);

    hipError_t result = amdnr_native_hipStreamSynchronize(stream);
    if (!destroy)
    {
        if (!result)
        {
            AcquireSRWLockExclusive(&worker->mutex);
            result = worker->error;
            ReleaseSRWLockExclusive(&worker->mutex);
        }
        finish_worker_call(worker);
        return result;
    }
    if (!result) result = amdnr_native_hipStreamDestroy(stream);
    if (result)
    {
        /* A failed drain may leave native callbacks using this worker. Retain
         * it and permit a later retry; freeing it here would be a use-after-free. */
        AcquireSRWLockExclusive(&registry);
        worker->closing = 0;
        WakeAllConditionVariable(&registry_changed);
        ReleaseSRWLockExclusive(&registry);
        return result;
    }

    AcquireSRWLockExclusive(&worker->mutex);
    result = worker->error;
    worker->stop = 1;
    WakeAllConditionVariable(&worker->condition);
    ReleaseSRWLockExclusive(&worker->mutex);
    if (worker->thread)
    {
        WaitForSingleObject(worker->thread, INFINITE);
        CloseHandle(worker->thread);
    }
    AcquireSRWLockExclusive(&registry);
    struct worker **entry = &workers;
    while (*entry != worker) entry = &(*entry)->next;
    *entry = worker->next;
    ReleaseSRWLockExclusive(&registry);
    free(worker);
    return result;
}
