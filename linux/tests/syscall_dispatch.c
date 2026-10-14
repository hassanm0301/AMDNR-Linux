/* SPDX-License-Identifier: GPL-3.0-or-later
 * Loading an old Wine ELF builtin disables direct Windows syscall dispatch.
 * This CPU-only regression loads the HIP DLL without calling HIP, then checks
 * NtQuerySystemTime on the caller, an existing thread and a new thread.
 *
 * This is deliberately restricted to the exact Wine x86-64 stub below. Its
 * Windows syscall number 0x5a is Linux x86-64 chmod(2). We force the Linux
 * pathname argument to UINTPTR_MAX and mode to zero, so accidental Linux
 * dispatch can only fail with EFAULT and cannot touch any filesystem object.
 * Unknown stub bytes/numbers are SKIP (77), never executed speculatively.
 */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if !defined(__x86_64__)
#error This regression exercises the Wine Linux x86-64 syscall dispatch.
#endif

/* Use the existing syscall instruction, including Wine's return/fallback
 * layout. GE's SIGSYS handler resumes at the instruction following its Wine
 * dispatcher call (eleven bytes after the hardware syscall return address).
 * No executable code or shared Wine state is patched by this test. */
static const unsigned char known_stub[] = {
    0x4c, 0x8b, 0xd1,                         /* mov r10, rcx */
    0xb8, 0x5a, 0x00, 0x00, 0x00,             /* mov eax, 0x5a */
    0xf6, 0x04, 0x25, 0x08, 0x03, 0xfe, 0x7f, 0x01,
    0x75, 0x03, 0x0f, 0x05, 0xc3, 0xeb, 0x01, 0xc3,
    0xff, 0x14, 0x25, 0x00, 0x10, 0xfe, 0x7f, 0xc3
};
enum { KNOWN_SYSCALL = 0x5a, SYSCALL_OFFSET = 0x12, PROBE_COUNT = 16 };
static const void *syscall_instruction;

extern int64_t query_time_direct(LARGE_INTEGER *time, unsigned number, const void *instruction);
__asm__(
    ".text\n.p2align 4\n.globl query_time_direct\n"
    ".def query_time_direct; .scl 2; .type 32; .endef\n"
    ".seh_proc query_time_direct\nquery_time_direct:\n"
    "pushq %rdi\n.seh_pushreg rdi\n"
    "pushq %rsi\n.seh_pushreg rsi\n"
    "subq $40, %rsp\n.seh_stackalloc 40\n.seh_endprologue\n"
    "movq %rcx, %r10\nmovl %edx, %eax\n"
    "movq $-1, %rdi\nxorl %esi, %esi\n"
    "call *%r8\n"
    "addq $40, %rsp\npopq %rsi\npopq %rdi\nret\n.seh_endproc\n"
);

static uint64_t normal_time(void)
{
    FILETIME time;
    GetSystemTimeAsFileTime(&time);
    return ((uint64_t)time.dwHighDateTime << 32) | time.dwLowDateTime;
}

static DWORD exercise(const char *stage)
{
    for (unsigned i = 0; i < PROBE_COUNT; i++) {
        LARGE_INTEGER time;
        time.QuadPart = -1;
        uint64_t before = normal_time();
        int64_t status = query_time_direct(&time, KNOWN_SYSCALL, syscall_instruction);
        uint64_t after = normal_time(), result = (uint64_t)time.QuadPart;
        /* Allow ordinary clock precision differences up to one second, while
         * requiring a written Windows timestamp and exact STATUS_SUCCESS. */
        if (status || time.QuadPart <= 0 || result + UINT64_C(10000000) < before ||
            result > after + UINT64_C(10000000)) {
            printf("FAIL: %s probe=%u status=%lld (0x%llx) time=0x%llx "
                   "normal=[0x%llx,0x%llx]%s\n", stage, i,
                   (long long)status, (unsigned long long)status,
                   (unsigned long long)result, (unsigned long long)before,
                   (unsigned long long)after,
                   status == -14 && time.QuadPart == -1 ?
                   " -- Linux EFAULT; Windows syscall dispatch lost" : "");
            return 1;
        }
    }
    printf("%s: %u direct Windows time queries passed\n", stage, PROBE_COUNT);
    return 0;
}

struct worker {
    HANDLE start, done;
    const char *stage;
    DWORD result;
    int stop;
};

static DWORD WINAPI persistent_worker(void *argument)
{
    struct worker *worker = argument;
    for (;;) {
        if (WaitForSingleObject(worker->start, INFINITE) != WAIT_OBJECT_0) return 1;
        if (worker->stop) return 0;
        worker->result = exercise(worker->stage);
        if (!SetEvent(worker->done)) return 1;
    }
}

