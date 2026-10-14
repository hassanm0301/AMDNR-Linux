/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "bridge.h"
#define VK_NO_PROTOTYPES
#define VK_USE_PLATFORM_WIN32_KHR
#include <vulkan/vulkan.h>
#include <initguid.h>
#include <dxgi1_4.h>
#include <stdlib.h>
#include <string.h>

/* DXVK's public Vulkan interop ABI, expressed in C. GetVulkanHandles follows
 * the three IUnknown methods and uses the Windows COM calling convention.
 * https://github.com/doitsujin/dxvk/blob/v2.7.1/src/dxgi/dxgi_interfaces.h */
typedef struct amdnr_dxgi_vk_adapter amdnr_dxgi_vk_adapter;
typedef struct amdnr_dxgi_vk_adapter_vtbl
{
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(amdnr_dxgi_vk_adapter *, REFIID, void **);
    ULONG (STDMETHODCALLTYPE *AddRef)(amdnr_dxgi_vk_adapter *);
    ULONG (STDMETHODCALLTYPE *Release)(amdnr_dxgi_vk_adapter *);
    void (STDMETHODCALLTYPE *GetVulkanHandles)(amdnr_dxgi_vk_adapter *, VkInstance *, VkPhysicalDevice *);
} amdnr_dxgi_vk_adapter_vtbl;
struct amdnr_dxgi_vk_adapter { const amdnr_dxgi_vk_adapter_vtbl *lpVtbl; };
static const GUID iid_dxgi_vk_adapter =
    {0x3a6d8f2c, 0xb0e8, 0x4ab4, {0xb4, 0xdc, 0x4f, 0xd2, 0x48, 0x91, 0xbf, 0xa5}};

static hipError_t match_adapter(PFN_vkGetInstanceProcAddr get, amdnr_dxgi_vk_adapter *adapter,
                               hipDeviceProp_t *properties)
{
    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice device = VK_NULL_HANDLE;
    adapter->lpVtbl->GetVulkanHandles(adapter, &instance, &device);
    if (!instance || !device)
    {
        amdnr_log("DXVK adapter returned empty Vulkan handles");
        return hipErrorNotSupported;
    }
    PFN_vkGetPhysicalDeviceProperties2 query = (PFN_vkGetPhysicalDeviceProperties2)get(instance, "vkGetPhysicalDeviceProperties2");
    PFN_vkEnumerateDeviceExtensionProperties extensions =
        (PFN_vkEnumerateDeviceExtensionProperties)get(instance, "vkEnumerateDeviceExtensionProperties");
    if (!query || !extensions)
    {
        amdnr_log("Wine Vulkan adapter identity queries are unavailable");
        return hipErrorNotSupported;
    }
    uint32_t count = 0;
    VkResult vr = extensions(device, NULL, &count, NULL);
    if (vr != VK_SUCCESS)
    {
        amdnr_log("DXVK adapter Vulkan extension count failed: %d", vr);
        return hipErrorNotSupported;
    }
    int has_pci = 0;
    if (count)
    {
        VkExtensionProperties *ext = calloc(count, sizeof(*ext));
        if (!ext) return hipErrorOutOfMemory;
        vr = extensions(device, NULL, &count, ext);
        if (vr == VK_SUCCESS)
            for (uint32_t i = 0; i < count; i++)
                if (!strcmp(ext[i].extensionName, VK_EXT_PCI_BUS_INFO_EXTENSION_NAME)) has_pci = 1;
        free(ext);
        if (vr != VK_SUCCESS)
        {
            amdnr_log("DXVK adapter Vulkan extension enumeration failed: %d", vr);
            return hipErrorNotSupported;
        }
    }
    VkPhysicalDevicePCIBusInfoPropertiesEXT pci = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PCI_BUS_INFO_PROPERTIES_EXT};
    VkPhysicalDeviceIDProperties id = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES,
                                      .pNext = has_pci ? &pci : NULL};
    VkPhysicalDeviceProperties2 p = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &id};
    query(device, &p);
    int matches = has_pci ? (pci.pciDomain == (unsigned)properties->pciDomainID &&
        pci.pciBus == (unsigned)properties->pciBusID && pci.pciDevice == (unsigned)properties->pciDeviceID) :
        !memcmp(id.deviceUUID, properties->uuid.bytes, VK_UUID_SIZE);
    /* DXGI descriptions and Vulkan vendor/device IDs may be spoofed by the
     * host. Match the native HIP PCI address (or UUID) instead; Wine Vulkan
     * supplies the LUID that the Windows D3D12 device uses. */
    if (!matches) return hipErrorInvalidDevice;
    if (!id.deviceLUIDValid)
    {
        amdnr_log("matching DXVK Vulkan adapter has no valid Windows LUID");
        return hipErrorNotSupported;
    }
    memcpy(properties->luid, id.deviceLUID, sizeof(properties->luid));
    properties->luidDeviceNodeMask = id.deviceNodeMask;
    return hipSuccess;
}

