/* SPDX-License-Identifier: GPL-3.0-or-later */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <link.h>
#include <stdatomic.h>

/* Count lookups without adding a loader lock to the path under test. */
static atomic_uint calls;
static int (*next_dladdr)(const void *, Dl_info *);
static int (*next_iterate)(int (*)(struct dl_phdr_info *, size_t, void *), void *);
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "signal-path test counter must be lock-free");

__attribute__((constructor)) static void initialize(void)
{
    next_dladdr = dlsym(RTLD_NEXT, "dladdr");
    next_iterate = dlsym(RTLD_NEXT, "dl_iterate_phdr");
}

unsigned loader_calls(void) { return atomic_load_explicit(&calls, memory_order_relaxed); }

int dladdr(const void *address, Dl_info *info)
{
    atomic_fetch_add_explicit(&calls, 1, memory_order_relaxed);
    return next_dladdr(address, info);
}

int dl_iterate_phdr(int (*callback)(struct dl_phdr_info *, size_t, void *), void *data)
{
    atomic_fetch_add_explicit(&calls, 1, memory_order_relaxed);
    return next_iterate(callback, data);
}
