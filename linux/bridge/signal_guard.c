/* SPDX-License-Identifier: GPL-3.0-or-later
 * COMGR's LLVM diagnostics temporarily replace process-wide signal handlers.
 * Re-sending a synchronous signal from LLVM loses the original CPU context;
 * Wine needs that context to implement Windows exceptions. Wine also uses
 * signals to suspend threads. Keep Wine's handlers when COMGR installs its
 * diagnostics, while leaving all other libraries and signal actions alone.
 * This is a native preload library, separate from the Wine builtin DLL.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <elf.h>
#include <errno.h>
#include <link.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static pthread_once_t once = PTHREAD_ONCE_INIT;
static int (*next_sigaction)(int, const struct sigaction *, struct sigaction *);
static int trace_enabled;

/* Both objects stay loaded for the lifetime of the Wine process. Publish an
 * immutable snapshot once, so LLVM's signal-handler restoration path never
 * acquires the dynamic loader lock (dladdr/dlsym are not signal-safe). */
#define MAX_LOAD_RANGES 16
struct image_ranges
{
    uintptr_t first[MAX_LOAD_RANGES], last[MAX_LOAD_RANGES];
    unsigned count;
};
struct module_ranges
{
    struct image_ranges comgr, wine;
};
static struct module_ranges modules;
static atomic_uint ranges_state; /* 0: unknown, 1: discovering, 2: published */
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "Signal-path atomics must be lock-free");

static void resolve_sigaction(void)
{
    next_sigaction = dlsym(RTLD_NEXT, "sigaction");
    trace_enabled = getenv("AMDNR_SIGNAL_TRACE") != NULL;
}

__attribute__((constructor)) static void initialize(void)
{
    pthread_once(&once, resolve_sigaction);
}

static int contains(const struct image_ranges *ranges, uintptr_t address)
{
    for (unsigned i = 0; i < ranges->count; ++i)
        if (address >= ranges->first[i] && address < ranges->last[i]) return 1;
    return 0;
}

struct range_query
{
    uintptr_t base;
    struct image_ranges *ranges;
};

static int find_ranges(struct dl_phdr_info *info, size_t size, void *opaque)
{
    (void)size;
    struct range_query *query = opaque;
    if ((uintptr_t)info->dlpi_addr != query->base) return 0;
    for (unsigned i = 0; i < info->dlpi_phnum; ++i)
    {
        const ElfW(Phdr) *phdr = &info->dlpi_phdr[i];
        if (phdr->p_type != PT_LOAD || !(phdr->p_flags & PF_X) || !phdr->p_memsz) continue;
        unsigned n = query->ranges->count;
        uintptr_t first = query->base + phdr->p_vaddr;
        uintptr_t last = first + phdr->p_memsz;
        if (n == MAX_LOAD_RANGES || first < query->base || last < first)
        {
            query->ranges->count = 0;
            return 1;
        }
        query->ranges->first[n] = first;
        query->ranges->last[n] = last;
        query->ranges->count = n + 1;
    }
    return 1;
}

static int image_at(uintptr_t address, const char *name, int versioned,
                    struct image_ranges *ranges)
{
    Dl_info info;
    if (!dladdr((void *)address, &info) || !info.dli_fname) return 0;
    const char *leaf = strrchr(info.dli_fname, '/');
    leaf = leaf ? leaf + 1 : info.dli_fname;
    size_t length = strlen(name);
    if (strncmp(leaf, name, length) ||
        (leaf[length] && !(versioned && leaf[length] == '.'))) return 0;
    struct range_query query = {(uintptr_t)info.dli_fbase, ranges};
    dl_iterate_phdr(find_ranges, &query);
    return contains(ranges, address);
}

