// Copyright (c) 2026 3zwr1 (AMDNR)
// SPDX-License-Identifier: GPL-3.0-or-later
// Modified for the Linux port: verify completion before retiring host resources.
#pragma once
#include <cstdint>
#include <limits>

namespace AmdPreSr
{
// GetCompletedValue returns UINT64_MAX when D3D12 removes the device. That
// sentinel must never authorize reuse of GPU resources or command allocators.
inline bool FenceReached(std::uint64_t completed, std::uint64_t target)
{
    return completed != (std::numeric_limits<std::uint64_t>::max)() && completed >= target;
}

// A bounded event wait is only a wake-up mechanism, not proof of completion.
// Re-read the fence even after a signalled event: an old registration can wake
// a later wait early. Callers retain the resources when this returns false.
// Callbacks keep the decision testable without a Windows device or GPU work.
template<class ReadCompleted, class ArmEvent, class WaitEvent>
bool WaitForFenceCompletion(std::uint64_t target, ReadCompleted readCompleted,
                            ArmEvent armEvent, WaitEvent waitEvent)
{
    const auto completed = readCompleted();
    if (FenceReached(completed, target))
        return true;
    if (completed == (std::numeric_limits<std::uint64_t>::max)() || !armEvent())
        return false;
    waitEvent();
    return FenceReached(readCompleted(), target);
}
} // namespace AmdPreSr
