/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "bridge.h"
#define VK_NO_PROTOTYPES
#define VK_USE_PLATFORM_WIN32_KHR
#include <vulkan/vulkan.h>
#include <initguid.h>
#include <dxgi1_6.h>
#include <stddef.h>
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

/* DXVK leaves DXGI_ADAPTER_DESC::Revision at 0: Vulkan has no PCI revision.
 * The Windows host identifies a card by vendor, device and revision, and its
 * table lists the RX 9070 XT only as 1002:7550 revision C0, so with 0 it does
 * not know the GPU and turns off what it keeps for that class. Once a HIP
 * device is matched to its DXVK adapter, that adapter's GetDesc calls report
 * the PCI revision from sysfs where DXVK says 0 and the vendor and device IDs
 * are the real ones (a spoofed description is left as it is). */
enum { DESC_CALLS = 4, MAX_ADAPTERS = 8, MAX_VTABLES = 4 };
typedef HRESULT (STDMETHODCALLTYPE *desc_call)(IUnknown *, DXGI_ADAPTER_DESC *);
/* GetDesc1..3 fill the same leading fields as GetDesc. */
_Static_assert(offsetof(DXGI_ADAPTER_DESC3, Revision) == offsetof(DXGI_ADAPTER_DESC, Revision) &&
               offsetof(DXGI_ADAPTER_DESC3, AdapterLuid) == offsetof(DXGI_ADAPTER_DESC, AdapterLuid) &&
               offsetof(DXGI_ADAPTER_DESC1, AdapterLuid) == offsetof(DXGI_ADAPTER_DESC, AdapterLuid),
               "DXGI adapter descriptions share their leading fields");
static const size_t desc_slot[DESC_CALLS] = {
    offsetof(IDXGIAdapter4Vtbl, GetDesc) / sizeof(void *), offsetof(IDXGIAdapter4Vtbl, GetDesc1) / sizeof(void *),
    offsetof(IDXGIAdapter4Vtbl, GetDesc2) / sizeof(void *), offsetof(IDXGIAdapter4Vtbl, GetDesc3) / sizeof(void *)};
static struct { LUID luid; unsigned vendor, device, revision; } pci_ids[MAX_ADAPTERS];
static struct { void **vtable; desc_call original[DESC_CALLS]; } wrapped[MAX_VTABLES];
static volatile LONG pci_count, wrapped_count;
static SRWLOCK pci_lock = SRWLOCK_INIT;

static void report_revision(DXGI_ADAPTER_DESC *desc)
{
    if (desc->Revision) return;
    AcquireSRWLockShared(&pci_lock);
    for (LONG i = 0; i < pci_count; i++)
        if (!memcmp(&pci_ids[i].luid, &desc->AdapterLuid, sizeof(LUID)) && pci_ids[i].vendor == desc->VendorId &&
            pci_ids[i].device == desc->DeviceId)
        {
            desc->Revision = pci_ids[i].revision;
            break;
        }
    ReleaseSRWLockShared(&pci_lock);
}

static HRESULT forward_desc(IUnknown *adapter, DXGI_ADAPTER_DESC *desc, int call)
{
    void **vtable = *(void ***)adapter;
    desc_call original = NULL;
    /* An entry is complete before its count is published and before its slots
     * point here, so no lock is needed to find it. */
    for (LONG i = 0; i < wrapped_count && !original; i++)
        if (wrapped[i].vtable == vtable) original = wrapped[i].original[call];
    if (!original) return E_FAIL;
    HRESULT hr = original(adapter, desc);
    if (SUCCEEDED(hr) && desc) report_revision(desc);
    return hr;
}
static HRESULT STDMETHODCALLTYPE get_desc(IUnknown *adapter, DXGI_ADAPTER_DESC *desc) { return forward_desc(adapter, desc, 0); }
static HRESULT STDMETHODCALLTYPE get_desc1(IUnknown *adapter, DXGI_ADAPTER_DESC *desc) { return forward_desc(adapter, desc, 1); }
static HRESULT STDMETHODCALLTYPE get_desc2(IUnknown *adapter, DXGI_ADAPTER_DESC *desc) { return forward_desc(adapter, desc, 2); }
static HRESULT STDMETHODCALLTYPE get_desc3(IUnknown *adapter, DXGI_ADAPTER_DESC *desc) { return forward_desc(adapter, desc, 3); }
static const desc_call desc_wrappers[DESC_CALLS] = {get_desc, get_desc1, get_desc2, get_desc3};

static int same_object(IUnknown *adapter, REFIID iid)
{
    IUnknown *other = NULL;
    if (FAILED(IUnknown_QueryInterface(adapter, iid, (void **)&other)) || !other) return 0;
    IUnknown_Release(other);
    return other == adapter;
}