static int installing_llvm_handler(const struct sigaction *action)
{
    /* Only the initial ordinary LLVM registration may discover module ranges.
     * In particular, saved Wine actions passed by LLVM's signal handler must
     * not enter the loader. These are the two registration forms in the pinned
     * ROCm LLVM Unix/Signals.inc; SA_RESTORER, if present, is irrelevant. */
    unsigned mask = SA_SIGINFO | SA_NODEFER | SA_RESETHAND | SA_ONSTACK | SA_RESTART;
    unsigned flags = (unsigned)action->sa_flags & mask;
    return flags == (unsigned)(SA_SIGINFO | SA_NODEFER | SA_RESETHAND | SA_ONSTACK) ||
           flags == (unsigned)SA_ONSTACK;
}

static void trace_preserved(int number)
{
    /* Only used for an ordinary COMGR handler installation, never restoration.
     * Keep even this optional path independent of stdio and the allocator. */
    char message[] = "amdnr-signals: preserving Wine handler for signal 00 during COMGR\n";
    char *digits = message + sizeof("amdnr-signals: preserving Wine handler for signal ") - 1;
    digits[0] = (char)('0' + number / 10);
    digits[1] = (char)('0' + number % 10);
    (void)write(STDERR_FILENO, message, sizeof(message) - 1);
}

int sigaction(int signal, const struct sigaction *action, struct sigaction *previous)
{
    /* The constructor resolves libc before normal calls. The fallback covers
     * a different preload's earlier constructor; after resolution no once lock
     * is taken, including on calls from a native signal handler. */
    if (!next_sigaction) pthread_once(&once, resolve_sigaction);
    if (!next_sigaction) { errno = ENOSYS; return -1; }
    if (!action) return next_sigaction(signal, NULL, previous);

    /* Save the requested action before querying, including callers which reuse
     * the same storage for the requested and previous actions. */
    struct sigaction requested = *action;
    uintptr_t caller = (uintptr_t)__builtin_return_address(0);
    uintptr_t replacement = (uintptr_t)requested.sa_sigaction;
    unsigned state = atomic_load_explicit(&ranges_state, memory_order_acquire);
    int saved_errno = errno;
    struct sigaction current;

    if (state == 2)
    {
        /* Restoring a Wine or third-party action takes this fast path: no
         * loader calls, logging, allocation or extra signal-action query. */
        if (!contains(&modules.comgr, caller) || !contains(&modules.comgr, replacement))
            return next_sigaction(signal, &requested, previous);
    }
    else
    {
        if (state != 0 || !installing_llvm_handler(&requested) ||
            requested.sa_handler == SIG_DFL || requested.sa_handler == SIG_IGN)
            return next_sigaction(signal, &requested, previous);

        if (next_sigaction(signal, NULL, &current)) return -1;
        if (current.sa_handler == SIG_DFL || current.sa_handler == SIG_IGN)
        {
            errno = saved_errno;
            return next_sigaction(signal, &requested, previous);
        }

        unsigned unknown = 0;
        if (atomic_compare_exchange_strong_explicit(&ranges_state, &unknown, 1,
                                                    memory_order_acq_rel, memory_order_acquire))
        {
            struct module_ranges found = {0};
            int ok = image_at(caller, "libamd_comgr.so", 1, &found.comgr) &&
                     contains(&found.comgr, replacement) &&
                     image_at((uintptr_t)current.sa_sigaction, "ntdll.so", 0, &found.wine);
            if (ok) modules = found;
            atomic_store_explicit(&ranges_state, ok ? 2 : 0, memory_order_release);
            state = ok ? 2 : 0;
        }
        else state = unknown;

        if (state != 2 || !contains(&modules.comgr, caller) || !contains(&modules.comgr, replacement))
        {
            errno = saved_errno;
            return next_sigaction(signal, &requested, previous);
        }
    }

    if (next_sigaction(signal, NULL, &current)) return -1;
    if (contains(&modules.wine, (uintptr_t)current.sa_sigaction))
    {
        if (previous) *previous = current;
        if (trace_enabled && installing_llvm_handler(&requested)) trace_preserved(signal);
        errno = saved_errno;
        return 0;
    }
    errno = saved_errno;
    return next_sigaction(signal, &requested, previous);
}
