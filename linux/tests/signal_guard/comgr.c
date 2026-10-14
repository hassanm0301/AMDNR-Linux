/* SPDX-License-Identifier: GPL-3.0-or-later */
#define _GNU_SOURCE
#include "fixtures.h"

static struct sigaction saved;
static volatile sig_atomic_t restore_result;

void comgr_handler(int signal, siginfo_t *info, void *context)
{
    (void)signal;
    (void)info;
    (void)context;
}

int comgr_install(int alias)
{
    struct sigaction action = {0};
    action.sa_sigaction = comgr_handler;
    action.sa_flags = SA_SIGINFO | SA_NODEFER | SA_RESETHAND | SA_ONSTACK;
    sigemptyset(&action.sa_mask);
    if (alias)
    {
        /* A function pointer avoids the libc header's restrict warning: this
         * deliberately exercises the interposer's reused-storage handling. */
        int (*call)(int, const struct sigaction *, struct sigaction *) = sigaction;
        int result = call(SIGUSR1, &action, &action);
        saved = action;
        return result;
    }
    return sigaction(SIGUSR1, &action, &saved);
}

void comgr_save(const struct sigaction *action) { saved = *action; }
int comgr_restore(void) { return sigaction(SIGUSR1, &saved, NULL); }
void comgr_restore_in_signal(void) { restore_result = comgr_restore(); }
int comgr_restore_result(void) { return restore_result; }
int comgr_replace(const struct sigaction *action, struct sigaction *previous)
{
    return sigaction(SIGUSR1, action, previous);
}
int comgr_query(struct sigaction *previous)
{
    return sigaction(SIGUSR1, NULL, previous);
}