/* Match PCI addresses, not GPU enumeration order or marketing-name guesses.
 * The Windows runtime compares this LUID with ID3D12Device::GetAdapterLuid. */
hipError_t amdnr_fix_identity(hipDeviceProp_t *properties, int ordinal)
{
    HMODULE vulkan = GetModuleHandleW(L"vulkan-1.dll");
    if (!vulkan) vulkan = LoadLibraryW(L"vulkan-1.dll");
    PFN_vkGetInstanceProcAddr get = vulkan ? (void *)GetProcAddress(vulkan, "vkGetInstanceProcAddr") : NULL;
    if (!get)
    {
        amdnr_log("Wine vulkan-1.dll vkGetInstanceProcAddr is unavailable");
        return hipErrorNotSupported;
    }
    /* DXVK factories share an instance. Borrow its adapter handles instead of
     * creating a private instance that the host's Vulkan hooks could capture.
     * Keep the factory and adapter references until all Vulkan queries finish. */
    IDXGIFactory1 *factory = NULL;
    HRESULT hr = CreateDXGIFactory1(&IID_IDXGIFactory1, (void **)&factory);
    if (FAILED(hr) || !factory)
    {
        amdnr_log("CreateDXGIFactory1 for HIP adapter identity failed: %#lx", (unsigned long)hr);
        return hipErrorNotSupported;
    }
    hipError_t result = hipErrorInvalidDevice;
    int unavailable_adapter = 0;
    for (UINT i = 0;; i++)
    {
        IDXGIAdapter1 *adapter = NULL;
        hr = IDXGIFactory1_EnumAdapters1(factory, i, &adapter);
        if (hr == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(hr) || !adapter)
        {
            amdnr_log("DXGI adapter %u enumeration failed: %#lx", i, (unsigned long)hr);
            result = hipErrorNotSupported;
            break;
        }
        amdnr_dxgi_vk_adapter *interop = NULL;
        hr = IDXGIAdapter1_QueryInterface(adapter, &iid_dxgi_vk_adapter, (void **)&interop);
        if (FAILED(hr) || !interop)
        {
            amdnr_log("DXGI adapter %u lacks IDXGIVkInteropAdapter (DXVK required): %#lx", i, (unsigned long)hr);
            IDXGIAdapter1_Release(adapter);
            unavailable_adapter = 1;
            continue;
        }
        result = match_adapter(get, interop, properties);
        interop->lpVtbl->Release(interop);
        IDXGIAdapter1_Release(adapter);
        if (result == hipSuccess)
        {
            amdnr_log("HIP device %d PCI %04x:%02x:%02x matched DXVK adapter %u using its existing Vulkan instance",
                      ordinal, properties->pciDomainID, properties->pciBusID, properties->pciDeviceID, i);
            break;
        }
        if (result == hipErrorOutOfMemory) break;
        if (result == hipErrorNotSupported) unavailable_adapter = 1;
    }
    IDXGIFactory1_Release(factory);
    if (result == hipErrorInvalidDevice && unavailable_adapter) result = hipErrorNotSupported;
    if (result) amdnr_log("no usable DXVK Vulkan adapter matches HIP PCI %04x:%02x:%02x", properties->pciDomainID,
                          properties->pciBusID, properties->pciDeviceID);
    return result;
}
