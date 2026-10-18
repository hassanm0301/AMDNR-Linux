/* SPDX-License-Identifier: GPL-3.0-or-later
 * The hipcc module ABI as compiled Windows modules call it. */
#pragma once
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct { uint32_t x, y, z; } dim3;
struct fat_binary { uint32_t magic, version; const void *binary; void *reserved; };
#define FAT_BINARY_MAGIC 0x48495046u

typedef void **(*register_fat_binary_fn)(const void *);
typedef void (*register_function_fn)(void **, const void *, char *, const char *, unsigned,
                                     void *, void *, dim3 *, dim3 *, int *);
typedef void (*register_var_fn)(void **, void *, char *, char *, int, size_t, int, int);
typedef void (*unregister_fat_binary_fn)(void **);

/* One registered copy of tests/registered.hip, keyed by host addresses. */
struct registered_module {
    void **handle;
    const void *kernel;
    unsigned *scale, *launches;
};

static void *read_bundle(const char *path)
{
    FILE *file = fopen(path, "rb");
    if (!file) return NULL;
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    fseek(file, 0, SEEK_SET);
    void *data = size > 24 ? malloc((size_t)size) : NULL;
    if (data && fread(data, 1, (size_t)size, file) != (size_t)size) { free(data); data = NULL; }
    fclose(file);
    return data;
}

static int register_module(HMODULE hip, struct fat_binary *wrapper, struct registered_module *module)
{
    register_fat_binary_fn fat = (register_fat_binary_fn)(void *)GetProcAddress(hip, "__hipRegisterFatBinary");
    register_function_fn function = (register_function_fn)(void *)GetProcAddress(hip, "__hipRegisterFunction");
    register_var_fn variable = (register_var_fn)(void *)GetProcAddress(hip, "__hipRegisterVar");
    if (!fat || !function || !variable) return 0;
    module->handle = fat(wrapper);
    if (!module->handle) return 0;
    function(module->handle, module->kernel, "scale_add", "scale_add", (unsigned)-1, NULL, NULL, NULL, NULL, NULL);
    variable(module->handle, module->scale, "amdnr_scale", "amdnr_scale", 0, sizeof(unsigned), 1, 0);
    variable(module->handle, module->launches, "amdnr_launches", "amdnr_launches", 0, sizeof(unsigned), 0, 0);
    return 1;
}
