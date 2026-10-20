// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise the released OptiScaler host through its public NGX entry points.
// Stage the host/runtime/config in an isolated directory before running this test.
// Required config: Dx12Upscaler=fsr21 or fsr31 (the FFX path Model E replaces); FrameGen/Enabled=false; DLSS/Enabled=false;
// DlssNr/NrBackend=lmxxf or daniel and DlssNr/Enabled matching the baseline/neural argument.
// lmxxf needs LmxxfNrRuntime.dll/.pak beside the host; daniel needs danielblnc's
// dlssnr_amd_pass1..3.dll and dlssnr_on_amd_weights.bin there instead.
// Run baseline and neural in separate processes with the same remaining settings.
#include <windows.h>
#include <d3d12.h>
#include <dxgi1_4.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

// Render and output sizes; AMDNR_TEST_HOST_SIZE=RWxRH:OWxOH replaces them (e.g. 2646x1108:3440x1440).
static UINT width = 640, height = 360, out_width = 960, out_height = 540;
static constexpr unsigned ngx_success = 1;
#define HR(call) do { HRESULT rc = (call); if (FAILED(rc)) { std::fprintf(stderr, "%s: %#lx\n", #call, (unsigned long)rc); return 1; } } while (0)
#define NGX(call) do { unsigned rc = (call); if (rc != ngx_success) { std::fprintf(stderr, "%s: %#x\n", #call, rc); return 1; } } while (0)

// Minimal public NGX ABI: NVIDIA/DLSS include/nvsdk_ngx{,_defs,_params}.h.
// The release is MSVC-built. MSVC reverses each virtual overload group, whereas
// MinGW's C++ ABI does not. Explicit slots avoid silently calling the wrong Set.
// Confirmed using clang's x86_64-pc-windows-msvc vtable call lowering.
struct Parameters {
    void **vtable;
    void ui(const char *key, unsigned value) { reinterpret_cast<void (*)(Parameters *, const char *, unsigned)>(vtable[4])(this, key, value); }
    void integer(const char *key, int value) { reinterpret_cast<void (*)(Parameters *, const char *, int)>(vtable[3])(this, key, value); }
    void real(const char *key, float value) { reinterpret_cast<void (*)(Parameters *, const char *, float)>(vtable[6])(this, key, value); }
    void resource(const char *key, ID3D12Resource *value) { reinterpret_cast<void (*)(Parameters *, const char *, ID3D12Resource *)>(vtable[1])(this, key, value); }
    unsigned get_ui(const char *key, unsigned *value) { return reinterpret_cast<unsigned (*)(Parameters *, const char *, unsigned *)>(vtable[12])(this, key, value); }
    unsigned get_real(const char *key, float *value) { return reinterpret_cast<unsigned (*)(Parameters *, const char *, float *)>(vtable[14])(this, key, value); }
    unsigned get_resource(const char *key, ID3D12Resource **value) { return reinterpret_cast<unsigned (*)(Parameters *, const char *, ID3D12Resource **)>(vtable[9])(this, key, value); }
};
struct NgxHandle { unsigned id; };
struct Ngx {
    unsigned (*init)(unsigned long long, const wchar_t *, ID3D12Device *, unsigned, const void *);
    unsigned (*allocate)(Parameters **);
    unsigned (*create)(ID3D12GraphicsCommandList *, unsigned, Parameters *, NgxHandle **);
    unsigned (*evaluate)(ID3D12GraphicsCommandList *, const NgxHandle *, Parameters *, void *);
    unsigned (*release)(NgxHandle *);
    unsigned (*destroy_parameters)(Parameters *);
    unsigned (*shutdown)(ID3D12Device *);
};