static DWORD run_worker(struct worker *worker, const char *stage)
{
    worker->stage = stage;
    if (!SetEvent(worker->start) || WaitForSingleObject(worker->done, 10000) != WAIT_OBJECT_0) {
        printf("FAIL: %s worker synchronization error=%lu\n", stage, GetLastError());
        return 1;
    }
    return worker->result;
}

static DWORD WINAPI fresh_worker(void *unused)
{
    (void)unused;
    return exercise("after load, new thread");
}

int wmain(int argc, wchar_t **argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc > 2) {
        fprintf(stderr, "Usage: syscall_dispatch.exe [HIP DLL path or name]\n");
        return 2;
    }
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    union {
        FARPROC address;
        void (__cdecl *call)(const char **, const char **);
    } get_host;
    get_host.address = GetProcAddress(ntdll, "wine_get_host_version");
    const char *host = NULL, *release = NULL;
    if (get_host.address) get_host.call(&host, &release);
    if (!host || strcmp(host, "Linux")) {
        printf("SKIP: this regression requires Wine on Linux; no direct syscall executed\n");
        return 77;
    }
    FARPROC function = GetProcAddress(ntdll, "NtQuerySystemTime");
    unsigned char bytes[sizeof(known_stub)] = {0};
    SIZE_T received = 0;
    if (!GetProcAddress(ntdll, "wine_get_version") || !function ||
        !ReadProcessMemory(GetCurrentProcess(), (const void *)(uintptr_t)function,
                           bytes, sizeof(bytes), &received) || received != sizeof(bytes) ||
        memcmp(bytes, known_stub, sizeof(bytes))) {
        printf("SKIP: unrecognized Wine NtQuerySystemTime stub; no direct syscall executed. Bytes:");
        for (size_t i = 0; i < received; i++) printf(" %02x", bytes[i]);
        printf("\n");
        return 77;
    }
    syscall_instruction = (const unsigned char *)(uintptr_t)function + SYSCALL_OFFSET;
    printf("Validated Wine syscall stub: number=0x%x, instruction=%p; "
           "Linux fallback constrained to chmod(UINTPTR_MAX, 0)\n",
           KNOWN_SYSCALL, syscall_instruction);

    /* Establish working dispatch before loading the module under test. If the
     * environment never provided it, this is unsupported, not a bridge pass. */
    if (exercise("before load, caller")) {
        printf("SKIP: direct Windows syscalls unavailable before the HIP DLL loads\n");
        return 77;
    }
    struct worker worker = {0};
    worker.start = CreateEventW(NULL, FALSE, FALSE, NULL);
    worker.done = CreateEventW(NULL, FALSE, FALSE, NULL);
    HANDLE thread = worker.start && worker.done ?
        CreateThread(NULL, 0, persistent_worker, &worker, 0, NULL) : NULL;
    if (!thread) {
        printf("FAIL: cannot create existing worker, error=%lu\n", GetLastError());
        return 1;
    }
    DWORD failed = run_worker(&worker, "before load, existing thread");
    if (failed) return 1;

    const wchar_t *library = argc == 2 ? argv[1] : L"amdhip64_7.dll";
    printf("Loading %ls (no HIP initialization or GPU calls)\n", library);
    HMODULE module = LoadLibraryW(library);
    if (!module || !GetProcAddress(module, "hipInit")) {
        printf("FAIL: HIP DLL did not load or lacks hipInit, error=%lu\n", GetLastError());
        return 1;
    }
    wchar_t loaded[MAX_PATH];
    if (GetModuleFileNameW(module, loaded, MAX_PATH)) printf("Loaded %ls\n", loaded);
    failed |= exercise("after load, caller");
    failed |= run_worker(&worker, "after load, existing thread");
    HANDLE fresh = CreateThread(NULL, 0, fresh_worker, NULL, 0, NULL);
    DWORD fresh_result = 1;
    if (!fresh || WaitForSingleObject(fresh, 10000) != WAIT_OBJECT_0 ||
        !GetExitCodeThread(fresh, &fresh_result)) {
        printf("FAIL: new worker did not complete, error=%lu\n", GetLastError());
        failed = 1;
    } else failed |= fresh_result;
    if (fresh) CloseHandle(fresh);
    worker.stop = 1;
    if (!SetEvent(worker.start) || WaitForSingleObject(thread, 10000) != WAIT_OBJECT_0) failed = 1;
    CloseHandle(thread);
    CloseHandle(worker.start);
    CloseHandle(worker.done);
    /* Keep the loaded bridge alive until process exit, as its Unix helpers may
     * have process lifetime. This test does not exercise unload semantics. */
    if (!failed) printf("PASS: %u direct Windows syscall queries preserved across HIP DLL loading "
                        "on caller, existing and new threads\n", PROBE_COUNT * 5);
    return failed ? 1 : 0;
}
