// SPDX-License-Identifier: GPL-3.0-or-later
#include <hip/hip_runtime.h>
extern "C" __global__ void transform(unsigned *data, unsigned count)
{
    unsigned i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < count) data[i] = data[i] * 3u + 7u;
}