static std::wstring directory(const std::wstring &path) {
    auto at = path.find_last_of(L"/\\");
    return at == std::wstring::npos ? L"." : path.substr(0, at);
}
static uint64_t file_size(const std::wstring &path) {
    WIN32_FILE_ATTRIBUTE_DATA data{};
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &data)) return 0;
    return (uint64_t(data.nFileSizeHigh) << 32) | data.nFileSizeLow;
}
static std::string fresh_log(const std::wstring &path, uint64_t start) {
    FILE *file = _wfopen(path.c_str(), L"rb");
    if (!file) return {};
    if (file_size(path) < start) start = 0;
    _fseeki64(file, static_cast<__int64>(start), SEEK_SET);
    std::string text;
    char data[4096]; size_t n;
    while ((n = std::fread(data, 1, sizeof(data), file))) text.append(data, n);
    std::fclose(file);
    return text;
}
// The released host identifies the GPU on its own thread right after loading. That
// thread creates a DXGI factory under a lock the app's first factory creation also
// reaches through the host's Vulkan hooks, and under DXVK the two can deadlock. Games
// start graphics later than this test; wait for the identification (Amdxc64Hooks on AMD).
static bool wait_for_log(const std::wstring &path, const char *marker, DWORD timeout_ms) {
    for (DWORD waited = 0; waited < timeout_ms; waited += 50) {
        if (fresh_log(path, 0).find(marker) != std::string::npos) return true;
        Sleep(50);
    }
    return false;
}
static unsigned counter_max(const std::string &text, const char *key) {
    unsigned maximum = 0; size_t at = 0;
    while ((at = text.find(key, at)) != std::string::npos) {
        at += std::strlen(key);
        unsigned value = static_cast<unsigned>(std::strtoul(text.c_str() + at, nullptr, 10));
        if (value > maximum) maximum = value;
    }
    return maximum;
}
static uint16_t half(float value) {
    uint32_t bits; std::memcpy(&bits, &value, 4);
    return uint16_t(((bits >> 16) & 0x8000) | ((((bits >> 23) & 255) - 112) << 10) | ((bits >> 13) & 1023));
}
static float unhalf(uint16_t value) {
    unsigned e = (value >> 10) & 31, m = value & 1023;
    if (e == 31) return m ? NAN : INFINITY;
    float result = e ? std::ldexp(1.f + float(m) / 1024, int(e) - 15) : std::ldexp(float(m), -24);
    return value & 0x8000 ? -result : result;
}
static D3D12_RESOURCE_DESC buffer_desc(UINT64 bytes) {
    D3D12_RESOURCE_DESC desc{}; desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    desc.Width = bytes; desc.Height = desc.DepthOrArraySize = desc.MipLevels = desc.SampleDesc.Count = 1;
    desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    return desc;
}
static void transition(ID3D12GraphicsCommandList *list, ID3D12Resource *resource,
                       D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
    D3D12_RESOURCE_BARRIER barrier{}; barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition = {resource, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, before, after};
    list->ResourceBarrier(1, &barrier);
}
struct Texture {
    ID3D12Resource *resource = nullptr, *upload = nullptr;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
};
static HRESULT make_input(ID3D12Device *device, ID3D12GraphicsCommandList *list,
                          DXGI_FORMAT format, unsigned kind, Texture &texture) {
    D3D12_RESOURCE_DESC desc{}; desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    desc.Width = width; desc.Height = height;
    desc.DepthOrArraySize = desc.MipLevels = desc.SampleDesc.Count = 1; desc.Format = format;
    D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_DEFAULT;
    HRESULT result = device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
        D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&texture.resource));
    if (FAILED(result)) return result;
    UINT64 bytes; device->GetCopyableFootprints(&desc, 0, 1, 0, &texture.footprint, nullptr, nullptr, &bytes);
    auto buffer = buffer_desc(bytes); heap.Type = D3D12_HEAP_TYPE_UPLOAD;
    result = device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &buffer,
        D3D12_RESOURCE_STATE_GENERIC_READ, nullptr, IID_PPV_ARGS(&texture.upload));
    if (FAILED(result)) return result;
    unsigned char *data;
    result = texture.upload->Map(0, nullptr, reinterpret_cast<void **>(&data));
    if (FAILED(result)) return result;
    std::memset(data, 0, static_cast<size_t>(bytes));
    for (unsigned y = 0; y < height; y++) for (unsigned x = 0; x < width; x++) {
        unsigned char *row = data + y * texture.footprint.Footprint.RowPitch;
        if (kind == 0) {
            auto pixel = reinterpret_cast<uint16_t *>(row + x * 8);
            pixel[0] = half(.05f + .8f * x / width); pixel[1] = half(.05f + .8f * y / height);
            pixel[2] = half(((x / 32 + y / 32) & 1) ? .15f : .65f); pixel[3] = half(1.f);
        } else if (kind == 2) reinterpret_cast<float *>(row)[x] = .5f;
    }
    texture.upload->Unmap(0, nullptr);
    D3D12_TEXTURE_COPY_LOCATION source{}, target{};
    source.pResource = texture.upload; source.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    source.PlacedFootprint = texture.footprint;
    target.pResource = texture.resource; target.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    list->CopyTextureRegion(&target, 0, 0, 0, &source, nullptr);
    transition(list, texture.resource, D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    return S_OK;
}

