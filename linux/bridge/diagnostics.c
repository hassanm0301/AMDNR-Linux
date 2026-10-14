/* SPDX-License-Identifier: GPL-3.0-or-later
 * Opt-in, observational Windows exception diagnostics. Never handles an
 * exception, changes its context, or writes to game memory. */
#include "bridge.h"
#include <stddef.h>

struct trace_buffer { char bytes[8192]; DWORD used; };
static HANDLE trace_file = INVALID_HANDLE_VALUE;
static PVOID trace_registration;
static volatile LONG initialized, writing, records;
static uintptr_t image_base, wrapper_address, filter_rva;
static int have_filter, stellar_blade;

static void append(struct trace_buffer *b, const char *s)
{
    while (*s && b->used < sizeof(b->bytes)) b->bytes[b->used++] = *s++;
}

static void hex(struct trace_buffer *b, uint64_t value)
{
    static const char digits[] = "0123456789abcdef";
    append(b, "0x");
    for (int shift = 60; shift >= 0; shift -= 4)
        if (b->used < sizeof(b->bytes)) b->bytes[b->used++] = digits[(value >> shift) & 15];
}

static void field(struct trace_buffer *b, const char *name, uint64_t value)
{
    append(b, name); append(b, "="); hex(b, value); append(b, " ");
}

static void write_record(struct trace_buffer *b)
{
    DWORD written;
    WriteFile(trace_file, b->bytes, b->used, &written, NULL);
}

static int read_memory(uintptr_t address, void *out, SIZE_T size)
{
    SIZE_T read = 0;
    return address && size && address <= UINTPTR_MAX - (size - 1) &&
        ReadProcessMemory(GetCurrentProcess(), (const void *)address, out, size, &read) && read == size;
}

static void memory_info(struct trace_buffer *b, const char *label, uintptr_t address)
{
    MEMORY_BASIC_INFORMATION info;
    append(b, label); append(b, ": "); field(b, "address", address);
    if (VirtualQuery((const void *)address, &info, sizeof(info)) == sizeof(info)) {
        field(b, "base", (uintptr_t)info.BaseAddress);
        field(b, "allocation", (uintptr_t)info.AllocationBase);
        field(b, "size", info.RegionSize);
        field(b, "state", info.State);
        field(b, "protect", info.Protect);
        field(b, "allocation_protect", info.AllocationProtect);
        field(b, "type", info.Type);
    } else field(b, "query_error", GetLastError());
    append(b, "\r\n");
}

static void memory_bytes(struct trace_buffer *b, const char *label, uintptr_t address, SIZE_T size)
{
    static const char digits[] = "0123456789abcdef";
    unsigned char bytes[256];
    append(b, label); append(b, ": "); field(b, "address", address); append(b, "\r\n");
    if (size > sizeof(bytes)) size = sizeof(bytes);
    if (!read_memory(address, bytes, size)) {
        append(b, "  unreadable "); field(b, "error", GetLastError()); append(b, "\r\n"); return;
    }
    for (SIZE_T i = 0; i < size; i++) {
        if (!(i % 16)) { append(b, "  "); hex(b, address + i); append(b, ": "); }
        char pair[] = {digits[bytes[i] >> 4], digits[bytes[i] & 15], ' ', 0};
        append(b, pair);
        if (i % 16 == 15 || i + 1 == size) append(b, "\r\n");
    }
}

