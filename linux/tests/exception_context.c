/* SPDX-License-Identifier: GPL-3.0-or-later
 * Regression for native HIP changing Wine's signal/exception context handling.
 * Build with x86_64-w64-mingw32-gcc; optionally pass compute.hsaco to exercise
 * module loading, GPU execution and cleanup before repeating the CPU probes.
 * A background thread continuously probes exceptions during HIP initialization
 * and GPU work. AMDNR_EXCEPTION_CONCURRENT=0 selects the serial-only comparison.
 * No GPU work occurs when AMDNR_EXCEPTION_CPU_ONLY=1 is set.
 */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if !defined(__x86_64__)
#error This regression exercises the Windows x86-64 exception ABI.
#endif

/* Each probe receives its thread's expected stack slot explicitly. The handler
 * uses a fixed thread registry, avoiding heap allocation or dynamically resolved
 * compiler TLS while handling an exception. Entries live for the whole test. */
struct probe_state {
    volatile LONG thread_id;
    volatile uint64_t expected_rsp;
    volatile LONG handled;
};
static struct probe_state probe_states[32];
const uint32_t exception_test_mxcsr = 0x5f80;
const uint16_t exception_test_fpcw = 0x0b7f;
const uint64_t exception_gpr_patterns[12] = {
    UINT64_C(0x1122334455667700), UINT64_C(0x1122334455667701),
    UINT64_C(0x1122334455667702), UINT64_C(0x1122334455667703),
    UINT64_C(0x1122334455667704), UINT64_C(0x1122334455667705),
    UINT64_C(0x1122334455667706), UINT64_C(0x1122334455667707),
    UINT64_C(0x1122334455667708), UINT64_C(0x1122334455667709),
    UINT64_C(0x112233445566770a), UINT64_C(0x112233445566770b)
};
#define PATTERN(n) {UINT64_C(0x0123456789abc000) + (n), UINT64_C(0xfedcba9876543000) + (n)}
_Alignas(16) const uint64_t exception_xmm_patterns[16][2] = {
    PATTERN(0), PATTERN(1), PATTERN(2), PATTERN(3),
    PATTERN(4), PATTERN(5), PATTERN(6), PATTERN(7),
    PATTERN(8), PATTERN(9), PATTERN(10), PATTERN(11),
    PATTERN(12), PATTERN(13), PATTERN(14), PATTERN(15)
};
#undef PATTERN

extern int exception_probe_div(volatile uint64_t *expected_rsp);
extern int exception_probe_access(volatile uint64_t *expected_rsp);
extern const unsigned char exception_div_fault[], exception_access_fault[];

