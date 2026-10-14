/* SPDX-License-Identifier: GPL-3.0-or-later
 * PE side of Wine shared-memory import. Keep Windows calls and Wine Vulkan
 * objects here; only native handle export runs through the Unix library. */
#include "bridge.h"
#define VK_NO_PROTOTYPES
#define VK_USE_PLATFORM_WIN32_KHR
#include <vulkan/vulkan.h>
#include <dxgi1_4.h>
#include <d3d12.h>
#include <string.h>

/* vkd3d-proton's ID3D12DeviceExt ABI. GetVulkanHandles is the first method
 * after IUnknown and returns borrowed Wine Vulkan handles. Keep the D3D12
 * device references alive until all operations using those handles finish. */
typedef struct amdnr_d3d12_device_ext amdnr_d3d12_device_ext;
typedef struct amdnr_d3d12_device_ext_vtbl
{
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(amdnr_d3d12_device_ext *, REFIID, void **);
    ULONG (STDMETHODCALLTYPE *AddRef)(amdnr_d3d12_device_ext *);
    ULONG (STDMETHODCALLTYPE *Release)(amdnr_d3d12_device_ext *);
    HRESULT (STDMETHODCALLTYPE *GetVulkanHandles)(amdnr_d3d12_device_ext *, VkInstance *, VkPhysicalDevice *, VkDevice *);
} amdnr_d3d12_device_ext_vtbl;
struct amdnr_d3d12_device_ext { const amdnr_d3d12_device_ext_vtbl *lpVtbl; };
static const GUID iid_d3d12_device_ext =
    {0x11ea7a1a, 0x0f6a, 0x49bf, {0xb6, 0x12, 0x3e, 0x30, 0xf8, 0xe2, 0x01, 0xdd}};

/* Inspect Wine's private header without dereferencing an unchecked pointer.
 * A different Wine layout must fail the import rather than crash the game. */
static int read_header(uint64_t address, uint64_t header[2])
{
    SIZE_T bytes = 0;
    return address && ReadProcessMemory(GetCurrentProcess(), (void *)(uintptr_t)address,
        header, 2 * sizeof(*header), &bytes) && bytes == 2 * sizeof(*header);
}

/* Wine 11's shared D3DKMT handle is a container, not an fd-backed handle.
 * Import it with Wine Vulkan (which owns that protocol), then export the
 * native memory fd. The final boundary uses Wine's validated Vulkan object
 * header, also used by d4r's Wine bridge. Refuse an unfamiliar layout. */
