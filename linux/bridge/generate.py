#!/usr/bin/env python3
"""Generate typed PE/unixcall boundaries for the Windows HIP exports."""
from pathlib import Path
import sys
import re

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

EXTRA = [
    ('hipGetDevicePropertiesR0600', 'hipDeviceProp_t *out, int device'),
    ('hipImportExternalMemory', 'hipExternalMemory_t *out, const hipExternalMemoryHandleDesc *description'),
    ('hipStreamSynchronize', 'hipStream_t stream'),
    ('hipStreamDestroy', 'hipStream_t stream'),
    ('hipStreamIsCapturing', 'hipStream_t stream, hipStreamCaptureStatus *status'),
]

def fields(arguments):
    return [] if arguments == 'void' else [a.strip() for a in arguments.split(',')]

def field_name(argument):
    return re.search(r'(\w+)$', argument).group(1)

def generate(output):
    output.mkdir(parents=True, exist_ok=True)
    catalog = [(name, args, 'hipError_t') for name, args, _ in API]
    catalog += [(name, args, 'hipError_t') for name, args in EXTRA]
    catalog += [(name, 'hipError_t error', 'const char *') for name in ('hipGetErrorName', 'hipGetErrorString')]
    ids = [name for name, _, _ in catalog] + ['vulkan_fd', 'close_fd', 'fence_launch', 'fence_wait', 'fence_complete']
    shared = ['/* Generated. SPDX-License-Identifier: GPL-3.0-or-later */', '#pragma once', '#include "hip_types.h"',
              '#include "fence_shared.h"', 'enum amdnr_unix_code {']
    shared += [f'    AMDNR_UNIX_{name},' for name in ids]
    shared += ['    AMDNR_UNIX_COUNT', '};']
    header = ['/* Generated. SPDX-License-Identifier: GPL-3.0-or-later */', '#pragma once', '#include "hip_types.h"']
    pe = ['/* Generated. SPDX-License-Identifier: GPL-3.0-or-later */', '#include "bridge.h"', '#include "unix_args.h"']
    native = ['/* Generated. SPDX-License-Identifier: GPL-3.0-or-later */', '#include "native.h"', '#include "unix_args.h"']
    for name, args, result_type in catalog:
        arg_fields = fields(args)
        names = [field_name(a) for a in arg_fields]
        shared += [f'struct amdnr_args_{name} {{'] + [f'    {a};' for a in arg_fields] + [f'    {result_type} result;', '};']
        header += [f'{result_type} amdnr_native_{name}({args});']
        fallback = 'hipErrorNotSupported' if result_type == 'hipError_t' else '"Linux HIP library unavailable"'
        values = ', '.join([f'.{n} = {n}' for n in names] + [f'.result = {fallback}'])
        pe += [f'{result_type} amdnr_native_{name}({args}) {{',
               f'    struct amdnr_args_{name} args = {{{values}}};',
               f'    if (amdnr_unix_call(AMDNR_UNIX_{name}, &args)) return {fallback};',
               '    return args.result;', '}']
        native += [f'static int32_t call_{name}(void *opaque) {{',
                   f'    struct amdnr_args_{name} *args = opaque;',
                   f'    __typeof__(&{name}) function = (__typeof__(&{name}))amdnr_symbol("{name}");',
                   f'    args->result = function ? function({", ".join("args->" + n for n in names)}) : {fallback};',
                   '    return 0;', '}']
    shared += ['struct amdnr_args_vulkan_fd { uint64_t device, memory; int32_t fd, result; };',
               'struct amdnr_args_close_fd { int32_t fd; };']
    header += ['int amdnr_native_vulkan_fd(uint64_t device, uint64_t memory, int *fd);',
               'void amdnr_native_close_fd(int fd);',
               'hipError_t amdnr_native_fence_launch(hipStream_t stream, void **token);',
               'hipError_t amdnr_native_fence_wait(void *token);',
               'hipError_t amdnr_native_fence_complete(void *token);']
    pe += ['int amdnr_native_vulkan_fd(uint64_t device, uint64_t memory, int *fd) {',
           '    struct amdnr_args_vulkan_fd args = {device, memory, -1, -1};',
           '    *fd = -1;',
           '    if (amdnr_unix_call(AMDNR_UNIX_vulkan_fd, &args)) return -1;',
           '    *fd = args.fd; return args.result;', '}',
           'void amdnr_native_close_fd(int fd) {',
           '    struct amdnr_args_close_fd args = {fd};',
           '    amdnr_unix_call(AMDNR_UNIX_close_fd, &args);', '}',
           'hipError_t amdnr_native_fence_launch(hipStream_t stream, void **token) {',
           '    struct amdnr_fence_launch_args args = {stream, NULL, hipErrorNotSupported};',
           '    *token = NULL;',
           '    if (amdnr_unix_call(AMDNR_UNIX_fence_launch, &args)) return hipErrorNotSupported;',
           '    *token = args.token; return (hipError_t)args.result;', '}']
    for op in ('wait', 'complete'):
        pe += [f'hipError_t amdnr_native_fence_{op}(void *token) {{',
               '    struct amdnr_fence_token_args args = {token, hipErrorNotSupported};',
               f'    if (amdnr_unix_call(AMDNR_UNIX_fence_{op}, &args)) return hipErrorNotSupported;',
               '    return (hipError_t)args.result;', '}']
    native += ['extern int32_t amdnr_unix_vulkan_fd(void *);', 'extern int32_t amdnr_unix_close_fd(void *);',
               'extern int32_t amdnr_unix_fence_launch(void *);', 'extern int32_t amdnr_unix_fence_wait(void *);',
               'extern int32_t amdnr_unix_fence_complete(void *);',
               '__attribute__((visibility("default"))) const amdnr_unix_entry __wine_unix_call_funcs[] = {']
    native += [f'    call_{name},' for name, _, _ in catalog]
    native += [f'    amdnr_unix_{name},' for name in ids[len(catalog):]] + ['};',
               '_Static_assert(sizeof(__wine_unix_call_funcs) / sizeof(*__wine_unix_call_funcs) == AMDNR_UNIX_COUNT, "Unixcall table mismatch");']
    exports = dict(CUSTOM)
    forward = ['/* Generated. SPDX-License-Identifier: GPL-3.0-or-later */', '#include "bridge.h"']
    for name, args, spec in API:
        forward += [f'hipError_t WINAPI amdnr_{name}({args}) {{ return amdnr_native_{name}({", ".join(field_name(a) for a in fields(args))}); }}']
        exports[name] = spec
    for name in ('hipGetErrorName', 'hipGetErrorString'):
        forward += [f'const char * WINAPI amdnr_{name}(hipError_t error) {{ return amdnr_native_{name}(error); }}']
        exports[name] = 'long'
    for filename, lines in [('unix_args.h', shared), ('native_api.h', header), ('native_api.c', pe), ('unix_calls.c', native), ('forward.c', forward)]:
        (output / filename).write_text('\n'.join(lines) + '\n')
    (output / 'amdhip64_7.spec').write_text('\n'.join(f'@ stdcall {name}({args}) amdnr_{name}' for name, args in sorted(exports.items())) + '\n')

if __name__ == '__main__':
    generate(Path(sys.argv[1]))