#define STR_INNER(x) #x
#define STR(x) STR_INNER(x)
#define SAVE_GPR(r) "pushq %" #r "\n.seh_pushreg " #r "\n"
#define SAVE_XMM(n, offset) "movdqu %xmm" STR(n) ", " STR(offset) "(%rsp)\n.seh_savexmm xmm" STR(n) ", " STR(offset) "\n"
#define RESTORE_XMM(n, offset) "movdqu " STR(offset) "(%rsp), %xmm" STR(n) "\n"
#define LOAD_XMM(n) "movdqu exception_xmm_patterns+16*" STR(n) "(%rip), %xmm" STR(n) "\n"
#define CHECK_XMM(n) "pcmpeqb exception_xmm_patterns+16*" STR(n) "(%rip), %xmm" STR(n) "\npmovmskb %xmm" STR(n) ", %eax\ncmpl $65535, %eax\njne 1f\n"
#define LOAD_GPR(r, n) "movq exception_gpr_patterns+8*" STR(n) "(%rip), %" #r "\n"
#define CHECK_GPR(r, n) "cmpq exception_gpr_patterns+8*" STR(n) "(%rip), %" #r "\njne 1f\n"
#define PROBE(name, fault, instruction) \
    ".text\n.p2align 4\n.globl " #name "\n.def " #name "; .scl 2; .type 32; .endef\n" \
    ".seh_proc " #name "\n" #name ":\n" \
    SAVE_GPR(rbx) SAVE_GPR(rbp) SAVE_GPR(rsi) SAVE_GPR(rdi) \
    SAVE_GPR(r12) SAVE_GPR(r13) SAVE_GPR(r14) SAVE_GPR(r15) \
    "subq $184, %rsp\n.seh_stackalloc 184\n" \
    SAVE_XMM(6, 0) SAVE_XMM(7, 16) SAVE_XMM(8, 32) SAVE_XMM(9, 48) \
    SAVE_XMM(10, 64) SAVE_XMM(11, 80) SAVE_XMM(12, 96) SAVE_XMM(13, 112) \
    SAVE_XMM(14, 128) SAVE_XMM(15, 144) \
    ".seh_endprologue\n" \
    "stmxcsr 160(%rsp)\nfnstcw 164(%rsp)\n" \
    "ldmxcsr exception_test_mxcsr(%rip)\nfldcw exception_test_fpcw(%rip)\n" \
    "movq %rcx, 176(%rsp)\nmovq %rsp, (%rcx)\n" \
    LOAD_GPR(rbx, 0) LOAD_GPR(rbp, 1) LOAD_GPR(rsi, 2) LOAD_GPR(rdi, 3) \
    LOAD_GPR(r8, 4) LOAD_GPR(r9, 5) LOAD_GPR(r10, 6) LOAD_GPR(r11, 7) \
    LOAD_GPR(r12, 8) LOAD_GPR(r13, 9) LOAD_GPR(r14, 10) LOAD_GPR(r15, 11) \
    LOAD_XMM(0) LOAD_XMM(1) LOAD_XMM(2) LOAD_XMM(3) \
    LOAD_XMM(4) LOAD_XMM(5) LOAD_XMM(6) LOAD_XMM(7) \
    LOAD_XMM(8) LOAD_XMM(9) LOAD_XMM(10) LOAD_XMM(11) \
    LOAD_XMM(12) LOAD_XMM(13) LOAD_XMM(14) LOAD_XMM(15) \
    "xorl %eax, %eax\nxorl %ecx, %ecx\nxorl %edx, %edx\n" \
    ".globl " #fault "\n" #fault ":\n" instruction "\n" \
    "cmpq $0x123, %rax\njne 1f\ncmpq $0x456, %rdx\njne 1f\n" \
    "testq %rcx, %rcx\njne 1f\n" \
    CHECK_GPR(rbx, 0) CHECK_GPR(rbp, 1) CHECK_GPR(rsi, 2) CHECK_GPR(rdi, 3) \
    CHECK_GPR(r8, 4) CHECK_GPR(r9, 5) CHECK_GPR(r10, 6) CHECK_GPR(r11, 7) \
    CHECK_GPR(r12, 8) CHECK_GPR(r13, 9) CHECK_GPR(r14, 10) CHECK_GPR(r15, 11) \
    "movq 176(%rsp), %rax\ncmpq (%rax), %rsp\njne 1f\n" \
    CHECK_XMM(0) CHECK_XMM(1) CHECK_XMM(2) CHECK_XMM(3) \
    CHECK_XMM(4) CHECK_XMM(5) CHECK_XMM(6) CHECK_XMM(7) \
    CHECK_XMM(8) CHECK_XMM(9) CHECK_XMM(10) CHECK_XMM(11) \
    CHECK_XMM(12) CHECK_XMM(13) CHECK_XMM(14) CHECK_XMM(15) \
    "stmxcsr 168(%rsp)\ncmpl $0x5f80, 168(%rsp)\njne 1f\n" \
    "fnstcw 172(%rsp)\ncmpw $0x0b7f, 172(%rsp)\njne 1f\n" \
    "xorl %eax, %eax\njmp 2f\n1: movl $1, %eax\n2:\n" \
    "ldmxcsr 160(%rsp)\nfldcw 164(%rsp)\n" \
    RESTORE_XMM(6, 0) RESTORE_XMM(7, 16) RESTORE_XMM(8, 32) RESTORE_XMM(9, 48) \
    RESTORE_XMM(10, 64) RESTORE_XMM(11, 80) RESTORE_XMM(12, 96) RESTORE_XMM(13, 112) \
    RESTORE_XMM(14, 128) RESTORE_XMM(15, 144) \
    "addq $184, %rsp\npopq %r15\npopq %r14\npopq %r13\npopq %r12\n" \
    "popq %rdi\npopq %rsi\npopq %rbp\npopq %rbx\nret\n.seh_endproc\n"

