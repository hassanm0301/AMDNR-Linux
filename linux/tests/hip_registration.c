/* SPDX-License-Identifier: GPL-3.0-or-later
 * The module ABI used by hipcc-compiled DLLs (danielblnc's runtime): fat
 * binary, kernel and variable registration, host-stub launches through
 * push/pop call configuration, symbol copies, occupancy and stream priorities.
 * One copy registers before hipInit (lazy), as a module loaded at process
 * start does; a second registers from a DLL constructor after hipInit
 * (eager), as a runtime loaded by the host does, and is unregistered by
 * unloading that DLL while the first copy keeps running. */
#include "hip_registration.h"
#include <string.h>

#define LOAD(name, args) int (*name) args = (void *)GetProcAddress(hip, #name); if (!name) { fprintf(stderr, "missing %s\n", #name); return 1; }
#define CHECK(call) do { int rc = (call); if (rc) { fprintf(stderr, "%s failed: %d\n", #call, rc); return 1; } } while (0)
enum { N = 4096, BLOCK = 256 };

static unsigned scale_shadow, launches_shadow;
static void kernel_stub(void) {}

int main(int argc, char **argv)
{
    if (argc != 2) { fprintf(stderr, "usage: hip_registration.exe registered.hipfb\n"); return 1; }
    HMODULE hip = LoadLibraryExW(L"amdhip64_7.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!hip) { fprintf(stderr, "HIP load failed: %lu\n", GetLastError()); return 1; }
    LOAD(hipInit, (unsigned)); LOAD(hipGetDeviceCount, (int *)); LOAD(hipSetDevice, (int));
    LOAD(hipGetDevicePropertiesR0600, (void *, int));
    LOAD(hipMalloc, (void **, size_t)); LOAD(hipFree, (void *)); LOAD(hipMemcpy, (void *, const void *, size_t, int));
    LOAD(hipStreamCreateWithPriority, (void **, unsigned, int)); LOAD(hipStreamSynchronize, (void *));
    LOAD(hipStreamDestroy, (void *)); LOAD(hipDeviceGetStreamPriorityRange, (int *, int *));
    LOAD(__hipPushCallConfiguration, (dim3, dim3, size_t, void *));
    LOAD(__hipPopCallConfiguration, (dim3 *, dim3 *, size_t *, void **));
    LOAD(hipLaunchKernel, (const void *, dim3, dim3, void **, size_t, void *));
    LOAD(hipMemcpyToSymbol, (const void *, const void *, size_t, size_t, int));
    LOAD(hipMemcpyToSymbolAsync, (const void *, const void *, size_t, size_t, int, void *));
    LOAD(hipMemcpyFromSymbol, (void *, const void *, size_t, size_t, int));
    LOAD(hipGetSymbolAddress, (void **, const void *)); LOAD(hipGetSymbolSize, (size_t *, const void *));
    LOAD(hipOccupancyMaxActiveBlocksPerMultiprocessor, (int *, const void *, int, size_t));
    register_fat_binary_fn register_fat = (register_fat_binary_fn)(void *)GetProcAddress(hip, "__hipRegisterFatBinary");
    register_function_fn register_function = (register_function_fn)(void *)GetProcAddress(hip, "__hipRegisterFunction");
    if (!register_fat || !register_function) { fputs("missing registration exports\n", stderr); return 1; }

    void *bundle = read_bundle(argv[1]);
    if (!bundle || memcmp(bundle, "__CLANG_OFFLOAD_BUNDLE__", 24)) { fputs("invalid offload bundle\n", stderr); return 1; }
    /* Malformed wrappers are refused before native HIP sees them, and the
     * NULL handle then makes kernel registration a harmless no-op. */
    struct fat_binary invalid = {0x46504948u, 1, bundle, NULL};
    if (register_fat(&invalid)) { fputs("accepted a wrapper with the wrong magic\n", stderr); return 1; }
    register_function(NULL, (const void *)kernel_stub, "scale_add", "scale_add", (unsigned)-1, NULL, NULL, NULL, NULL, NULL);
    struct fat_binary wrapper = {FAT_BINARY_MAGIC, 1, bundle, NULL};
    struct registered_module early = {NULL, (const void *)kernel_stub, &scale_shadow, &launches_shadow};
    if (!register_module(hip, &wrapper, &early)) { fputs("lazy fat-binary registration failed\n", stderr); return 1; }

    CHECK(hipInit(0));
    int count = 0, selected = -1;
    CHECK(hipGetDeviceCount(&count));
    for (int i = 0; i < count; i++)
    {
        _Alignas(16) unsigned char properties[1472] = {0};
        CHECK(hipGetDevicePropertiesR0600(properties, i));
        if (!strncmp((char *)properties + 1160, "gfx1201", 7)) selected = i;
    }
    if (selected < 0) { fprintf(stderr, "test kernel requires gfx1201\n"); return 77; }
    CHECK(hipSetDevice(selected));
    int least = 0, greatest = 0;
    CHECK(hipDeviceGetStreamPriorityRange(&least, &greatest));
    if (greatest > least) { fprintf(stderr, "invalid stream priority range %d..%d\n", least, greatest); return 1; }
    void *stream = NULL, *device = NULL;
    CHECK(hipStreamCreateWithPriority(&stream, 1, greatest));
    CHECK(hipMalloc(&device, N * sizeof(unsigned)));

    if (!SetEnvironmentVariableA("AMDNR_TEST_BUNDLE", argv[1])) return 1;
    HMODULE dll = LoadLibraryW(L"hip_registration_module.dll");
    if (!dll) { fprintf(stderr, "module load failed: %lu\n", GetLastError()); return 1; }
    const struct registered_module *(*loaded)(void) = (void *)GetProcAddress(dll, "registered_module");
    const struct registered_module *late = loaded ? loaded() : NULL;
    if (!late) { fputs("eager registration from DllMain failed\n", stderr); return 1; }

    const struct registered_module *modules[] = {&early, late, &early};
    for (unsigned round = 0; round < 3; round++)
    {
        const struct registered_module *module = modules[round];
        if (round == 2)
        {
            /* Unloading runs the DLL destructor's unregistration while the
             * process lives. The other registration must stay usable. */
            if (!FreeLibrary(dll)) return 1;
            dll = NULL;
        }
        unsigned scale = 3 + round, offset = 11 * (round + 1), zero = 0, launches = 0, host[N];
        for (unsigned i = 0; i < N; i++) host[i] = i;
        CHECK(hipMemcpy(device, host, sizeof(host), 1));
        CHECK(hipMemcpyToSymbol(module->scale, &scale, sizeof(scale), 0, 1));
        CHECK(hipMemcpyToSymbolAsync(module->launches, &zero, sizeof(zero), 0, 1, stream));
        CHECK(hipStreamSynchronize(stream));
        void *address = NULL; size_t bytes = 0;
        CHECK(hipGetSymbolAddress(&address, module->scale));
        CHECK(hipGetSymbolSize(&bytes, module->scale));
        if (!address || bytes != sizeof(unsigned)) { fprintf(stderr, "symbol %p has %zu bytes\n", address, bytes); return 1; }
        int blocks = 0;
        CHECK(hipOccupancyMaxActiveBlocksPerMultiprocessor(&blocks, module->kernel, BLOCK, 0));
        if (blocks < 1) { fprintf(stderr, "occupancy %d\n", blocks); return 1; }
        /* What a hipcc host stub does: the caller pushes the <<<>>> shape,
         * the stub pops it on the same thread and launches itself. */
        dim3 grid = {N / BLOCK, 1, 1}, block = {BLOCK, 1, 1}, popped_grid, popped_block;
        size_t shared = 0; void *popped_stream = NULL;
        CHECK(__hipPushCallConfiguration(grid, block, 0, stream));
        CHECK(__hipPopCallConfiguration(&popped_grid, &popped_block, &shared, &popped_stream));
        if (memcmp(&grid, &popped_grid, sizeof(grid)) || memcmp(&block, &popped_block, sizeof(block)) ||
            shared || popped_stream != stream) { fputs("call configuration changed in transit\n", stderr); return 1; }
        unsigned n = N; void *arguments[] = {&device, &n, &offset};
        CHECK(hipLaunchKernel(module->kernel, popped_grid, popped_block, arguments, shared, popped_stream));
        CHECK(hipStreamSynchronize(stream));
        CHECK(hipMemcpy(host, device, sizeof(host), 2));
        CHECK(hipMemcpyFromSymbol(&launches, module->launches, sizeof(launches), 0, 2));
        for (unsigned i = 0; i < N; i++) if (host[i] != i * scale + offset)
        { fprintf(stderr, "round %u: wrong GPU result at %u: %u\n", round, i, host[i]); return 1; }
        if (launches != 1) { fprintf(stderr, "round %u: device variable reads %u\n", round, launches); return 1; }
        printf("round=%u scale=%u offset=%u occupancy=%d priority=%d..%d\n", round, scale, offset, blocks, least, greatest);
    }
    CHECK(hipFree(device)); CHECK(hipStreamDestroy(stream));
    puts("PASS: hipcc module ABI: lazy/eager registration, host-stub launches, symbols, occupancy, priorities, unregistration");
    return 0;
}