/* DXVK's adapters share one vtable, so wrapping its GetDesc slots covers every
 * factory the host or the game creates. The previous slot values are called,
 * so hooks others installed there stay in the chain. */
static void wrap_desc_calls(IUnknown *adapter)
{
    void **vtable = *(void ***)adapter;
    int calls = same_object(adapter, &IID_IDXGIAdapter4) ? 4 : same_object(adapter, &IID_IDXGIAdapter2) ? 3 : 2;
    AcquireSRWLockExclusive(&pci_lock);
    LONG i = 0;
    while (i < wrapped_count && wrapped[i].vtable != vtable) i++;
    if (i == wrapped_count && i < MAX_VTABLES)
    {
        /* Pinned: a game freeing the bridge would leave the slots pointing at unmapped code. */
        HMODULE self;
        GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                           (LPCWSTR)(void *)wrap_desc_calls, &self);
        wrapped[i].vtable = vtable;
        for (int c = 0; c < calls; c++) wrapped[i].original[c] = (desc_call)vtable[desc_slot[c]];
        InterlockedIncrement(&wrapped_count);
        for (int c = 0; c < calls; c++)
        {
            void **slot = &vtable[desc_slot[c]];
            DWORD protect;
            if (!VirtualProtect(slot, sizeof(*slot), PAGE_READWRITE, &protect))
            {
                amdnr_log("DXGI adapter GetDesc slot %u could not be made writable: %lu", (unsigned)desc_slot[c], GetLastError());
                continue;
            }
            InterlockedExchangePointer(slot, (void *)desc_wrappers[c]);
            VirtualProtect(slot, sizeof(*slot), protect, &protect);
        }
    }
    ReleaseSRWLockExclusive(&pci_lock);
}

static int pci_known(const LUID *luid)
{
    for (LONG i = 0; i < pci_count; i++)
        if (!memcmp(&pci_ids[i].luid, luid, sizeof(*luid))) return 1;
    return 0;
}

/* Once per adapter: the first properties call that matches it decides. The
 * wrapper is installed whatever this call sees, since the host's own DXGI
 * spoofing can show other IDs here than the unspoofed calls it identifies
 * the GPU with; report_revision compares the IDs on every call. */
static void report_pci_revision(const hipDeviceProp_t *properties, IDXGIAdapter1 *adapter, amdnr_dxgi_vk_adapter *interop)
{
    LUID luid;
    memcpy(&luid, properties->luid, sizeof(luid));
    AcquireSRWLockShared(&pci_lock);
    int known = pci_known(&luid);
    ReleaseSRWLockShared(&pci_lock);
    if (known) return;
    unsigned vendor = 0, device = 0, revision = 0;
    if (amdnr_native_pci_identity(properties->pciDomainID, properties->pciBusID, properties->pciDeviceID,
                                  &vendor, &device, &revision))
        amdnr_log("PCI %04x:%02x:%02x IDs are not readable from sysfs; DXGI keeps DXVK's adapter revision",
                  properties->pciDomainID, properties->pciBusID, properties->pciDeviceID);
    AcquireSRWLockExclusive(&pci_lock);
    const int added = !pci_known(&luid) && pci_count < MAX_ADAPTERS;
    if (added)
    {
        pci_ids[pci_count].luid = luid;
        pci_ids[pci_count].vendor = vendor;
        pci_ids[pci_count].device = device;
        pci_ids[pci_count].revision = revision;
        InterlockedIncrement(&pci_count);
    }
    ReleaseSRWLockExclusive(&pci_lock);
    if (!added || !revision) return;
    /* DXVK's own adapter behind the interface, in case the one enumerated is another layer's wrapper. */
    IDXGIAdapter1 *dxvk = NULL;
    if (FAILED(interop->lpVtbl->QueryInterface(interop, &IID_IDXGIAdapter1, (void **)&dxvk)) || !dxvk)
    {
        dxvk = adapter;
        IDXGIAdapter1_AddRef(dxvk);
    }
    wrap_desc_calls((IUnknown *)dxvk);
    IDXGIAdapter1_Release(dxvk);
    DXGI_ADAPTER_DESC1 desc;
    if (FAILED(IDXGIAdapter1_GetDesc1(adapter, &desc)))
        amdnr_log("DXGI adapter for PCI %04x:%04x revision %02x: GetDesc1 failed", vendor, device, revision);
    else if (desc.VendorId == vendor && desc.DeviceId == device)
        amdnr_log("DXGI adapter %04x:%04x reports PCI revision %02x (sysfs %02x)", vendor, device, desc.Revision, revision);
    else
        amdnr_log("DXGI adapter shows %04x:%04x here (spoofed); calls that see PCI %04x:%04x get revision %02x",
                  desc.VendorId, desc.DeviceId, vendor, device, revision);
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
        if (result == hipSuccess) report_pci_revision(properties, adapter, interop);
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
