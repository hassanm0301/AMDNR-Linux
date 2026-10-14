/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "native.h"
#include <dlfcn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static pthread_once_t once = PTHREAD_ONCE_INIT;
static void *library;

void amdnr_log(const char *format, ...)
{
    va_list args;
    flockfile(stderr);
    fprintf(stderr, "amdnr-hip: ");
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
    funlockfile(stderr);
}

static void load_hip(void)
{
    const char *override = getenv("AMDNR_HIP_LIBRARY");
    if (override && *override)
        library = dlopen(override, RTLD_NOW | RTLD_LOCAL);
    else
    {
        library = dlopen("libamdhip64.so.7", RTLD_NOW | RTLD_LOCAL);
        if (!library) library = dlopen("libamdhip64.so", RTLD_NOW | RTLD_LOCAL);
    }
    if (!library) amdnr_log("cannot load Linux HIP: %s", dlerror());
    else amdnr_log("Linux HIP loaded (%s)", override && *override ? override : "ROCm search path");
    /* HIP owns process-lifetime worker threads. Never dlclose this library. */
}

void *amdnr_symbol(const char *name)
{
    pthread_once(&once, load_hip);
    if (getenv("AMDNR_HIP_TRACE")) amdnr_log("%s", name);
    return library ? dlsym(library, name) : NULL;
}
