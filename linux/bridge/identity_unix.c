/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "native.h"
#include "unix_args.h"
#include <stdio.h>

static int read_hex(int domain, int bus, int device, const char *name, unsigned *value)
{
    char path[96];
    snprintf(path, sizeof(path), "/sys/bus/pci/devices/%04x:%02x:%02x.0/%s", domain, bus, device, name);
    FILE *file = fopen(path, "re");
    if (!file) return -1;
    int read = fscanf(file, "%x", value);
    fclose(file);
    return read == 1 ? 0 : -1;
}

/* The PCI IDs of a HIP device, which HIP itself does not report (its
 * asicRevision is the chip's, not the PCI revision ID). GPUs are function 0. */
int32_t amdnr_unix_pci_identity(void *opaque)
{
    struct amdnr_args_pci_identity *args = opaque;
    args->result = -1;
    if (read_hex(args->domain, args->bus, args->device, "vendor", &args->vendor) ||
        read_hex(args->domain, args->bus, args->device, "device", &args->device_id) ||
        read_hex(args->domain, args->bus, args->device, "revision", &args->revision))
        return 0;
    args->result = 0;
    return 0;
}
