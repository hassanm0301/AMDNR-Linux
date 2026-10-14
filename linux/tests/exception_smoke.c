/* SPDX-License-Identifier: GPL-3.0-or-later
 * Native ROCm must not change Windows exception delivery in the host game. */
#include <windows.h>
#include <stdio.h>
#include <stdint.h>

static volatile LONG handled;
static LONG WINAPI handler(EXCEPTION_POINTERS *exception)
{
    const unsigned char *instruction = (void *)(uintptr_t)exception->ContextRecord->Rip;
    DWORD code = exception->ExceptionRecord->ExceptionCode;
    if ((code == EXCEPTION_INT_DIVIDE_BY_ZERO && instruction[0] == 0xf7 && instruction[1] == 0xf1) ||
        (code == EXCEPTION_ACCESS_VIOLATION && instruction[0] == 0x8b && instruction[1] == 0x00))
    {
        exception->ContextRecord->Rax = 0x123;
        exception->ContextRecord->Rdx = 0x456;
        exception->ContextRecord->Rip += 2;
        InterlockedIncrement(&handled);
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static DWORD WINAPI exercise(void *unused)
{
    (void)unused;
    for (int i = 0; i < 16; i++)
    {
        unsigned a, d;
        __asm__ volatile("xorl %%edx, %%edx; movl $1, %%eax; xorl %%ecx, %%ecx; divl %%ecx"
                         : "=a"(a), "=d"(d) : : "ecx", "cc");
        if (a != 0x123 || d != 0x456) return 1;
        __asm__ volatile("xorl %%eax, %%eax; movl (%%rax), %%eax" : "=a"(a) : : "edx", "cc");
        if (a != 0x123) return 1;
    }
    return 0;
}

int main(void)
{
    void *registration = AddVectoredExceptionHandler(1, handler);
    if (!registration || exercise(NULL)) return 1;
    puts("Windows exceptions before HIP: OK"); fflush(stdout);
    HMODULE hip = LoadLibraryExW(L"amdhip64_7.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    int (*init)(unsigned) = hip ? (void *)GetProcAddress(hip, "hipInit") : NULL;
    if (!init || init(0)) return 1;
    if (exercise(NULL)) return 1;
    puts("Windows exceptions after HIP on caller: OK"); fflush(stdout);
    HANDLE thread = CreateThread(NULL, 0, exercise, NULL, 0, NULL);
    DWORD result = 1;
    if (!thread || WaitForSingleObject(thread, 10000) != WAIT_OBJECT_0 ||
        !GetExitCodeThread(thread, &result) || result) return 1;
    CloseHandle(thread);
    RemoveVectoredExceptionHandler(registration);
    if (handled != 96) return 1;
    puts("PASS: Windows divide/access exceptions survive HIP initialization on existing and new threads");
    return 0;
}