int wmain(int argc, wchar_t **argv)
{
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    if (argc < 5 || argc > 6 || (std::wcscmp(argv[2], L"baseline") && std::wcscmp(argv[2], L"neural"))) {
        std::fputs("usage: host_neural.exe host.dll baseline|neural output.bin reference.bin|- [frames>=650]\n", stderr);
        return 1;
    }
    bool neural = !std::wcscmp(argv[2], L"neural");
    unsigned frames = argc == 6 ? _wtoi(argv[5]) : 720;
    if (frames < 650 || frames > 10000 || (neural && !std::wcscmp(argv[4], L"-"))) return 1;
    if (const char *size = std::getenv("AMDNR_TEST_HOST_SIZE")) {
        if (std::sscanf(size, "%ux%u:%ux%u", &width, &height, &out_width, &out_height) != 4 || width < 64 || height < 64 ||
            width > out_width || height > out_height || out_width > 7680 || out_height > 4320) {
            std::fputs("AMDNR_TEST_HOST_SIZE must be RWxRH:OWxOH, render no larger than output, output up to 7680x4320\n", stderr);
            return 1;
        }
    }
    wchar_t full_path[32768];
    DWORD path_length = GetFullPathNameW(argv[1], 32768, full_path, nullptr);
    if (!path_length || path_length >= 32768) return 1;
    std::wstring host_directory = directory(full_path), ini = host_directory + L"\\OptiScaler.ini";
    wchar_t setting[128];
    GetPrivateProfileStringW(L"Upscalers", L"Dx12Upscaler", L"", setting, 128, ini.c_str());
    bool fsr31 = !_wcsicmp(setting, L"fsr31");
    if (!fsr31 && _wcsicmp(setting, L"fsr21")) { std::fputs("Test requires [Upscalers] Dx12Upscaler=fsr21 or fsr31\n", stderr); return 1; }
    const char *upscaler = fsr31 ? "FSR3.1" : "FSR2";
    GetPrivateProfileStringW(L"DlssNr", L"Enabled", L"", setting, 128, ini.c_str());
    if (_wcsicmp(setting, neural ? L"true" : L"false")) { std::fputs("DlssNr Enabled must match test mode\n", stderr); return 1; }
    GetPrivateProfileStringW(L"DlssNr", L"NrBackend", L"", setting, 128, ini.c_str());
    bool daniel = !_wcsicmp(setting, L"daniel");
    if (!daniel && _wcsicmp(setting, L"lmxxf")) { std::fputs("Test requires [DlssNr] NrBackend=lmxxf or daniel\n", stderr); return 1; }
    // danielblnc's runtime is hosted by the pre-SR path, which logs completed jobs to amd_presr.log.
    std::wstring backend_log = host_directory + (daniel ? L"\\amd_presr.log" : L"\\lmxxf_backend.log"),
                 bridge_log = host_directory + L"\\amd_bridge.log";
    uint64_t backend_start = file_size(backend_log), bridge_start = file_size(bridge_log);
    std::printf("Loading released host (runtime %s)\n", daniel ? "daniel" : "lmxxf");
    HMODULE host = LoadLibraryW(full_path);
    if (!host) { std::fprintf(stderr, "host load failed: %lu\n", GetLastError()); return 1; }
    std::puts("Released host loaded");
    // Match the separately validated host/runtime load order without invoking
    // the neural API directly: all rendering below still enters through NGX.
    // The host loads danielblnc's pass DLLs itself, after selecting its HIP device.
    if (!daniel) {
        std::puts("Loading neural runtime dependency");
        if (!LoadLibraryW((host_directory + L"\\LmxxfNrRuntime.dll").c_str())) {
            std::fprintf(stderr, "runtime load failed: %lu\n", GetLastError()); return 1;
        }
    }
    if (!wait_for_log(host_directory + L"\\OptiScaler.log", "Amdxc64Hooks::Init", 20000)) {
        std::fputs("Host GPU identification did not finish; inspect OptiScaler.log\n", stderr); return 1;
    }
    // Use the application's ordinary DXGI import, as games do. The loaded host
    // attaches its factory/swapchain hooks; its proxy export is not an application API.
    std::puts("Creating DXGI factory");
    IDXGIFactory1 *factory = nullptr; HR(CreateDXGIFactory1(IID_PPV_ARGS(&factory)));
    std::puts("DXGI factory created");
    IDXGIAdapter1 *adapter = nullptr; SIZE_T largest = 0;
    for (UINT index = 0;; index++) {
        IDXGIAdapter1 *candidate = nullptr; HRESULT result = factory->EnumAdapters1(index, &candidate);
        if (result == DXGI_ERROR_NOT_FOUND) break;
        HR(result); DXGI_ADAPTER_DESC1 desc{}; HR(candidate->GetDesc1(&desc));
        if (!(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) && desc.DedicatedVideoMemory > largest) {
            if (adapter) adapter->Release();
            adapter = candidate; largest = desc.DedicatedVideoMemory;
        } else candidate->Release();
    }
    if (!adapter) { std::fputs("No hardware adapter\n", stderr); return 77; }
    std::puts("Creating D3D12 device");
    ID3D12Device *device = nullptr; HR(D3D12CreateDevice(adapter, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&device)));
    Ngx ngx{};