int amdnr_vulkan_fd(HANDLE handle, uint64_t size)
{
    int ordinal, fd = -1;
    hipDeviceProp_t properties;
    if (amdnr_native_hipGetDevice(&ordinal) ||
        amdnr_native_hipGetDevicePropertiesR0600(&properties, ordinal) ||
        amdnr_fix_identity(&properties, ordinal)) return -1;
    IDXGIFactory4 *factory = NULL;
    IDXGIAdapter1 *adapter = NULL;
    ID3D12Device *d3d12 = NULL;
    amdnr_d3d12_device_ext *interop = NULL;
    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkResult result = VK_SUCCESS;
    LUID luid;
    memcpy(&luid, properties.luid, sizeof(luid));
    HRESULT hr = CreateDXGIFactory1(&IID_IDXGIFactory4, (void **)&factory);
    if (SUCCEEDED(hr)) hr = IDXGIFactory4_EnumAdapterByLuid(factory, luid, &IID_IDXGIAdapter1, (void **)&adapter);
    /* vkd3d-proton returns its existing device singleton for this adapter.
     * Private Vulkan instances/devices would be captured by released host
     * Vulkan hooks, leaving the host with dangling global graphics state. */
    if (SUCCEEDED(hr)) hr = D3D12CreateDevice((IUnknown *)adapter, D3D_FEATURE_LEVEL_11_0,
                                             &IID_ID3D12Device, (void **)&d3d12);
    if (SUCCEEDED(hr)) hr = ID3D12Device_QueryInterface(d3d12, &iid_d3d12_device_ext, (void **)&interop);
    if (SUCCEEDED(hr)) hr = interop->lpVtbl->GetVulkanHandles(interop, &instance, &physical, &device);
    if (FAILED(hr) || !instance || !physical || !device)
    {
        amdnr_log("cannot borrow vkd3d Vulkan handles for HIP memory: %#x", (unsigned)hr);
        goto done;
    }

    HMODULE dll = GetModuleHandleW(L"vulkan-1.dll");
    PFN_vkGetInstanceProcAddr get = dll ? (void *)GetProcAddress(dll, "vkGetInstanceProcAddr") : NULL;
    PFN_vkGetDeviceProcAddr device_proc = get ? (void *)get(instance, "vkGetDeviceProcAddr") : NULL;
    PFN_vkGetPhysicalDeviceExternalBufferProperties external_buffer = get ?
        (void *)get(instance, "vkGetPhysicalDeviceExternalBufferProperties") : NULL;
    if (!device_proc || !external_buffer)
    {
        amdnr_log("vkd3d Wine Vulkan external-memory queries are unavailable");
        goto done;
    }
    VkPhysicalDeviceExternalBufferInfo buffer = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_BUFFER_INFO,
        .usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_WIN32_BIT};
    VkExternalBufferProperties support = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_BUFFER_PROPERTIES};
    external_buffer(physical, &buffer, &support);
    VkExternalMemoryFeatureFlags required = VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT | VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT;
    if ((support.externalMemoryProperties.externalMemoryFeatures & required) != required ||
        !(support.externalMemoryProperties.exportFromImportedHandleTypes & buffer.handleType) ||
        !(support.externalMemoryProperties.compatibleHandleTypes & buffer.handleType) ||
        (support.externalMemoryProperties.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT))
    {
        amdnr_log("vkd3d adapter does not support reusable opaque Win32 buffer memory (features %#x)",
                  support.externalMemoryProperties.externalMemoryFeatures);
        goto done;
    }
    PFN_vkGetMemoryWin32HandlePropertiesKHR handle_properties = (void *)device_proc(device, "vkGetMemoryWin32HandlePropertiesKHR");
    PFN_vkAllocateMemory allocate = (void *)device_proc(device, "vkAllocateMemory");
    PFN_vkFreeMemory free_memory = (void *)device_proc(device, "vkFreeMemory");
    if (!handle_properties || !allocate || !free_memory)
    {
        amdnr_log("vkd3d device lacks Wine Vulkan external-memory entry points");
        goto done;
    }
    VkMemoryWin32HandlePropertiesKHR hp = {.sType = VK_STRUCTURE_TYPE_MEMORY_WIN32_HANDLE_PROPERTIES_KHR};
    /* Wine's D3DKMT protocol exposes the imported allocation's memory type
     * through this call for opaque handles; this is Wine-specific behavior. */
    if ((result = handle_properties(device, VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_WIN32_BIT, handle, &hp))) goto done;
    VkImportMemoryWin32HandleInfoKHR import = {.sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_WIN32_HANDLE_INFO_KHR,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_WIN32_BIT, .handle = handle};
    VkExportMemoryAllocateInfo export = {.sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,
        .pNext = &import, .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_WIN32_BIT};
    VkMemoryAllocateInfo ai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .pNext = &export, .allocationSize = size};
    if (!hp.memoryTypeBits) goto done;
    while (!(hp.memoryTypeBits & (1u << ai.memoryTypeIndex))) ai.memoryTypeIndex++;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    if ((result = allocate(device, &ai, NULL, &memory))) goto done;

    uint64_t client_device[2], device_object[2], memory_object[2];
    if (read_header((uintptr_t)device, client_device) && read_header(client_device[1], device_object) &&
        read_header((uint64_t)memory, memory_object) && device_object[0] && memory_object[0] &&
        device_object[1] == (uint64_t)(uintptr_t)device && memory_object[1] == (uint64_t)memory)
    {
        /* Only native handles cross the Unix-call boundary. The native
         * helper exports an fd while these borrowed Wine objects and their
         * owning D3D12 device remain alive. It performs no Win32 calls. */
        if (amdnr_native_vulkan_fd(device_object[0], memory_object[0], &fd) || fd < 0)
            fd = -1;
    }
    else amdnr_log("unsupported Wine Vulkan object ABI; cannot export HIP memory");
    free_memory(device, memory, NULL);
done:
    if (result) amdnr_log("Vulkan shared-handle import failed: %d", result);
    if (interop) interop->lpVtbl->Release(interop);
    if (d3d12) ID3D12Device_Release(d3d12);
    if (adapter) IDXGIAdapter1_Release(adapter);
    if (factory) IDXGIFactory4_Release(factory);
    return fd;
}
