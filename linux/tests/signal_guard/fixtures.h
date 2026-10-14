/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef AMDNR_SIGNAL_GUARD_FIXTURES_H
#define AMDNR_SIGNAL_GUARD_FIXTURES_H
#include <signal.h>

void wine_handler(int signal, siginfo_t *info, void *context);
int wine_count(void);
void wine_callback(void (*callback)(void));

void comgr_handler(int signal, siginfo_t *info, void *context);
int comgr_install(int alias);
void comgr_save(const struct sigaction *action);
int comgr_restore(void);
void comgr_restore_in_signal(void);
int comgr_restore_result(void);
int comgr_replace(const struct sigaction *action, struct sigaction *previous);
int comgr_query(struct sigaction *previous);

unsigned loader_calls(void);
#endif
