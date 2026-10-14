/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "native.h"
#include "unix_args.h"
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <unistd.h>

int32_t amdnr_unix_vulkan_fd(void *opaque)
{
    struct amdnr_args_vulkan_fd *args = opaque;
    args->fd = -1;
    args->result = -1;
    void *library = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
    PFN_vkGetDeviceProcAddr proc = library ? (PFN_vkGetDeviceProcAddr)dlsym(library, "vkGetDeviceProcAddr") : NULL;
    VkDevice device = (VkDevice)(uintptr_t)args->device;
    PFN_vkGetMemoryFdKHR get_fd = proc ? (PFN_vkGetMemoryFdKHR)proc(device, "vkGetMemoryFdKHR") : NULL;
    VkMemoryGetFdInfoKHR info = {.sType = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
        .memory = (VkDeviceMemory)args->memory, .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT};
    int fd = -1;
    if (get_fd && get_fd(device, &info, &fd) == VK_SUCCESS && fd >= 0)
    { args->fd = fd; args->result = 0; }
    else if (fd >= 0) close(fd);
    if (library) dlclose(library);
    return 0;
}

int32_t amdnr_unix_close_fd(void *opaque)
{
    const struct amdnr_args_close_fd *args = opaque;
    if (args->fd >= 0) close(args->fd);
    return 0;
}