static LONG WINAPI trace_exception(EXCEPTION_POINTERS *exception)
{
    if (!exception || !exception->ExceptionRecord || !exception->ContextRecord ||
        exception->ExceptionRecord->ExceptionCode != EXCEPTION_ACCESS_VIOLATION)
        return EXCEPTION_CONTINUE_SEARCH;
    CONTEXT *c = exception->ContextRecord;
    uintptr_t rva = (uintptr_t)c->Rip - image_base;
    if (have_filter && rva != filter_rva) return EXCEPTION_CONTINUE_SEARCH;
    /* Preserve the known failing sites even if earlier intentional game AVs
     * filled the unfiltered diagnostic budget. A specific RVA filter has no
     * earlier-fault budget because unrelated sites never enter this handler. */
    int known_site = stellar_blade && (rva == 0x3d969da || rva == 0x2828ec7 || rva == 0xf02d9a1);
    if (!have_filter && !known_site && InterlockedCompareExchange(&records, 0, 0) >= 256)
        return EXCEPTION_CONTINUE_SEARCH;
    if (InterlockedCompareExchange(&writing, 1, 0)) return EXCEPTION_CONTINUE_SEARCH;

    struct trace_buffer b = {{0}, 0};
    EXCEPTION_RECORD *e = exception->ExceptionRecord;
    uintptr_t fault = e->NumberParameters >= 2 ? e->ExceptionInformation[1] : 0;
    append(&b, "\r\n=== AMDNR observational AV ===\r\n");
    field(&b, "record", InterlockedIncrement(&records));
    field(&b, "thread", GetCurrentThreadId()); field(&b, "tick_ms", GetTickCount64());
    field(&b, "code", e->ExceptionCode); field(&b, "flags", e->ExceptionFlags);
    append(&b, "\r\n");
    field(&b, "image_base", image_base); field(&b, "rip_rva", rva);
    field(&b, "parameters", e->NumberParameters);
    if (e->NumberParameters) field(&b, "operation_0read_1write_8execute", e->ExceptionInformation[0]);
    field(&b, "fault", fault); append(&b, "\r\n");
#define REGISTER(name) field(&b, #name, c->name)
    REGISTER(Rip); REGISTER(Rsp); REGISTER(Rbp); REGISTER(EFlags); append(&b, "\r\n");
    REGISTER(Rax); REGISTER(Rbx); REGISTER(Rcx); REGISTER(Rdx); append(&b, "\r\n");
    REGISTER(Rsi); REGISTER(Rdi); REGISTER(R8); REGISTER(R9); append(&b, "\r\n");
    REGISTER(R10); REGISTER(R11); REGISTER(R12); REGISTER(R13); append(&b, "\r\n");
    REGISTER(R14); REGISTER(R15); append(&b, "\r\n");
#undef REGISTER
    memory_info(&b, "fault region", fault);
    memory_info(&b, "instruction region", (uintptr_t)c->Rip);
    memory_info(&b, "RSI object region", (uintptr_t)c->Rsi);
    uintptr_t vtable = 0, target = 0;
    int vtable_ok = read_memory((uintptr_t)c->Rsi, &vtable, sizeof(vtable));
    int target_ok = vtable_ok && vtable <= UINTPTR_MAX - 0x48 &&
        read_memory(vtable + 0x48, &target, sizeof(target));
    field(&b, "RSI_vtable_read", vtable_ok); field(&b, "RSI_vtable", vtable);
    field(&b, "slot_48_read", target_ok); field(&b, "slot_48_target", target);
    if (target_ok && target >= image_base) field(&b, "slot_48_target_rva", target - image_base);
    append(&b, "\r\n");
    memory_bytes(&b, "RSI object bytes", (uintptr_t)c->Rsi, 256);
    memory_bytes(&b, "instruction window", c->Rip >= 32 ? (uintptr_t)c->Rip - 32 : (uintptr_t)c->Rip, 128);
    if (target_ok) memory_bytes(&b, "virtual slot 0x48 target bytes", target, 96);
    if (wrapper_address) memory_bytes(&b, "Stellar Blade candidate wrapper bytes (RVA 0x19d02b0)", wrapper_address, 96);
    memory_bytes(&b, "stack bytes", (uintptr_t)c->Rsp, 128);
    append(&b, "=== continue exception search; context unchanged ===\r\n");
    write_record(&b);
    InterlockedExchange(&writing, 0);
    return EXCEPTION_CONTINUE_SEARCH;
}

