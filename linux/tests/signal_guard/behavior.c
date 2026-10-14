/* SPDX-License-Identifier: GPL-3.0-or-later */
#define _GNU_SOURCE
#include "fixtures.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "line %d: %s (errno=%d)\n", __LINE__, #condition, errno); \
        return 1; \
    } \
} while (0)

static void other_handler(int signal) { (void)signal; }

static struct sigaction wine_action(void)
{
    struct sigaction action = {0};
    action.sa_sigaction = wine_handler;
    action.sa_flags = SA_SIGINFO | SA_RESTART | SA_ONSTACK;
    sigemptyset(&action.sa_mask);
    sigaddset(&action.sa_mask, SIGUSR2);
    return action;
}

static int check_wine(void)
{
    struct sigaction current;
    CHECK(!sigaction(SIGUSR1, NULL, &current));
    CHECK(current.sa_sigaction == wine_handler);
    CHECK((current.sa_flags & (SA_SIGINFO | SA_RESTART | SA_ONSTACK)) ==
          (SA_SIGINFO | SA_RESTART | SA_ONSTACK));
    CHECK(!(current.sa_flags & (SA_NODEFER | SA_RESETHAND)));
    CHECK(sigismember(&current.sa_mask, SIGUSR2) == 1);
    return 0;
}

static int retention(void)
{
    for (int i = 0; i < 100; ++i)
    {
        errno = E2BIG;
        CHECK(!comgr_install(0));
        CHECK(errno == E2BIG);
        CHECK(!check_wine());
        errno = EDOM;
        CHECK(!comgr_restore());
        CHECK(errno == EDOM);
        CHECK(!check_wine());
    }
    return 0;
}

static int queries(void)
{
    struct sigaction current;
    unsigned before = loader_calls();
    errno = E2BIG;
    CHECK(!comgr_query(&current));
    CHECK(errno == E2BIG);
    CHECK(current.sa_sigaction == wine_handler);
    CHECK(!sigaction(SIGUSR1, NULL, NULL));
    CHECK(loader_calls() == before);
    struct sigaction action = wine_action();
    errno = 0;
    CHECK(sigaction(-1, &action, NULL) == -1);
    CHECK(errno == EINVAL);
    CHECK(!check_wine());
    return 0;
}

static int scope(void)
{
    CHECK(!comgr_install(0)); /* Populate module cache first. */
    unsigned before = loader_calls();
    struct sigaction action = {0}, previous;
    action.sa_handler = other_handler;
    sigemptyset(&action.sa_mask);
    /* A COMGR caller may legitimately restore a third-party handler. */
    CHECK(!comgr_replace(&action, &previous));
    CHECK(previous.sa_sigaction == wine_handler);
    CHECK(!comgr_query(&previous));
    CHECK(previous.sa_handler == other_handler);
    /* COMGR's diagnostics remain allowed when Wine does not own the signal. */
    CHECK(!comgr_install(0));
    CHECK(!comgr_query(&previous));
    CHECK(previous.sa_sigaction == comgr_handler);
    CHECK(!comgr_restore());
    CHECK(!comgr_query(&previous));
    CHECK(previous.sa_handler == other_handler);
    action = wine_action();
    CHECK(!sigaction(SIGUSR1, &action, NULL));
    /* The caller must also be COMGR, even if it uses COMGR's handler address. */
    action.sa_sigaction = comgr_handler;
    action.sa_flags = SA_SIGINFO | SA_NODEFER | SA_RESETHAND | SA_ONSTACK;
    CHECK(!sigaction(SIGUSR1, &action, &previous));
    CHECK(previous.sa_sigaction == wine_handler);
    CHECK(!comgr_query(&previous));
    CHECK(previous.sa_sigaction == comgr_handler);
    action.sa_handler = SIG_IGN;
    action.sa_flags = 0;
    CHECK(!comgr_replace(&action, NULL));
    CHECK(!comgr_query(&previous));
    CHECK(previous.sa_handler == SIG_IGN);
    action.sa_handler = SIG_DFL;
    CHECK(!comgr_replace(&action, NULL));
    CHECK(!comgr_query(&previous));
    CHECK(previous.sa_handler == SIG_DFL);
    CHECK(loader_calls() == before);
    return 0;
}

static int alias(void)
{
    for (int i = 0; i < 100; ++i)
    {
        errno = EDOM;
        CHECK(!comgr_install(1));
        CHECK(errno == EDOM);
        CHECK(!check_wine());
        CHECK(!comgr_restore());
        CHECK(!check_wine());
    }
    struct sigaction action = {0};
    action.sa_handler = other_handler;
    sigemptyset(&action.sa_mask);
    int (*call)(int, const struct sigaction *, struct sigaction *) = sigaction;
    CHECK(!call(SIGUSR1, &action, &action));
    CHECK(action.sa_sigaction == wine_handler);
    CHECK(!sigaction(SIGUSR1, NULL, &action));
    CHECK(action.sa_handler == other_handler);
    return 0;
}

static int signal_restore(void)
{
    /* A saved Wine handler restored before first registration must not enter
     * discovery either: LLVM may unregister while processing a signal. */
    struct sigaction action = wine_action();
    comgr_save(&action);
    unsigned before = loader_calls();
    CHECK(!comgr_restore());
    CHECK(loader_calls() == before);
    CHECK(!comgr_install(0));
    CHECK(loader_calls() > before); /* Ensure interception/auditing is active. */
    before = loader_calls();
    wine_callback(comgr_restore_in_signal);
    CHECK(!raise(SIGUSR1));
    CHECK(wine_count() == 1);
    CHECK(!comgr_restore_result());
    CHECK(!check_wine());
    CHECK(loader_calls() == before);
    /* Cached ordinary installs and queries must stay independent of loader
     * state too, even if another thread currently owns the loader lock. */
    for (int i = 0; i < 100; ++i)
    {
        CHECK(!comgr_install(i & 1));
        CHECK(!comgr_restore());
        CHECK(!check_wine());
    }
    CHECK(loader_calls() == before);
    return 0;
}

int main(int argc, char **argv)
{
    CHECK(argc == 2);
    struct sigaction action = wine_action();
    CHECK(!sigaction(SIGUSR1, &action, NULL));
    int result;
    if (!strcmp(argv[1], "retention")) result = retention();
    else if (!strcmp(argv[1], "queries")) result = queries();
    else if (!strcmp(argv[1], "scope")) result = scope();
    else if (!strcmp(argv[1], "alias")) result = alias();
    else if (!strcmp(argv[1], "signal_restore")) result = signal_restore();
    else { fprintf(stderr, "unknown test: %s\n", argv[1]); return 1; }
    if (!result) puts("PASS");
    return result;
}
