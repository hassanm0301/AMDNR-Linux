/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#include "hip_types.h"
#include <pthread.h>
typedef int32_t (*amdnr_unix_entry)(void *);
void *amdnr_symbol(const char *name);
void amdnr_log(const char *format, ...);
