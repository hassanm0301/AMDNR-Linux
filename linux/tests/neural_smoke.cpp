// SPDX-License-Identifier: GPL-3.0-or-later
// Run the real, separately supplied neural runtime against deterministic D3D12 frames.
#include <windows.h>
#include <d3d12.h>
#include <dxgi1_4.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <numeric>
#include <vector>
#include "../../main/AMDNR-source/optiscaler/dlssnr/lmxxf/runtime/LmxxfNrApi.h"
#define HR(call) do { HRESULT rc = (call); if (FAILED(rc)) { std::fprintf(stderr, "%s: %#lx\n", #call, (unsigned long)rc); return 1; } } while (0)
#define NR(call) do { if (!benchmark) { std::printf("%s\n", #call); std::fflush(stdout); } int rc = (call); if (rc) { char error[4096]{}; api.GetLastError(error, sizeof(error)); std::fprintf(stderr, "%s: %d %s\n", #call, rc, error); return 1; } } while (0)
static uint16_t half(float v) {
    uint32_t bits; std::memcpy(&bits, &v, 4);
    return uint16_t(((bits >> 16) & 0x8000) | ((((bits >> 23) & 255) - 112) << 10) | ((bits >> 13) & 1023));
}
static float unhalf(uint16_t v) {
    unsigned e = (v >> 10) & 31, m = v & 1023;
    if (e == 31) return m ? NAN : INFINITY;
    float f = e ? std::ldexp(1.f + float(m) / 1024, int(e) - 15) : std::ldexp(float(m), -24);
    return v & 0x8000 ? -f : f;
}
static bool setting(const char *name) {
    const char *value = std::getenv(name);
    return value && value[0] == '1';
}
int wmain(int argc, wchar_t **argv)
{
    const bool benchmark = setting("AMDNR_TEST_BENCHMARK");
    if (argc < 3 || argc > 6 || argc == 5) { std::fputs("usage: neural_smoke.exe LmxxfNrRuntime.dll LmxxfNrRuntime.pak [frames [width height]]\n", stderr); return 1; }
    unsigned frames = argc >= 4 ? _wtoi(argv[3]) : 3;
    unsigned width = argc == 6 ? _wtoi(argv[4]) : 640, height = argc == 6 ? _wtoi(argv[5]) : 360;
    if (frames < 3 || frames > 10000 || width < 64 || height < 64 || width > 1920 || height > 1080) return 1;
    const unsigned warmup = 10;
    if (benchmark && frames < 30) { std::fputs("Benchmark requires at least 30 frames (10 warmup)\n", stderr); return 1; }
    LARGE_INTEGER frequency{};
    if (benchmark && (!QueryPerformanceFrequency(&frequency) || !frequency.QuadPart)) return 1;
    std::vector<double> timings;
    // Exercise the released host's real DXGI/D3D12/Vulkan hooks when requested.
    // Keep it loaded until process exit: its hooks are process-wide.
    wchar_t host_path[32768];
    DWORD host_length = GetEnvironmentVariableW(L"AMDNR_TEST_HOST", host_path, 32768);
    if (host_length) {
        if (host_length >= 32768 || !LoadLibraryW(host_path)) {
            std::fprintf(stderr, "OptiScaler host load: %lu\n", GetLastError()); return 1;
        }
        std::puts("Released OptiScaler host loaded for hook integration test");
    }
    HMODULE runtime = LoadLibraryW(argv[1]);
    if (!runtime) { std::fprintf(stderr, "runtime load: %lu\n", GetLastError()); return 1; }
    auto get_api = reinterpret_cast<decltype(&LmxxfNrGetApi)>(reinterpret_cast<void *>(GetProcAddress(runtime, "LmxxfNrGetApi")));
    LmxxfNrApi api{}; api.struct_size = sizeof(api);
    if (!get_api || get_api(LMXXF_NR_ABI_VERSION, &api)) return 1;
    IDXGIFactory1 *factory = nullptr; IDXGIAdapter1 *adapter = nullptr;
    HR(CreateDXGIFactory1(IID_PPV_ARGS(&factory)));
    SIZE_T largest = 0;
    for (unsigned i = 0;; i++) {
        IDXGIAdapter1 *candidate = nullptr;
        if (factory->EnumAdapters1(i, &candidate) != S_OK) break;
        DXGI_ADAPTER_DESC1 desc{}; candidate->GetDesc1(&desc);
        // The host may deliberately spoof VendorId; the bridge validates HIP
        // identity independently using physical PCI/UUID identifiers.
        if (!(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) && desc.DedicatedVideoMemory > largest) {
            if (adapter) adapter->Release();
            adapter = candidate; largest = desc.DedicatedVideoMemory;
        } else candidate->Release();
    }
    if (!adapter) return 77;
    ID3D12Device *device = nullptr; HR(D3D12CreateDevice(adapter, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&device)));
    ID3D12CommandQueue *queue = nullptr; D3D12_COMMAND_QUEUE_DESC qd{};
    HR(device->CreateCommandQueue(&qd, IID_PPV_ARGS(&queue)));
    ID3D12Fence *fence = nullptr; HR(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence)));
    HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (!event) return 1;
    UINT64 fence_value = 0;
    auto drain = [&] {
        if (FAILED(queue->Signal(fence, ++fence_value)) || FAILED(fence->SetEventOnCompletion(fence_value, event)) ||
            WaitForSingleObject(event, 60000) != WAIT_OBJECT_0) { std::fputs("GPU timeout\n", stderr); std::exit(1); }
    };
    D3D12_RESOURCE_DESC td{}; td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D; td.Width = width; td.Height = height;
    td.DepthOrArraySize = td.MipLevels = td.SampleDesc.Count = 1; td.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;
    D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_DEFAULT;
    ID3D12Resource *color = nullptr; HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&color)));
    ID3D12Resource *motion = nullptr, *motion_upload = nullptr;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT motion_footprint{};
    if (setting("AMDNR_TEST_HISTORY")) {
        D3D12_RESOURCE_DESC md = td; md.Format = DXGI_FORMAT_R16G16_FLOAT;
        HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &md, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&motion)));
        UINT64 motion_bytes;
        device->GetCopyableFootprints(&md, 0, 1, 0, &motion_footprint, nullptr, nullptr, &motion_bytes);
        D3D12_RESOURCE_DESC mb{}; mb.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; mb.Width = motion_bytes;
        mb.Height = mb.DepthOrArraySize = mb.MipLevels = mb.SampleDesc.Count = 1; mb.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
        D3D12_HEAP_PROPERTIES upload_heap{}; upload_heap.Type = D3D12_HEAP_TYPE_UPLOAD;
        HR(device->CreateCommittedResource(&upload_heap, D3D12_HEAP_FLAG_NONE, &mb, D3D12_RESOURCE_STATE_GENERIC_READ, nullptr, IID_PPV_ARGS(&motion_upload)));
        void *zero; HR(motion_upload->Map(0, nullptr, &zero)); std::memset(zero, 0, size_t(motion_bytes));
        motion_upload->Unmap(0, nullptr);
    }
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{}; UINT64 bytes;
    device->GetCopyableFootprints(&td, 0, 1, 0, &footprint, nullptr, nullptr, &bytes);
    D3D12_RESOURCE_DESC bd{}; bd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; bd.Width = bytes;
    bd.Height = bd.DepthOrArraySize = bd.MipLevels = bd.SampleDesc.Count = 1; bd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    ID3D12Resource *upload = nullptr, *readback = nullptr;
    heap.Type = D3D12_HEAP_TYPE_UPLOAD; HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &bd, D3D12_RESOURCE_STATE_GENERIC_READ, nullptr, IID_PPV_ARGS(&upload)));
    heap.Type = D3D12_HEAP_TYPE_READBACK; HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &bd, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&readback)));
    unsigned char *data; HR(upload->Map(0, nullptr, reinterpret_cast<void **>(&data)));
    for (unsigned y = 0; y < height; y++) for (unsigned x = 0; x < width; x++) {
        auto p = reinterpret_cast<uint16_t *>(data + y * footprint.Footprint.RowPitch + x * 8);
        p[0] = half(0.05f + .8f * x / width); p[1] = half(0.05f + .8f * y / height);
        p[2] = half(((x / 32 + y / 32) & 1) ? .15f : .65f); p[3] = half(1.f);
    }
    upload->Unmap(0, nullptr);
    ID3D12CommandAllocator *allocator = nullptr; ID3D12GraphicsCommandList *list = nullptr;
    HR(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator)));
    HR(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator, nullptr, IID_PPV_ARGS(&list)));
    auto submit = [&] { if (FAILED(list->Close())) std::exit(1); ID3D12CommandList *lists[] = {list}; queue->ExecuteCommandLists(1, lists); };
    auto reset = [&] { if (FAILED(allocator->Reset()) || FAILED(list->Reset(allocator, nullptr))) std::exit(1); };
    D3D12_TEXTURE_COPY_LOCATION dst{}; dst.pResource = color; dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    D3D12_TEXTURE_COPY_LOCATION src{}; src.pResource = upload; src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT; src.PlacedFootprint = footprint;
    list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    D3D12_RESOURCE_BARRIER barrier{}; barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition = {color, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE};
    list->ResourceBarrier(1, &barrier);
    if (motion) {
        auto motion_dst = dst; motion_dst.pResource = motion;
        auto motion_src = src; motion_src.pResource = motion_upload; motion_src.PlacedFootprint = motion_footprint;
        list->CopyTextureRegion(&motion_dst, 0, 0, 0, &motion_src, nullptr);
        auto motion_barrier = barrier; motion_barrier.Transition.pResource = motion;
        list->ResourceBarrier(1, &motion_barrier);
    }
    submit(); drain(); reset();
    void *context = nullptr; LmxxfNrCreateInfo ci{}; ci.struct_size = sizeof(ci); ci.device = device; ci.queue = queue; ci.assets_directory = argv[2];
    NR(api.Create(&ci, &context)); NR(api.PrepareSession(context));
    std::vector<float> reference;
    uint64_t output_hash = 14695981039346656037ull;
    for (unsigned frame = 0; frame < frames; frame++) {
        LmxxfNrFrameInfo fi{}; fi.struct_size = 128; fi.session_id = 1; fi.frame_id = fi.list_generation = frame + 1;
        fi.command_list = list; fi.color = color; fi.color_width = width; fi.color_height = height;
        fi.color_state = D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE; fi.model_scale = fi.transfer_strength = fi.color_strength = 1.f;
        fi.flags = LMXXF_NR_FRAME_FLAG_STRENGTH | (frame == 0 ? LMXXF_NR_FRAME_FLAG_CODEC_PASSTHROUGH : 0);
        if (setting("AMDNR_TEST_FULL_NETWORK"))
            fi.flags |= LMXXF_NR_FRAME_FLAG_FULL_NETWORK;
        if (motion) {
            fi.flags |= LMXXF_NR_FRAME_FLAG_TEMPORAL;
            fi.motion = motion; fi.motion_width = width; fi.motion_height = height;
            fi.motion_state = D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
            fi.motion_scale_x = fi.motion_scale_y = 1.f;
            fi.history_reset = frame <= 1 || frame % 17 == 0;
        }
        if (setting("AMDNR_TEST_CONTROLS")) {
            fi.struct_size = sizeof(fi); fi.flags |= LMXXF_NR_FRAME_FLAG_CONTROLS;
            fi.control_tone = fi.control_structure = fi.control_skin = fi.control_other = 1.f;
            fi.control_style = 0.f;
        }
        LmxxfNrJob job{}; job.struct_size = sizeof(job);
        // This measures the serialized validation pipeline, including GPU input/
        // output copies and readback completion. It is NOT game FPS or kernel time.
        // Use synchronous jobs to avoid the correctness test's 1 ms polling sleep.
        LARGE_INTEGER start{}, end{};
        if (benchmark) QueryPerformanceCounter(&start);
        NR(api.PrepareFrame(context, &fi, &job)); NR(api.RecordInputs(context, job.handle, list));
        submit(); drain(); reset();
        if (!benchmark && frame % 2 && api.EnqueueHipAsync && api.OutputReady) {
            NR(api.EnqueueHipAsync(context, job.handle, queue));
            DWORD start = GetTickCount(); uint32_t ready = 0;
            while (!ready) {
                if (api.OutputReady(context, job.handle, &ready)) return 1;
                if (GetTickCount() - start > 30000) { std::fputs("async neural timeout\n", stderr); return 1; }
                if (!ready) Sleep(1);
            }
        } else { NR(api.EnqueueHip(context, job.handle, queue)); }
        NR(api.RecordOutputs(context, job.handle, list));
        auto output = static_cast<ID3D12Resource *>(job.private_output);
        auto od = output->GetDesc();
        if (od.Width != width || od.Height != height || od.Format != td.Format) { std::fputs("unexpected output format\n", stderr); return 1; }
        barrier.Transition = {output, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_SOURCE};
        list->ResourceBarrier(1, &barrier);
        dst.pResource = readback; dst.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT; dst.PlacedFootprint = footprint;
        src.pResource = output; src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX; src.SubresourceIndex = 0;
        list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
        std::swap(barrier.Transition.StateBefore, barrier.Transition.StateAfter); list->ResourceBarrier(1, &barrier);
        submit(); drain();
        NR(api.Retire(context, job.handle));
        if (benchmark) {
            QueryPerformanceCounter(&end);
            if (frame >= warmup) timings.push_back(1000.0 * (end.QuadPart - start.QuadPart) / frequency.QuadPart);
        }
        HR(readback->Map(0, nullptr, reinterpret_cast<void **>(&data)));
        double sum = 0, delta = 0; unsigned changed = 0; std::vector<float> values;
        values.reserve(size_t(width) * height * 3);
        for (unsigned y = 0; y < height; y++) for (unsigned x = 0; x < width; x++) for (unsigned c = 0; c < 3; c++) {
            uint16_t raw = reinterpret_cast<uint16_t *>(data + y * footprint.Footprint.RowPitch + x * 8)[c];
            float v = unhalf(raw);
            if (benchmark && frame + 1 == frames) {
                output_hash = (output_hash ^ (raw & 255)) * 1099511628211ull;
                output_hash = (output_hash ^ (raw >> 8)) * 1099511628211ull;
            }
            if (!std::isfinite(v) || std::fabs(v) > 10000) { std::fputs("non-finite/out-of-range neural output\n", stderr); return 1; }
            if (frame) { double d = std::fabs(v - reference[values.size()]); delta += d; if (d > .001) changed++; }
            values.push_back(v); sum += v;
        }
        readback->Unmap(0, nullptr);
        if (!frame) reference = values;
        else if (changed < width * height / 100 || delta < 1) { std::fputs("neural output equals bypass\n", stderr); return 1; }
        if (!benchmark || frame == 0 || frame + 1 == frames) {
            std::printf("frame=%u mean=%g mean_abs_change=%g changed_channels=%u\n", frame, sum / values.size(), delta / values.size(), changed);
            char status[4096]{}; NR(api.GetStatus(context, status, sizeof(status))); std::puts(status);
        }
        reset();
    }
    if (benchmark) {
        std::sort(timings.begin(), timings.end());
        double mean = std::accumulate(timings.begin(), timings.end(), 0.0) / timings.size();
        auto percentile = [&](double fraction) { return timings[size_t(std::ceil(fraction * timings.size())) - 1]; };
        std::printf("BENCHMARK: {\"metric\":\"serialized_pipeline_readback_ms\",\"samples\":%zu,\"warmup\":%u,\"width\":%u,\"height\":%u,\"mean_ms\":%.6f,\"p50_ms\":%.6f,\"p95_ms\":%.6f,\"min_ms\":%.6f,\"max_ms\":%.6f,\"output_rgb_fnv1a64\":\"%016llx\"}\n",
                    timings.size(), warmup, width, height, mean, percentile(.5), percentile(.95), timings.front(), timings.back(),
                    static_cast<unsigned long long>(output_hash));
    }
    NR(api.Drain(context)); NR(api.Destroy(context));
    std::puts("PASS: real neural runtime produced finite changed frames on Linux");
    list->Close(); list->Release(); allocator->Release(); readback->Release(); upload->Release(); color->Release();
    if (motion_upload) motion_upload->Release();
    if (motion) motion->Release();
    fence->Release(); CloseHandle(event); queue->Release(); device->Release(); adapter->Release(); factory->Release();
    return 0;
}
