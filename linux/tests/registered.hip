// SPDX-License-Identifier: GPL-3.0-or-later
// Device code for hip_registration: one kernel reading a registered constant
// and updating a registered device variable.
#include <hip/hip_runtime.h>
__constant__ unsigned amdnr_scale;
__device__ unsigned amdnr_launches;
extern "C" __global__ void scale_add(unsigned *data, unsigned count, unsigned offset)
{
    unsigned i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < count) data[i] = data[i] * amdnr_scale + offset;
    if (i == 0) atomicAdd(&amdnr_launches, 1u);
}