__asm__(PROBE(exception_probe_div, exception_div_fault, "divl %ecx")
        PROBE(exception_probe_access, exception_access_fault, "movl (%rax), %eax"));

static volatile LONG handled, bad_gpr, bad_xmm, bad_fp;
static HANDLE worker_start, worker_done;
static volatile LONG worker_stop;
static DWORD worker_result;
static HANDLE stress_ready;
static volatile LONG stress_stop, stress_batches;

static struct probe_state *thread_state(int create)
{
    LONG id = (LONG)GetCurrentThreadId();
    for (unsigned i = 0; i < sizeof(probe_states) / sizeof(probe_states[0]); i++) {
        if (probe_states[i].thread_id == id) return &probe_states[i];
        if (create && !probe_states[i].thread_id &&
            !InterlockedCompareExchange(&probe_states[i].thread_id, id, 0)) return &probe_states[i];
    }
    return NULL;
}

static LONG WINAPI handler(EXCEPTION_POINTERS *exception)
{
    CONTEXT *c = exception->ContextRecord;
    const void *ip = (void *)(uintptr_t)c->Rip;
    DWORD code = exception->ExceptionRecord->ExceptionCode;
    if (!((ip == exception_div_fault && code == EXCEPTION_INT_DIVIDE_BY_ZERO) ||
          (ip == exception_access_fault && code == EXCEPTION_ACCESS_VIOLATION)))
        return EXCEPTION_CONTINUE_SEARCH;
    struct probe_state *state = thread_state(0);
    if (!state) return EXCEPTION_CONTINUE_SEARCH;
    const uint64_t gpr[] = {c->Rbx, c->Rbp, c->Rsi, c->Rdi, c->R8, c->R9,
                           c->R10, c->R11, c->R12, c->R13, c->R14, c->R15};
    if (c->Rax || c->Rcx || c->Rdx || c->Rsp != state->expected_rsp ||
        memcmp(gpr, exception_gpr_patterns, sizeof(gpr))) InterlockedIncrement(&bad_gpr);
    if (memcmp(c->FltSave.XmmRegisters, exception_xmm_patterns, sizeof(exception_xmm_patterns)))
        InterlockedIncrement(&bad_xmm);
    if (c->MxCsr != exception_test_mxcsr || c->FltSave.MxCsr != exception_test_mxcsr ||
        c->FltSave.ControlWord != exception_test_fpcw) InterlockedIncrement(&bad_fp);
    c->Rax = 0x123;
    c->Rdx = 0x456;
    c->Rip += 2;
    InterlockedIncrement(&state->handled);
    InterlockedIncrement(&handled);
    return EXCEPTION_CONTINUE_EXECUTION;
}

static DWORD WINAPI exercise(void *unused)
{
    (void)unused;
    struct probe_state *state = thread_state(1);
    if (!state) return 1;
    LONG before = state->handled;
    for (unsigned i = 0; i < 16; i++)
        if (exception_probe_div(&state->expected_rsp) || exception_probe_access(&state->expected_rsp)) return 1;
    return state->handled - before == 32 ? 0 : 1;
}

