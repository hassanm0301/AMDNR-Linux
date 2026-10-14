/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define LOAD(name, args) int (*name) args = (void *)GetProcAddress(hip, #name); if (!name) { fprintf(stderr, "missing %s\n", #name); return 1; }
#define CHECK(call) do { int rc = (call); if (rc) { fprintf(stderr, "%s failed: %d\n", #call, rc); return 1; } } while (0)
int main(int argc, char **argv)
{
    HMODULE hip = LoadLibraryExW(L"amdhip64_7.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!hip) { fprintf(stderr, "HIP load failed: %lu\n", GetLastError()); return 1; }
    LOAD(hipInit, (unsigned)); LOAD(hipGetDeviceCount, (int *)); LOAD(hipSetDevice, (int));
    LOAD(hipRuntimeGetVersion, (int *)); LOAD(hipGetDevicePropertiesR0600, (void *, int));
    LOAD(hipMalloc, (void **, size_t)); LOAD(hipFree, (void *)); LOAD(hipMemcpy, (void *, const void *, size_t, int));
    LOAD(hipStreamCreate, (void **)); LOAD(hipStreamSynchronize, (void *)); LOAD(hipStreamDestroy, (void *));
    LOAD(hipModuleLoad, (void **, const char *)); LOAD(hipModuleUnload, (void *));
    LOAD(hipModuleGetFunction, (void **, void *, const char *));
    LOAD(hipModuleLaunchKernel, (void *, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, void *, void **, void **));
    CHECK(hipInit(0)); int count = 0, version = 0;
    CHECK(hipGetDeviceCount(&count)); CHECK(hipRuntimeGetVersion(&version));
    printf("HIP runtime=%d devices=%d\n", version, count);
    int selected = -1;
    for (int i = 0; i < count; i++)
    {
        _Alignas(16) unsigned char properties[1472] = {0};
        CHECK(hipGetDevicePropertiesR0600(properties, i));
        printf("device=%d name=%s arch=%s LUID=%08x:%08x\n", i, properties, properties + 1160,
               *(uint32_t *)(properties + 276), *(uint32_t *)(properties + 272));
        if (!strncmp((char *)properties + 1160, "gfx1201", 7)) selected = i;
    }
    if (selected < 0) { fprintf(stderr, "test kernel requires gfx1201\n"); return 77; }
    CHECK(hipSetDevice(selected));
    enum { N = 4096 }; unsigned data[N];
    for (unsigned i = 0; i < N; i++) data[i] = i;
    void *pointer = NULL, *stream = NULL, *module = NULL, *kernel = NULL;
    CHECK(hipMalloc(&pointer, sizeof(data)));
    CHECK(hipMemcpy(pointer, data, sizeof(data), 1));
    CHECK(hipStreamCreate(&stream));
    if (argc != 2) { fprintf(stderr, "usage: hip_smoke.exe compute.hsaco\n"); return 1; }
    CHECK(hipModuleLoad(&module, argv[1]));
    CHECK(hipModuleGetFunction(&kernel, module, "transform"));
    unsigned n = N; void *args[] = {&pointer, &n};
    CHECK(hipModuleLaunchKernel(kernel, N / 256, 1, 1, 256, 1, 1, 0, stream, args, NULL));
    CHECK(hipStreamSynchronize(stream));
    CHECK(hipMemcpy(data, pointer, sizeof(data), 2));
    for (unsigned i = 0; i < N; i++) if (data[i] != i * 3 + 7)
    { fprintf(stderr, "wrong GPU result at %u: %u\n", i, data[i]); return 1; }
    CHECK(hipModuleUnload(module)); CHECK(hipStreamDestroy(stream)); CHECK(hipFree(pointer));
    puts("PASS: Windows HIP calls, adapter identity, allocation, copies, HSACO load, GPU kernel, synchronization");
    return 0;
}