static int parse_rva(const WCHAR *text, uintptr_t *value)
{
    unsigned base = 10;
    if (text[0] == '0' && (text[1] == 'x' || text[1] == 'X')) { text += 2; base = 16; }
    if (!*text) return 0;
    *value = 0;
    while (*text) {
        unsigned digit;
        if (*text >= '0' && *text <= '9') digit = *text - '0';
        else if (*text >= 'a' && *text <= 'f') digit = *text - 'a' + 10;
        else if (*text >= 'A' && *text <= 'F') digit = *text - 'A' + 10;
        else return 0;
        if (digit >= base || *value > (UINTPTR_MAX - digit) / base) return 0;
        *value = *value * base + digit;
        text++;
    }
    return 1;
}

/* Called once from HIP loader initialization. All file/path/module setup
 * happens here, outside the vectored exception handler. */
void amdnr_diagnostics_init(void)
{
    if (InterlockedCompareExchange(&initialized, 1, 0)) return;
    WCHAR path[4096], windows_path[4096], executable[1024], filter[64];
    DWORD length = GetEnvironmentVariableW(L"AMDNR_CRASH_TRACE", path, sizeof(path) / sizeof(path[0]));
    if (!length) return;
    if (length >= sizeof(path) / sizeof(path[0])) { amdnr_log("crash trace path too long"); return; }
    const WCHAR *selected_path = path;
    if (path[0] == '/') {
        if (length + 3 > sizeof(windows_path) / sizeof(windows_path[0])) return;
        windows_path[0] = 'Z'; windows_path[1] = ':';
        for (DWORD i = 0; i <= length; i++) windows_path[i + 2] = path[i] == '/' ? '\\' : path[i];
        selected_path = windows_path;
    }
    DWORD filter_length = GetEnvironmentVariableW(L"AMDNR_CRASH_TRACE_RVA", filter, sizeof(filter) / sizeof(filter[0]));
    if (filter_length) {
        if (filter_length >= sizeof(filter) / sizeof(filter[0]) || !parse_rva(filter, &filter_rva)) {
            amdnr_log("invalid AMDNR_CRASH_TRACE_RVA; diagnostics disabled"); return;
        }
        have_filter = 1;
    }
    image_base = (uintptr_t)GetModuleHandleW(NULL);
    DWORD exe_length = GetModuleFileNameW(NULL, executable, sizeof(executable) / sizeof(executable[0]));
    if (exe_length && exe_length < sizeof(executable) / sizeof(executable[0])) {
        const WCHAR *name = executable;
        for (const WCHAR *p = executable; *p; p++) if (*p == '\\' || *p == '/') name = p + 1;
        stellar_blade = !lstrcmpiW(name, L"SB-Win64-Shipping.exe");
        if (stellar_blade && image_base <= UINTPTR_MAX - 0x19d02b0) wrapper_address = image_base + 0x19d02b0;
    }
    trace_file = CreateFileW(selected_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (trace_file == INVALID_HANDLE_VALUE) { amdnr_log("cannot open crash trace: Win32 error %lu", GetLastError()); return; }
    trace_registration = AddVectoredExceptionHandler(1, trace_exception);
    if (!trace_registration) {
        amdnr_log("cannot register crash trace handler: Win32 error %lu", GetLastError());
        CloseHandle(trace_file); trace_file = INVALID_HANDLE_VALUE; return;
    }
    struct trace_buffer b = {{0}, 0};
    append(&b, "\r\n=== AMDNR crash trace session ===\r\n");
    field(&b, "process", GetCurrentProcessId()); field(&b, "image_base", image_base);
    field(&b, "filtered", have_filter); field(&b, "filter_rva", filter_rva);
    append(&b, "\r\nObservational only: all exceptions continue searching, no context changes.\r\n");
    write_record(&b);
    amdnr_log("observational crash trace enabled%s", have_filter ? " with RVA filter" : "");
}