static DWORD WINAPI concurrent_worker(void *unused)
{
    (void)unused;
    if (exercise(NULL)) { SetEvent(stress_ready); return 1; }
    InterlockedIncrement(&stress_batches);
    SetEvent(stress_ready);
    while (!InterlockedCompareExchange(&stress_stop, 0, 0)) {
        if (exercise(NULL) || bad_gpr || bad_xmm || bad_fp) return 1;
        InterlockedIncrement(&stress_batches);
    }
    return 0;
}

static DWORD WINAPI persistent_worker(void *unused)
{
    (void)unused;
    for (;;) {
        if (WaitForSingleObject(worker_start, INFINITE) != WAIT_OBJECT_0) return 1;
        if (worker_stop) return 0;
        worker_result = exercise(NULL);
        SetEvent(worker_done);
    }
}

static int check_stage(const char *stage)
{
    LONG before = handled;
    printf("Checking exception context: %s\n", stage); fflush(stdout);
    if (exercise(NULL)) { fprintf(stderr, "caller register restore failed\n"); return 1; }
    SetEvent(worker_start);
    if (WaitForSingleObject(worker_done, 10000) != WAIT_OBJECT_0 || worker_result) {
        fprintf(stderr, "existing thread register restore failed\n"); return 1;
    }
    HANDLE fresh = CreateThread(NULL, 0, exercise, NULL, 0, NULL);
    DWORD result = 1;
    if (!fresh || WaitForSingleObject(fresh, 10000) != WAIT_OBJECT_0 ||
        !GetExitCodeThread(fresh, &result) || result) {
        fprintf(stderr, "fresh thread register restore failed\n"); return 1;
    }
    CloseHandle(fresh);
    if (handled - before < 96 || bad_gpr || bad_xmm || bad_fp) {
        fprintf(stderr, "context mismatch: handled=%ld gpr=%ld xmm=%ld fp=%ld\n",
                handled - before, bad_gpr, bad_xmm, bad_fp); return 1;
    }
    printf("OK: %s, 96 foreground exceptions; background batches=%ld\n", stage, stress_batches); fflush(stdout);
    return 0;
}

