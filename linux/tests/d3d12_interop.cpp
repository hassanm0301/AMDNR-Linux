// SPDX-License-Identifier: GPL-3.0-or-later
#define __HIP_PLATFORM_AMD__
#include <windows.h>
#include <d3d12.h>
#include <dxgi1_4.h>
#include <hip/hip_runtime_api.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <vector>
#define HR(call) do { HRESULT result = (call); if (FAILED(result)) { std::fprintf(stderr, "%s failed: %#lx\n", #call, (unsigned long)result); return 1; } } while (0)
#define HIP(call) do { int result = (call); if (result) { std::fprintf(stderr, "%s failed: %d\n", #call, result); return 1; } } while (0)
#define LOAD(name) auto name = reinterpret_cast<decltype(&::name)>(reinterpret_cast<void *>(GetProcAddress(hip, #name))); if (!name) { std::fprintf(stderr, "missing %s\n", #name); return 1; }
#define EXPECT(call, code) do { int result = (call); if (result != (code)) { std::fprintf(stderr, "%s: expected %d, got %d\n", #call, (int)(code), result); return 1; } } while (0)

int main()
{
    HMODULE hip = LoadLibraryExW(L"amdhip64_7.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!hip) return 1;
    LOAD(hipInit); LOAD(hipSetDevice); LOAD(hipGetDeviceCount); LOAD(hipGetDevicePropertiesR0600);
    LOAD(hipImportExternalMemory); LOAD(hipExternalMemoryGetMappedBuffer); LOAD(hipDestroyExternalMemory);
    LOAD(hipImportExternalSemaphore); LOAD(hipDestroyExternalSemaphore);
    LOAD(hipWaitExternalSemaphoresAsync); LOAD(hipSignalExternalSemaphoresAsync);
    LOAD(hipStreamCreate); LOAD(hipStreamSynchronize); LOAD(hipStreamDestroy); LOAD(hipMemsetD32Async); LOAD(hipFree);
    LOAD(hipMemcpyAsync); LOAD(hipHostFree);
    // The SDK also declares a templated C++ overload of hipHostMalloc.
    using HostMallocFn = hipError_t (*)(void **, size_t, unsigned);
    auto hipHostMalloc = reinterpret_cast<HostMallocFn>(reinterpret_cast<void *>(GetProcAddress(hip, "hipHostMalloc")));
    if (!hipHostMalloc) { std::fputs("missing hipHostMalloc\n", stderr); return 1; }
    LOAD(hipStreamBeginCapture); LOAD(hipStreamEndCapture); LOAD(hipGraphDestroy);
    HIP(hipInit(0));
    int count = 0, ordinal = -1; hipDeviceProp_t prop{};
    HIP(hipGetDeviceCount(&count));
    for (int i = 0; i < count; i++) { HIP(hipGetDevicePropertiesR0600(&prop, i)); if (!std::strncmp(prop.gcnArchName, "gfx1201", 7)) { ordinal = i; break; } }
    if (ordinal < 0) return 77;
    HIP(hipSetDevice(ordinal));
    LUID luid; std::memcpy(&luid, prop.luid, sizeof(luid));
    IDXGIFactory4 *factory = nullptr; IDXGIAdapter1 *adapter = nullptr; ID3D12Device *device = nullptr;
    HR(CreateDXGIFactory1(IID_PPV_ARGS(&factory)));
    HR(factory->EnumAdapterByLuid(luid, IID_PPV_ARGS(&adapter)));
    // DXVK reports PCI revision 0; the host's device table needs the real one to know the card.
    DXGI_ADAPTER_DESC1 adapter_desc{}; HR(adapter->GetDesc1(&adapter_desc));
    std::printf("DXGI adapter %04x:%04x revision %02x\n", adapter_desc.VendorId, adapter_desc.DeviceId, adapter_desc.Revision);
    if (!adapter_desc.Revision) { std::fputs("DXGI still reports PCI revision 0 for the HIP adapter\n", stderr); return 1; }
    HR(D3D12CreateDevice(adapter, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&device)));
    D3D12_COMMAND_QUEUE_DESC qd{}; ID3D12CommandQueue *queue = nullptr;
    HR(device->CreateCommandQueue(&qd, IID_PPV_ARGS(&queue)));
    // Creating a device alone misses accidental use of Wine's DXGI with a
    // vkd3d-proton device. Exercise the same swapchain boundary as a real game.
    WNDCLASSW wc{}; wc.lpfnWndProc = DefWindowProcW; wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"AmdNrInteropTest";
    if (!RegisterClassW(&wc)) return 1;
    HWND window = CreateWindowW(wc.lpszClassName, L"AMDNR interop test", WS_OVERLAPPEDWINDOW,
                                0, 0, 64, 64, nullptr, nullptr, wc.hInstance, nullptr);
    if (!window) return 1;
    DXGI_SWAP_CHAIN_DESC1 swap_desc{}; swap_desc.Width = swap_desc.Height = 64;
    swap_desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM; swap_desc.SampleDesc.Count = 1;
    swap_desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT; swap_desc.BufferCount = 2;
    swap_desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    IDXGISwapChain1 *swapchain = nullptr;
    HR(factory->CreateSwapChainForHwnd(queue, window, &swap_desc, nullptr, nullptr, &swapchain));
    swapchain->Release(); DestroyWindow(window); UnregisterClassW(wc.lpszClassName, wc.hInstance);
    ID3D12Fence *fence = nullptr; HR(device->CreateFence(0, D3D12_FENCE_FLAG_SHARED, IID_PPV_ARGS(&fence)));
    HANDLE fence_handle = nullptr; HR(device->CreateSharedHandle(fence, nullptr, GENERIC_ALL, nullptr, &fence_handle));
    hipExternalSemaphoreHandleDesc sd{}; sd.type = hipExternalSemaphoreHandleTypeD3D12Fence; sd.handle.win32.handle = fence_handle;
    hipExternalSemaphore_t semaphore = nullptr; HIP(hipImportExternalSemaphore(&semaphore, &sd));
    CloseHandle(fence_handle);
    D3D12_RESOURCE_DESC desc{}; desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; desc.Width = 65536;
    desc.Height = desc.DepthOrArraySize = desc.MipLevels = desc.SampleDesc.Count = 1;
    desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR; desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
    D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_DEFAULT;
    ID3D12Resource *shared = nullptr, *readback = nullptr;
    HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_SHARED, &desc, D3D12_RESOURCE_STATE_COMMON, nullptr, IID_PPV_ARGS(&shared)));
    HANDLE memory_handle = nullptr; HR(device->CreateSharedHandle(shared, nullptr, GENERIC_ALL, nullptr, &memory_handle));
    hipExternalMemoryHandleDesc md{}; md.type = hipExternalMemoryHandleTypeD3D12Resource; md.handle.win32.handle = memory_handle;
    md.size = device->GetResourceAllocationInfo(0, 1, &desc).SizeInBytes; md.flags = 1;
    hipExternalMemory_t memory = nullptr; HIP(hipImportExternalMemory(&memory, &md));
    CloseHandle(memory_handle);
    hipExternalMemoryBufferDesc bd{}; bd.size = desc.Width;
    void *mapped = nullptr; HIP(hipExternalMemoryGetMappedBuffer(&mapped, memory, &bd));
    heap.Type = D3D12_HEAP_TYPE_READBACK; desc.Flags = D3D12_RESOURCE_FLAG_NONE;
    HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&readback)));
    ID3D12CommandAllocator *allocator = nullptr; ID3D12GraphicsCommandList *list = nullptr;
    HR(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator)));
    HR(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator, nullptr, IID_PPV_ARGS(&list)));
    list->CopyBufferRegion(readback, 0, shared, 0, desc.Width); HR(list->Close());
    hipStream_t stream = nullptr; HIP(hipStreamCreate(&stream));
    EXPECT(hipImportExternalSemaphore(nullptr, &sd), hipErrorInvalidValue);
    EXPECT(hipImportExternalMemory(nullptr, &md), hipErrorInvalidValue);
    hipExternalSemaphore_t invalid = reinterpret_cast<hipExternalSemaphore_t>(1);
    hipExternalSemaphoreWaitParams invalid_wait{};
    EXPECT(hipWaitExternalSemaphoresAsync(&invalid, &invalid_wait, 1, stream), hipErrorInvalidHandle);
    invalid_wait.flags = 1;
    EXPECT(hipWaitExternalSemaphoresAsync(&semaphore, &invalid_wait, 1, stream), hipErrorInvalidValue);
    HIP(hipStreamBeginCapture(stream, hipStreamCaptureModeGlobal));
    invalid_wait.flags = 0;
    EXPECT(hipWaitExternalSemaphoresAsync(&semaphore, &invalid_wait, 1, stream), hipErrorStreamCaptureUnsupported);
    hipGraph_t graph = nullptr; HIP(hipStreamEndCapture(stream, &graph)); HIP(hipGraphDestroy(graph));
    // One API call may contain several fences, including repeated handles.
    // Validate the entire batch before any operation can change a timeline.
    {
        constexpr unsigned batch_size = 3;
        ID3D12Fence *batch_fences[batch_size]{};
        hipExternalSemaphore_t batch_semaphores[batch_size]{};
        hipExternalSemaphoreSignalParams signals[batch_size]{};
        hipExternalSemaphoreWaitParams waits[batch_size]{};
        for (unsigned i = 0; i < batch_size; i++) {
            HR(device->CreateFence(0, D3D12_FENCE_FLAG_SHARED, IID_PPV_ARGS(&batch_fences[i])));
            HANDLE handle = nullptr;
            HR(device->CreateSharedHandle(batch_fences[i], nullptr, GENERIC_ALL, nullptr, &handle));
            hipExternalSemaphoreHandleDesc imported{};
            imported.type = hipExternalSemaphoreHandleTypeD3D12Fence;
            imported.handle.win32.handle = handle;
            HIP(hipImportExternalSemaphore(&batch_semaphores[i], &imported));
            CloseHandle(handle);
            signals[i].params.fence.value = 41 + i * 42;
            waits[i].params.fence.value = signals[i].params.fence.value;
        }
        HIP(hipWaitExternalSemaphoresAsync(nullptr, nullptr, 0, stream));
        HIP(hipSignalExternalSemaphoresAsync(nullptr, nullptr, 0, stream));
        HIP(hipSignalExternalSemaphoresAsync(batch_semaphores, signals, batch_size, stream));
        HIP(hipStreamSynchronize(stream));
        for (unsigned i = 0; i < batch_size; i++)
            if (batch_fences[i]->GetCompletedValue() != signals[i].params.fence.value) return 1;
        HIP(hipWaitExternalSemaphoresAsync(batch_semaphores, waits, batch_size, stream));

        hipExternalSemaphore_t invalid_batch[] = {batch_semaphores[0], invalid, batch_semaphores[2]};
        for (auto &signal : signals) signal.params.fence.value = 999999;
        EXPECT(hipSignalExternalSemaphoresAsync(invalid_batch, signals, batch_size, stream), hipErrorInvalidHandle);
        signals[batch_size - 1].flags = 1;
        EXPECT(hipSignalExternalSemaphoresAsync(batch_semaphores, signals, batch_size, stream), hipErrorInvalidValue);
        signals[batch_size - 1].flags = 0;
        // An incorrectly enqueued prefix would block stream synchronization.
        waits[0].params.fence.value = 999999;
        waits[batch_size - 1].flags = 1;
        EXPECT(hipWaitExternalSemaphoresAsync(batch_semaphores, waits, batch_size, stream), hipErrorInvalidValue);
        waits[batch_size - 1].flags = 0;
        HIP(hipStreamSynchronize(stream));
        for (unsigned i = 0; i < batch_size; i++)
            if (batch_fences[i]->GetCompletedValue() != 41 + i * 42) {
                std::fputs("rejected fence batch changed a timeline\n", stderr); return 1;
            }
        hipExternalSemaphore_t duplicate[] = {batch_semaphores[0], batch_semaphores[0]};
        hipExternalSemaphoreSignalParams rewind[2]{};
        rewind[0].params.fence.value = 202; rewind[1].params.fence.value = 201;
        // Wine runners differ in their shared-fence rewind support. Compare
        // against direct COM on a fresh shared fence to separate a bridge
        // regression from an underlying runner limitation.
        ID3D12Fence *rewind_control = nullptr, *rewind_import = nullptr;
        HR(device->CreateFence(0, D3D12_FENCE_FLAG_SHARED, IID_PPV_ARGS(&rewind_control)));
        HANDLE rewind_handle = nullptr;
        HR(device->CreateSharedHandle(rewind_control, nullptr, GENERIC_ALL, nullptr, &rewind_handle));
        HR(device->OpenSharedHandle(rewind_handle, IID_PPV_ARGS(&rewind_import)));
        CloseHandle(rewind_handle);
        HR(rewind_import->Signal(202));
        HRESULT rewind_result = rewind_import->Signal(201);
        UINT64 direct_rewind = rewind_control->GetCompletedValue();
        rewind_import->Release(); rewind_control->Release();
        if (SUCCEEDED(rewind_result)) {
            HIP(hipSignalExternalSemaphoresAsync(duplicate, rewind, 2, stream));
            HIP(hipStreamSynchronize(stream));
            UINT64 observed_rewind = batch_fences[0]->GetCompletedValue();
            if (observed_rewind != direct_rewind) {
                std::fprintf(stderr, "batched fence rewind differs from direct COM: direct %llu, bridge %llu (requested 202 -> 201)\n",
                    static_cast<unsigned long long>(direct_rewind),
                    static_cast<unsigned long long>(observed_rewind)); return 1;
            }
            if (direct_rewind != 201)
                std::printf("SKIP: shared_fence_rewind: direct D3D12 Signal(202) -> Signal(201) observed %llu instead of 201; bridge matched; runner does not support this rewind\n",
                    static_cast<unsigned long long>(direct_rewind));
        } else {
            std::printf("SKIP: shared_fence_rewind: direct D3D12 Signal(201) after Signal(202) failed with %#lx; bridge rewind not attempted\n",
                static_cast<unsigned long>(rewind_result));
        }
        // Duplicate handles must still work when the runner has monotonic
        // shared fences. Keep this assertion independent of the rewind probe.
        rewind[0].params.fence.value = 303; rewind[1].params.fence.value = 304;
        HIP(hipSignalExternalSemaphoresAsync(duplicate, rewind, 2, stream));
        HIP(hipStreamSynchronize(stream));
        UINT64 observed_duplicate = batch_fences[0]->GetCompletedValue();
        if (observed_duplicate != 304) {
            std::fprintf(stderr, "batched duplicate fence signals: expected 304, observed %llu\n",
                static_cast<unsigned long long>(observed_duplicate)); return 1;
        }

        const char *benchmark = std::getenv("AMDNR_TEST_BENCHMARK");
        if (benchmark && *benchmark && std::strcmp(benchmark, "0")) {
            LARGE_INTEGER frequency{};
            if (!QueryPerformanceFrequency(&frequency) || !frequency.QuadPart) return 1;
            constexpr unsigned warmup = 32, samples = 256;
            for (unsigned semaphores_per_call : {1u, batch_size}) {
                std::vector<double> elapsed;
                double total = 0;
                for (unsigned sample = 0; sample < warmup + samples; sample++) {
                    for (unsigned i = 0; i < semaphores_per_call; i++) {
                        waits[i].params.fence.value = batch_fences[i]->GetCompletedValue();
                        signals[i].params.fence.value = 10000 * semaphores_per_call + sample;
                    }
                    LARGE_INTEGER start{}, end{};
                    QueryPerformanceCounter(&start);
                    HIP(hipWaitExternalSemaphoresAsync(batch_semaphores, waits, semaphores_per_call, stream));
                    HIP(hipSignalExternalSemaphoresAsync(batch_semaphores, signals, semaphores_per_call, stream));
                    HIP(hipStreamSynchronize(stream));
                    QueryPerformanceCounter(&end);
                    if (sample >= warmup) {
                        double milliseconds = 1000.0 * (end.QuadPart - start.QuadPart) / frequency.QuadPart;
                        elapsed.push_back(milliseconds); total += milliseconds;
                    }
                }
                std::sort(elapsed.begin(), elapsed.end());
                std::printf("BENCHMARK: {\"metric\":\"fence_batch_roundtrip_ms\",\"semaphores_per_call\":%u,\"samples\":%u,\"warmup\":%u,\"mean_ms\":%.6f,\"p50_ms\":%.6f,\"p95_ms\":%.6f,\"min_ms\":%.6f,\"max_ms\":%.6f}\n",
                    semaphores_per_call, samples, warmup, total / samples,
                    elapsed[samples / 2], elapsed[(samples * 95) / 100], elapsed.front(), elapsed.back());
            }
        }

        // Destroy imports while the first batch is waiting. Queued operations
        // must retain every fence until the final native acknowledgement.
        for (unsigned i = 0; i < batch_size; i++) {
            waits[i].params.fence.value = 1000000 + i;
            signals[i].params.fence.value = 2000000 + i;
        }
        HIP(hipWaitExternalSemaphoresAsync(batch_semaphores, waits, batch_size, stream));
        HIP(hipSignalExternalSemaphoresAsync(batch_semaphores, signals, batch_size, stream));
        for (unsigned i = 0; i < batch_size; i++) HIP(hipDestroyExternalSemaphore(batch_semaphores[i]));
        for (unsigned i = 0; i < batch_size; i++) HR(batch_fences[i]->Signal(waits[i].params.fence.value));
        HIP(hipStreamSynchronize(stream));
        for (unsigned i = 0; i < batch_size; i++) {
            if (batch_fences[i]->GetCompletedValue() != signals[i].params.fence.value) {
                std::fputs("pending fence batch lost its references\n", stderr); return 1;
            }
            batch_fences[i]->Release();
        }
    }
    HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    for (unsigned round = 0; round < 256; round++) {
        // Values intentionally skip numbers: a raw Vulkan timeline is NOT the D3D12 timeline.
        UINT64 base = UINT64(round) * 1000;
        unsigned pattern = 0x12345678 ^ (round * 0x1020304u);
        hipExternalSemaphoreWaitParams wait{}; wait.params.fence.value = base + 41;
        HIP(hipWaitExternalSemaphoresAsync(&semaphore, &wait, 1, stream));
        HIP(hipMemsetD32Async(mapped, pattern, desc.Width / 4, stream));
        hipExternalSemaphoreSignalParams signal{}; signal.params.fence.value = base + 103;
        HIP(hipSignalExternalSemaphoresAsync(&semaphore, &signal, 1, stream));
        HR(queue->Signal(fence, base + 41)); HR(queue->Wait(fence, base + 103));
        ID3D12CommandList *lists[] = {list}; queue->ExecuteCommandLists(1, lists);
        HR(queue->Signal(fence, base + 205));
        HR(fence->SetEventOnCompletion(base + 205, event));
        if (WaitForSingleObject(event, 30000) != WAIT_OBJECT_0) { std::fputs("fence round trip timed out\n", stderr); return 1; }
        HIP(hipStreamSynchronize(stream));
        unsigned *data = nullptr; HR(readback->Map(0, nullptr, reinterpret_cast<void **>(&data)));
        for (unsigned i = 0; i < desc.Width / 4; i++) if (data[i] != pattern) { std::fprintf(stderr, "shared data mismatch round %u at %u: %#x\n", round, i, data[i]); return 1; }
        readback->Unmap(0, nullptr);
    }
    // Exercise the producer side too: D3D12 fills three distinct buffers, HIP
    // snapshots their contents and overwrites them, then D3D12 reads them back.
    // Queue all three handoffs before waiting on the CPU. Recreate the imports
    // halfway through to catch mappings or callbacks retained after teardown.
    constexpr unsigned batch_count = 3, words_per_buffer = 65536 / sizeof(unsigned);
    constexpr UINT64 bytes_per_buffer = words_per_buffer * sizeof(unsigned);
    constexpr UINT64 batch_bytes = batch_count * bytes_per_buffer;
    for (unsigned generation = 0; generation < 2; generation++) {
        ID3D12Resource *batch_shared[batch_count]{};
        hipExternalMemory_t batch_memory[batch_count]{};
        void *batch_mapped[batch_count]{};
        D3D12_RESOURCE_DESC batch_desc = desc;
        batch_desc.Width = bytes_per_buffer;
        batch_desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
        heap.Type = D3D12_HEAP_TYPE_DEFAULT;
        for (unsigned slot = 0; slot < batch_count; slot++) {
            HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_SHARED, &batch_desc,
                D3D12_RESOURCE_STATE_COMMON, nullptr, IID_PPV_ARGS(&batch_shared[slot])));
            HANDLE handle = nullptr;
            HR(device->CreateSharedHandle(batch_shared[slot], nullptr, GENERIC_ALL, nullptr, &handle));
            hipExternalMemoryHandleDesc imported{};
            imported.type = hipExternalMemoryHandleTypeD3D12Resource;
            imported.handle.win32.handle = handle;
            imported.size = device->GetResourceAllocationInfo(0, 1, &batch_desc).SizeInBytes;
            imported.flags = 1;
            HIP(hipImportExternalMemory(&batch_memory[slot], &imported));
            CloseHandle(handle);
            hipExternalMemoryBufferDesc mapped_desc{}; mapped_desc.size = bytes_per_buffer;
            HIP(hipExternalMemoryGetMappedBuffer(&batch_mapped[slot], batch_memory[slot], &mapped_desc));
        }
        batch_desc.Width = batch_bytes; batch_desc.Flags = D3D12_RESOURCE_FLAG_NONE;
        ID3D12Resource *upload = nullptr, *batch_readback = nullptr;
        heap.Type = D3D12_HEAP_TYPE_UPLOAD;
        HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &batch_desc,
            D3D12_RESOURCE_STATE_GENERIC_READ, nullptr, IID_PPV_ARGS(&upload)));
        heap.Type = D3D12_HEAP_TYPE_READBACK;
        HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &batch_desc,
            D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&batch_readback)));
        unsigned *uploaded = nullptr, *observed = nullptr;
        D3D12_RANGE no_reads{0, 0};
        HR(upload->Map(0, &no_reads, reinterpret_cast<void **>(&uploaded)));
        HIP(hipHostMalloc(reinterpret_cast<void **>(&observed), batch_bytes, 0));
        ID3D12CommandAllocator *producer_allocator = nullptr, *consumer_allocator = nullptr;
        ID3D12GraphicsCommandList *producer = nullptr, *consumer = nullptr;
        HR(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&producer_allocator)));
        HR(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&consumer_allocator)));
        HR(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, producer_allocator, nullptr,
            IID_PPV_ARGS(&producer)));
        HR(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, consumer_allocator, nullptr,
            IID_PPV_ARGS(&consumer)));
        for (unsigned slot = 0; slot < batch_count; slot++) {
            D3D12_RESOURCE_BARRIER barrier{}; barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
            barrier.Transition.pResource = batch_shared[slot];
            barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
            barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_COMMON;
            barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
            producer->ResourceBarrier(1, &barrier);
            producer->CopyBufferRegion(batch_shared[slot], 0, upload, slot * bytes_per_buffer, bytes_per_buffer);
            barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
            barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_COMMON;
            producer->ResourceBarrier(1, &barrier);
            barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_COMMON;
            barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
            consumer->ResourceBarrier(1, &barrier);
            consumer->CopyBufferRegion(batch_readback, slot * bytes_per_buffer,
                batch_shared[slot], 0, bytes_per_buffer);
            barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_SOURCE;
            barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_COMMON;
            consumer->ResourceBarrier(1, &barrier);
        }
        HR(producer->Close()); HR(consumer->Close());
        for (unsigned round = 0; round < 32; round++) {
            unsigned frame = generation * 32 + round;
            UINT64 base = 1000000 + UINT64(frame) * 1000;
            auto input_pattern = [frame](unsigned slot, unsigned word) {
                return 0x6a09e667u ^ (frame * 0x1020304u) ^ (slot * 0x9e3779b9u) ^ (word * 0x85ebca6bu);
            };
            auto output_pattern = [frame](unsigned slot) {
                return 0xbb67ae85u ^ (frame * 0x1000193u) ^ (slot * 0xc2b2ae35u);
            };
            for (unsigned slot = 0; slot < batch_count; slot++)
                for (unsigned word = 0; word < words_per_buffer; word++)
                    uploaded[slot * words_per_buffer + word] = input_pattern(slot, word);
            std::memset(observed, 0, batch_bytes);
            ID3D12CommandList *produce[] = {producer}; queue->ExecuteCommandLists(1, produce);
            HR(queue->Signal(fence, base + 41));
            for (unsigned slot = 0; slot < batch_count; slot++) {
                hipExternalSemaphoreWaitParams wait{}; wait.params.fence.value = base + 41;
                HIP(hipWaitExternalSemaphoresAsync(&semaphore, &wait, 1, stream));
                HIP(hipMemcpyAsync(observed + slot * words_per_buffer, batch_mapped[slot],
                    bytes_per_buffer, hipMemcpyDeviceToHost, stream));
                HIP(hipMemsetD32Async(batch_mapped[slot], output_pattern(slot), words_per_buffer, stream));
                hipExternalSemaphoreSignalParams signal{}; signal.params.fence.value = base + 103 + slot;
                HIP(hipSignalExternalSemaphoresAsync(&semaphore, &signal, 1, stream));
            }
            HR(queue->Wait(fence, base + 103 + batch_count - 1));
            ID3D12CommandList *consume[] = {consumer}; queue->ExecuteCommandLists(1, consume);
            HR(queue->Signal(fence, base + 205));
            HR(fence->SetEventOnCompletion(base + 205, event));
            if (WaitForSingleObject(event, 30000) != WAIT_OBJECT_0) {
                std::fputs("bidirectional batch timed out\n", stderr); return 1;
            }
            HIP(hipStreamSynchronize(stream));
            unsigned *received = nullptr;
            HR(batch_readback->Map(0, nullptr, reinterpret_cast<void **>(&received)));
            for (unsigned slot = 0; slot < batch_count; slot++) {
                for (unsigned word = 0; word < words_per_buffer; word++) {
                    unsigned offset = slot * words_per_buffer + word;
                    if (observed[offset] != input_pattern(slot, word) || received[offset] != output_pattern(slot)) {
                        std::fprintf(stderr, "bidirectional mismatch frame %u slot %u word %u: HIP read %#x (expected %#x), D3D12 read %#x (expected %#x)\n",
                            frame, slot, word, observed[offset], input_pattern(slot, word),
                            received[offset], output_pattern(slot));
                        return 1;
                    }
                }
            }
            batch_readback->Unmap(0, &no_reads);
        }
        HIP(hipHostFree(observed)); upload->Unmap(0, nullptr);
        producer->Release(); consumer->Release(); producer_allocator->Release(); consumer_allocator->Release();
        upload->Release(); batch_readback->Release();
        for (unsigned slot = 0; slot < batch_count; slot++) {
            HIP(hipFree(batch_mapped[slot])); HIP(hipDestroyExternalMemory(batch_memory[slot]));
            batch_shared[slot]->Release();
        }
    }
    HIP(hipStreamDestroy(stream)); HIP(hipDestroyExternalSemaphore(semaphore));
    EXPECT(hipDestroyExternalSemaphore(semaphore), hipErrorInvalidHandle);
    HIP(hipFree(mapped)); HIP(hipDestroyExternalMemory(memory));
    CloseHandle(event); list->Release(); allocator->Release(); readback->Release(); shared->Release();
    fence->Release(); queue->Release(); device->Release(); adapter->Release(); factory->Release();
    std::puts("PASS: 256 D3D12/HIP fence round trips, 64 bidirectional batches of 3 buffers with import recreation; batched and duplicate fences, pending destruction; shared-fence rewind compared with direct COM (see any SKIP); invalid handles/flags and capture rejected");
    return 0;
}
