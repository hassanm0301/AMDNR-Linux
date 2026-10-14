#!/usr/bin/env python3
"""Generate typed Wine entry points; the native prototypes come from the HIP SDK."""
from pathlib import Path
import sys

# name, C arguments, Wine spec arguments. Custom entries live in interop.c.
API = [
    ("hipInit", "unsigned flags", "long"),
    ("hipRuntimeGetVersion", "int *version", "ptr"),
    ("hipDriverGetVersion", "int *version", "ptr"),
    ("hipGetDeviceCount", "int *count", "ptr"),
    ("hipGetDevice", "int *device", "ptr"),
    ("hipSetDevice", "int device", "long"),
    ("hipDeviceGetAttribute", "int *value, hipDeviceAttribute_t attribute, int device", "ptr long long"),
    ("hipDeviceGetPCIBusId", "char *id, int length, int device", "ptr long long"),
    ("hipDeviceSynchronize", "void", ""),
    ("hipMemGetInfo", "size_t *available, size_t *total", "ptr ptr"),
    ("hipMalloc", "void **pointer, size_t size", "ptr int64"),
    ("hipHostMalloc", "void **pointer, size_t size, unsigned flags", "ptr int64 long"),
    ("hipHostFree", "void *pointer", "ptr"),
    ("hipHostGetDevicePointer", "void **device, void *host, unsigned flags", "ptr ptr long"),
    ("hipFree", "void *pointer", "ptr"),
    ("hipMemcpy", "void *dst, const void *src, size_t size, hipMemcpyKind kind", "ptr ptr int64 long"),
    ("hipMemcpyAsync", "void *dst, const void *src, size_t size, hipMemcpyKind kind, hipStream_t stream", "ptr ptr int64 long ptr"),
    ("hipMemcpy2DAsync", "void *dst, size_t dpitch, const void *src, size_t spitch, size_t width, size_t height, hipMemcpyKind kind, hipStream_t stream", "ptr int64 ptr int64 int64 int64 long ptr"),
    ("hipMemset", "void *dst, int value, size_t size", "ptr long int64"),
    ("hipMemsetAsync", "void *dst, int value, size_t size, hipStream_t stream", "ptr long int64 ptr"),
    ("hipMemsetD32Async", "hipDeviceptr_t dst, int value, size_t count, hipStream_t stream", "ptr long int64 ptr"),
    ("hipEventCreate", "hipEvent_t *event", "ptr"),
    ("hipEventCreateWithFlags", "hipEvent_t *event, unsigned flags", "ptr long"),
    ("hipEventRecord", "hipEvent_t event, hipStream_t stream", "ptr ptr"),
    ("hipEventElapsedTime", "float *ms, hipEvent_t start, hipEvent_t end", "ptr ptr ptr"),
    ("hipEventDestroy", "hipEvent_t event", "ptr"),
    ("hipEventSynchronize", "hipEvent_t event", "ptr"),
    ("hipEventQuery", "hipEvent_t event", "ptr"),
    ("hipStreamCreate", "hipStream_t *stream", "ptr"),
    ("hipStreamCreateWithFlags", "hipStream_t *stream, unsigned flags", "ptr long"),
    ("hipStreamCreateWithPriority", "hipStream_t *stream, unsigned flags, int priority", "ptr long long"),
    ("hipStreamQuery", "hipStream_t stream", "ptr"),
    ("hipStreamWaitEvent", "hipStream_t stream, hipEvent_t event, unsigned flags", "ptr ptr long"),
    ("hipStreamWaitValue32", "hipStream_t stream, void *pointer, uint32_t value, unsigned flags, uint32_t mask", "ptr ptr long long long"),
    ("hipStreamWriteValue32", "hipStream_t stream, void *pointer, uint32_t value, unsigned flags", "ptr ptr long long"),
    ("hipExternalMemoryGetMappedBuffer", "void **pointer, hipExternalMemory_t memory, const hipExternalMemoryBufferDesc *description", "ptr ptr ptr"),
    ("hipDestroyExternalMemory", "hipExternalMemory_t memory", "ptr"),
    ("hipModuleLoadData", "hipModule_t *module, const void *data", "ptr ptr"),
    ("hipModuleGetFunction", "hipFunction_t *function, hipModule_t module, const char *name", "ptr ptr str"),
    ("hipModuleGetGlobal", "hipDeviceptr_t *pointer, size_t *bytes, hipModule_t module, const char *name", "ptr ptr ptr str"),
    ("hipModuleLaunchKernel", "hipFunction_t function, unsigned gx, unsigned gy, unsigned gz, unsigned bx, unsigned by, unsigned bz, unsigned shared, hipStream_t stream, void **params, void **extra", "ptr long long long long long long long ptr ptr ptr"),
    ("hipModuleUnload", "hipModule_t module", "ptr"),
    ("hipStreamBeginCapture", "hipStream_t stream, hipStreamCaptureMode mode", "ptr long"),
    ("hipStreamEndCapture", "hipStream_t stream, hipGraph_t *graph", "ptr ptr"),
    ("hipGraphInstantiate", "hipGraphExec_t *executable, hipGraph_t graph, hipGraphNode_t *error, char *log, size_t length", "ptr ptr ptr ptr int64"),
    ("hipGraphLaunch", "hipGraphExec_t graph, hipStream_t stream", "ptr ptr"),
    ("hipGraphDestroy", "hipGraph_t graph", "ptr"),
    ("hipGraphExecDestroy", "hipGraphExec_t graph", "ptr"),
    ("hipMemAddressReserve", "void **pointer, size_t size, size_t alignment, void *address, unsigned long long flags", "ptr int64 int64 ptr int64"),
    ("hipMemAddressFree", "void *pointer, size_t size", "ptr int64"),
    ("hipMemCreate", "hipMemGenericAllocationHandle_t *handle, size_t size, const hipMemAllocationProp *properties, unsigned long long flags", "ptr int64 ptr int64"),
    ("hipMemRelease", "hipMemGenericAllocationHandle_t handle", "ptr"),
    ("hipMemMap", "void *pointer, size_t size, size_t offset, hipMemGenericAllocationHandle_t handle, unsigned long long flags", "ptr int64 int64 ptr int64"),
    ("hipMemUnmap", "void *pointer, size_t size", "ptr int64"),
    ("hipMemSetAccess", "void *pointer, size_t size, const hipMemAccessDesc *descriptions, size_t count", "ptr int64 ptr int64"),
    ("hipMemGetAllocationGranularity", "size_t *size, const hipMemAllocationProp *properties, hipMemAllocationGranularity_flags flags", "ptr ptr long"),
    ("hipGetLastError", "void", ""),
    ("hipPeekAtLastError", "void", ""),
]
CUSTOM = {
    "hipGetDevicePropertiesR0600": "ptr long", "hipDeviceGetName": "ptr long long",
    "hipImportExternalMemory": "ptr ptr", "hipImportExternalSemaphore": "ptr ptr",
    "hipDestroyExternalSemaphore": "ptr", "hipSignalExternalSemaphoresAsync": "ptr ptr long ptr",
    "hipWaitExternalSemaphoresAsync": "ptr ptr long ptr", "hipStreamSynchronize": "ptr",
    "hipStreamDestroy": "ptr", "hipModuleLoad": "ptr str",
}

def generate(output):
    output.mkdir(parents=True, exist_ok=True)
    source = ['/* Generated by generate.py. SPDX-License-Identifier: GPL-3.0-or-later */', '#include "bridge.h"']
    exports = dict(CUSTOM)
    for name, arguments, spec in API:
        names = ", ".join(a.strip().split()[-1].lstrip("*") for a in arguments.split(",")) if arguments != "void" else ""
        source.append(f"hipError_t WINAPI amdnr_{name}({arguments}) {{ REQUIRE_FN({name}); return {name}_native({names}); }}")
        exports[name] = spec
    for name in ("hipGetErrorName", "hipGetErrorString"):
        source.append(f'const char * WINAPI amdnr_{name}(hipError_t error) {{ NATIVE_FN({name}); return {name}_native ? {name}_native(error) : "Linux HIP library unavailable"; }}')
        exports[name] = "long"
    (output / "forward.c").write_text("\n".join(source) + "\n")
    (output / "amdhip64_7.spec").write_text("\n".join(f"@ stdcall {name}({args}) amdnr_{name}" for name, args in sorted(exports.items())) + "\n")

if __name__ == "__main__":
    generate(Path(sys.argv[1]))