#define LOAD(name, args) int (*name) args = (void *)GetProcAddress(hip, #name); \
    if (!name) { fprintf(stderr, "missing %s\n", #name); return 1; }
#define HIP(call) do { int rc = (call); if (rc) { fprintf(stderr, "%s: %d\n", #call, rc); return 1; } } while (0)

static int exercise_hip(int argc, char **argv)
{
    HMODULE hip = LoadLibraryExW(L"amdhip64_7.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!hip) { fprintf(stderr, "HIP load failed: %lu\n", GetLastError()); return 1; }
    LOAD(hipInit, (unsigned)); LOAD(hipGetDeviceCount, (int *)); LOAD(hipSetDevice, (int));
    LOAD(hipGetDevicePropertiesR0600, (void *, int));
    HIP(hipInit(0));
    if (check_stage("after HIP initialization")) return 1;
    if (argc == 1) { puts("Module/kernel checks skipped: no compute.hsaco argument"); return 0; }
    int count = 0, selected = -1;
    HIP(hipGetDeviceCount(&count));
    for (int i = 0; i < count; i++) {
        _Alignas(16) unsigned char properties[1472] = {0};
        HIP(hipGetDevicePropertiesR0600(properties, i));
        if (!strncmp((const char *)properties + 1160, "gfx1201", 7)) selected = i;
    }
    if (selected < 0) { fprintf(stderr, "compute.hsaco requires gfx1201\n"); return 77; }
    HIP(hipSetDevice(selected));
    LOAD(hipModuleLoad, (void **, const char *)); LOAD(hipModuleUnload, (void *));
    LOAD(hipModuleGetFunction, (void **, void *, const char *));
    LOAD(hipModuleLaunchKernel, (void *, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned,
                                unsigned, void *, void **, void **));
    LOAD(hipMalloc, (void **, size_t)); LOAD(hipFree, (void *));
    LOAD(hipMemcpy, (void *, const void *, size_t, int));
    LOAD(hipStreamCreate, (void **)); LOAD(hipStreamSynchronize, (void *)); LOAD(hipStreamDestroy, (void *));
    void *module = NULL, *kernel = NULL, *pointer = NULL, *stream = NULL;
    HIP(hipModuleLoad(&module, argv[1]));
    HIP(hipModuleGetFunction(&kernel, module, "transform"));
    if (check_stage("after HIP module loading")) return 1;
    enum { N = 4096 };
    unsigned values[N];
    for (unsigned i = 0; i < N; i++) values[i] = i;
    HIP(hipMalloc(&pointer, sizeof(values))); HIP(hipStreamCreate(&stream));
    HIP(hipMemcpy(pointer, values, sizeof(values), 1));
    unsigned n = N; void *arguments[] = {&pointer, &n};
    HIP(hipModuleLaunchKernel(kernel, N / 256, 1, 1, 256, 1, 1, 0, stream, arguments, NULL));
    HIP(hipStreamSynchronize(stream));
    HIP(hipMemcpy(values, pointer, sizeof(values), 2));
    for (unsigned i = 0; i < N; i++) if (values[i] != i * 3 + 7) {
        fprintf(stderr, "GPU result mismatch at %u\n", i); return 1;
    }
    if (check_stage("after GPU kernel/copies")) return 1;
    HIP(hipStreamDestroy(stream)); HIP(hipFree(pointer)); HIP(hipModuleUnload(module));
    return check_stage("after HIP cleanup");
}

int main(int argc, char **argv)
{
    if (argc > 2) { fprintf(stderr, "usage: exception_context.exe [compute.hsaco]\n"); return 1; }
    void *registration = AddVectoredExceptionHandler(1, handler);
    worker_start = CreateEventW(NULL, FALSE, FALSE, NULL);
    worker_done = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!registration || !worker_start || !worker_done) return 1;
    HANDLE worker = CreateThread(NULL, 0, persistent_worker, NULL, 0, NULL);
    if (!worker) return 1;
    int result = check_stage("before HIP");
    const char *cpu_only = getenv("AMDNR_EXCEPTION_CPU_ONLY");
    const char *concurrent = getenv("AMDNR_EXCEPTION_CONCURRENT");
    HANDLE stress = NULL;
    if (!result && !(cpu_only && !strcmp(cpu_only, "1")) && !(concurrent && !strcmp(concurrent, "0"))) {
        stress_ready = CreateEventW(NULL, FALSE, FALSE, NULL);
        if (!stress_ready) result = 1;
        else stress = CreateThread(NULL, 0, concurrent_worker, NULL, 0, NULL);
        if (!stress || WaitForSingleObject(stress_ready, 10000) != WAIT_OBJECT_0) result = 1;
        if (!result) { puts("Concurrent Windows exception stress active during HIP/module/kernel work"); fflush(stdout); }
    }
    if (!result && !(cpu_only && !strcmp(cpu_only, "1"))) result = exercise_hip(argc, argv);
    if (stress) {
        InterlockedExchange(&stress_stop, 1);
        DWORD stress_result = 1;
        if (WaitForSingleObject(stress, 10000) != WAIT_OBJECT_0 ||
            !GetExitCodeThread(stress, &stress_result) || stress_result || !stress_batches) result = 1;
        CloseHandle(stress);
        printf("Concurrent stress completed: batches=%ld exceptions=%lld gpr=%ld xmm=%ld fp=%ld result=%lu\n",
                stress_batches, (long long)stress_batches * 32, bad_gpr, bad_xmm, bad_fp, stress_result);
    }
    if (stress_ready) CloseHandle(stress_ready);
    worker_stop = 1;
    SetEvent(worker_start);
    if (WaitForSingleObject(worker, 10000) != WAIT_OBJECT_0) return 1;
    CloseHandle(worker); CloseHandle(worker_start); CloseHandle(worker_done);
    RemoveVectoredExceptionHandler(registration);
    if (!result) puts("PASS: exception GPR/XMM/FP context and register restoration survived all tested stages");
    return result;
}
