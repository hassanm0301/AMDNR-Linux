/* SPDX-License-Identifier: GPL-3.0-or-later */
#define _GNU_SOURCE
#include "fixtures.h"

static volatile sig_atomic_t calls;
static void (*on_signal)(void);

void wine_handler(int signal, siginfo_t *info, void *context)
{
    (void)signal;
    (void)info;
    (void)context;
    ++calls;
    if (on_signal) on_signal();
}

int wine_count(void) { return calls; }
void wine_callback(void (*callback)(void)) { on_signal = callback; }