#define LOAD(field, name) ngx.field = reinterpret_cast<decltype(ngx.field)>(reinterpret_cast<void *>(GetProcAddress(host, name))); if (!ngx.field) { std::fprintf(stderr, "missing host export %s\n", name); return 1; }
    LOAD(init, "NVSDK_NGX_D3D12_Init_Ext"); LOAD(allocate, "NVSDK_NGX_D3D12_AllocateParameters");
    LOAD(create, "NVSDK_NGX_D3D12_CreateFeature"); LOAD(evaluate, "NVSDK_NGX_D3D12_EvaluateFeature");
    LOAD(release, "NVSDK_NGX_D3D12_ReleaseFeature"); LOAD(destroy_parameters, "NVSDK_NGX_D3D12_DestroyParameters");
    LOAD(shutdown, "NVSDK_NGX_D3D12_Shutdown1");
#undef LOAD
    std::puts("Initializing NGX host");
    NGX(ngx.init(0x1337, host_directory.c_str(), device, 0x15, nullptr));
    std::puts("NGX host initialized");
    ID3D12CommandQueue *queue = nullptr; D3D12_COMMAND_QUEUE_DESC qd{};
    HR(device->CreateCommandQueue(&qd, IID_PPV_ARGS(&queue)));
    WNDCLASSW wc{}; wc.lpfnWndProc = DefWindowProcW; wc.hInstance = GetModuleHandleW(nullptr); wc.lpszClassName = L"AMDNRHostTest";
    if (!RegisterClassW(&wc)) return 1;
    HWND window = CreateWindowExW(0, wc.lpszClassName, L"AMDNR isolated host test", WS_OVERLAPPEDWINDOW,
        0, 0, 160, 120, nullptr, nullptr, wc.hInstance, nullptr);
    if (!window) return 1;
    DXGI_SWAP_CHAIN_DESC sd{}; sd.BufferDesc.Width = out_width; sd.BufferDesc.Height = out_height;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM; sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT; sd.BufferCount = 2;
    sd.OutputWindow = window; sd.Windowed = TRUE; sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    std::puts("Creating host-observed swapchain");
    IDXGISwapChain *swapchain = nullptr; HR(factory->CreateSwapChain(queue, &sd, &swapchain));
    std::puts("Swapchain created");
    ID3D12Fence *fence = nullptr; HR(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence)));
    HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr); if (!event) return 1;
    UINT64 fence_value = 0;
    auto drain = [&] {
        if (FAILED(queue->Signal(fence, ++fence_value)) || FAILED(fence->SetEventOnCompletion(fence_value, event)) ||
            WaitForSingleObject(event, 60000) != WAIT_OBJECT_0) { std::fputs("GPU drain timeout\n", stderr); std::exit(1); }
    };
    ID3D12CommandAllocator *allocator = nullptr; ID3D12GraphicsCommandList *list = nullptr;
    HR(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator)));
    HR(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator, nullptr, IID_PPV_ARGS(&list)));
    auto submit = [&] { if (FAILED(list->Close())) std::exit(1); ID3D12CommandList *lists[] = {list}; queue->ExecuteCommandLists(1, lists); };
    auto reset = [&] { if (FAILED(allocator->Reset()) || FAILED(list->Reset(allocator, nullptr))) std::exit(1); };
    Texture color, motion, depth;
    HR(make_input(device, list, DXGI_FORMAT_R16G16B16A16_FLOAT, 0, color));
    HR(make_input(device, list, DXGI_FORMAT_R16G16_FLOAT, 1, motion));
    HR(make_input(device, list, DXGI_FORMAT_R32_FLOAT, 2, depth));
    D3D12_RESOURCE_DESC od{}; od.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    od.Width = out_width; od.Height = out_height; od.DepthOrArraySize = od.MipLevels = od.SampleDesc.Count = 1;
    od.Format = DXGI_FORMAT_R16G16B16A16_FLOAT; od.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
    D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_DEFAULT;
    ID3D12Resource *output = nullptr, *readback = nullptr;
    HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &od, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, nullptr, IID_PPV_ARGS(&output)));
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{}; UINT64 bytes;
    device->GetCopyableFootprints(&od, 0, 1, 0, &footprint, nullptr, nullptr, &bytes);
    auto bd = buffer_desc(bytes); heap.Type = D3D12_HEAP_TYPE_READBACK;
    HR(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &bd, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&readback)));
    submit(); drain(); reset();
    Parameters *parameters = nullptr; NGX(ngx.allocate(&parameters)); if (!parameters) return 1;
    parameters->ui("HostTest.UInt", 0x1234); parameters->real("HostTest.Float", 1.25f);
    parameters->resource("HostTest.Resource", color.resource);
    unsigned check = 0; float check_float = 0; ID3D12Resource *check_resource = nullptr;
    NGX(parameters->get_ui("HostTest.UInt", &check)); NGX(parameters->get_real("HostTest.Float", &check_float));
    NGX(parameters->get_resource("HostTest.Resource", &check_resource));
    if (check != 0x1234 || check_float != 1.25f || check_resource != color.resource) { std::fputs("NGX parameter ABI mismatch\n", stderr); return 1; }
    parameters->ui("Width", width); parameters->ui("Height", height);
    parameters->ui("OutWidth", out_width); parameters->ui("OutHeight", out_height);
    parameters->integer("PerfQualityValue", 2);
    parameters->integer("DLSS.Feature.Create.Flags", (1 << 0) | (1 << 1) | (1 << 6));
    parameters->ui("CreationNodeMask", 1); parameters->ui("VisibilityNodeMask", 1);
    NgxHandle *feature = nullptr; NGX(ngx.create(list, 1, parameters, &feature));
    if (!feature) return 1;
    submit(); drain(); reset();
    parameters->resource("Color", color.resource); parameters->resource("MotionVectors", motion.resource);
    parameters->resource("Depth", depth.resource); parameters->resource("Output", output);
    parameters->resource("ExposureTexture", nullptr); parameters->resource("TransparencyMask", nullptr);
    parameters->resource("FSR.transparencyAndComposition", nullptr); parameters->resource("FSR.reactive", nullptr);
    parameters->resource("DLSS.Input.Bias.Current.Color.Mask", nullptr);
    parameters->ui("DLSS.Render.Subrect.Dimensions.Width", width); parameters->ui("DLSS.Render.Subrect.Dimensions.Height", height);
    parameters->real("Jitter.Offset.X", 0.f); parameters->real("Jitter.Offset.Y", 0.f);
    parameters->real("MV.Scale.X", 1.f); parameters->real("MV.Scale.Y", 1.f);
    parameters->real("DLSS.Pre.Exposure", 1.f); parameters->real("DLSS.Exposure.Scale", 1.f);
    parameters->real("FrameTimeDeltaInMsec", 16.6667f);
    parameters->real("FSR.cameraNear", .1f); parameters->real("FSR.cameraFar", 1000.f);
    parameters->real("FSR.cameraFovAngleVertical", 1.04719755f);
    std::printf("Host NGX feature=%u mode=%s frames=%u input=%ux%u output=%ux%u\n", feature->id,
                neural ? "neural" : "baseline", frames, width, height, out_width, out_height);
    for (unsigned frame = 0; frame < frames; frame++) {
        parameters->ui("Reset", frame == 0 ? 1 : 0);
        NGX(ngx.evaluate(list, feature, parameters, nullptr));
        ID3D12Resource *restored = nullptr; NGX(parameters->get_resource("Color", &restored));
        if (restored != color.resource) { std::fputs("Host did not restore the input color parameter\n", stderr); return 1; }
        submit(); HR(swapchain->Present(0, 0)); drain(); reset();
        if (frame % 60 == 0) std::printf("host frame=%u\n", frame);
        MSG message; while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&message); DispatchMessageW(&message); }
        Sleep(16); // Let the host's one-frame-late HIP work complete; pass its 300 ms settle gate.
    }
    transition(list, output, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_COPY_SOURCE);
    D3D12_TEXTURE_COPY_LOCATION source{}, target{}; source.pResource = output; source.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    target.pResource = readback; target.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT; target.PlacedFootprint = footprint;
    list->CopyTextureRegion(&target, 0, 0, 0, &source, nullptr);
    transition(list, output, D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    submit(); drain();
    unsigned char *data; HR(readback->Map(0, nullptr, reinterpret_cast<void **>(&data)));
    std::vector<float> values; values.reserve(size_t(out_width) * out_height * 3); double sum = 0;
    for (unsigned y = 0; y < out_height; y++) for (unsigned x = 0; x < out_width; x++) for (unsigned c = 0; c < 3; c++) {
        float value = unhalf(reinterpret_cast<uint16_t *>(data + y * footprint.Footprint.RowPitch + x * 8)[c]);
        if (!std::isfinite(value) || std::fabs(value) > 10000) { std::fputs("Non-finite/out-of-range host output\n", stderr); return 1; }
        values.push_back(value); sum += value;
    }
    readback->Unmap(0, nullptr);
    if (sum < values.size() * .001) { std::fputs("Host output is black/unwritten\n", stderr); return 1; }
    FILE *image = _wfopen(argv[3], L"wb"); if (!image) return 1;
    bool saved = std::fwrite(values.data(), sizeof(float), values.size(), image) == values.size(); std::fclose(image); if (!saved) return 1;
    NGX(ngx.release(feature)); NGX(ngx.destroy_parameters(parameters)); NGX(ngx.shutdown(device)); drain();
    std::string backend = fresh_log(backend_log, backend_start), bridge = fresh_log(bridge_log, bridge_start);
    unsigned model = counter_max(backend, " | model +"), fresh = counter_max(backend, "(fresh +");
    if (daniel) {
        // Completed jobs are logged for the first three frames and every 120th.
        model = fresh = 0;
        for (size_t at = 0; (at = backend.find("Completed AMD pre-SR passes=", at)) != std::string::npos; at++) model++;
        fresh = counter_max(backend, "Completed AMD pre-SR passes=");
    }
    std::printf("Host output mean=%g; fresh backend log bytes=%zu bridge bytes=%zu model_window=%u fresh_window=%u\n",
                sum / values.size(), backend.size(), bridge.size(), model, fresh);
    if (neural) {
        if (!model || !fresh) { std::fputs(daniel ? "No fresh completed danielblnc jobs in amd_presr.log; inspect it and amd_bridge.log\n"
                                                  : "No fresh host log counters prove neural frames reached composition; inspect amd_bridge.log and lmxxf_backend.log\n", stderr); return 1; }
        FILE *reference = _wfopen(argv[4], L"rb"); if (!reference) { std::fputs("Missing baseline output\n", stderr); return 1; }
        std::vector<float> baseline(values.size());
        bool valid = std::fread(baseline.data(), sizeof(float), baseline.size(), reference) == baseline.size() && std::fgetc(reference) == EOF;
        std::fclose(reference); if (!valid) return 1;
        double delta = 0; size_t changed = 0;
        for (size_t i = 0; i < values.size(); i++) {
            if (!std::isfinite(baseline[i])) return 1;
            double d = std::fabs(values[i] - baseline[i]); delta += d; if (d > .001) changed++;
        }
        std::printf("Host NR vs baseline: mean_abs_change=%g changed_channels=%zu/%zu\n", delta / values.size(), changed, values.size());
        if (delta / values.size() < .0001 || changed < values.size() / 100) { std::fputs("Neural host output did not materially differ from the NR-disabled baseline\n", stderr); return 1; }
    } else if (model || fresh) { std::fputs("Baseline unexpectedly ran neural rendering\n", stderr); return 1; }
    // Do not unload the injected host: its hooks retain process-wide state, like in a game.
    swapchain->Release(); DestroyWindow(window); UnregisterClassW(wc.lpszClassName, wc.hInstance);
    list->Release(); allocator->Release(); readback->Release(); output->Release();
    for (Texture *texture : {&color, &motion, &depth}) { texture->upload->Release(); texture->resource->Release(); }
    fence->Release(); CloseHandle(event); queue->Release(); device->Release(); adapter->Release(); factory->Release();
    if (neural) std::printf("PASS: released host NGX/%s/%sNR submission and composition produced finite changed output\n",
                            upscaler, daniel ? "danielblnc " : "");
    else std::printf("PASS: released host NGX/%s baseline produced finite output\n", upscaler);
    return 0;
}
