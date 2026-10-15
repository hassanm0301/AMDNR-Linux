// SPDX-License-Identifier: GPL-3.0-or-later
#include "../optiscaler/dlssnr/amd/FenceWait.h"
#include <cassert>
#include <cstdint>
#include <limits>

namespace
{
struct FakeFence
{
    std::uint64_t before = 0, after = 0;
    bool registrationWorks = true;
    unsigned reads = 0, registrations = 0, waits = 0;

    bool Wait(std::uint64_t target)
    {
        return AmdPreSr::WaitForFenceCompletion(target,
            [&] { return reads++ == 0 ? before : after; },
            [&] { ++registrations; return registrationWorks; },
            [&] { ++waits; });
    }
};
}

int main()
{
    // Already completed: never register an event or wait on the render thread.
    FakeFence completed { 7, 0 };
    assert(completed.Wait(7));
    assert(completed.reads == 1 && completed.registrations == 0 && completed.waits == 0);
    FakeFence ahead { 8, 0 };
    assert(ahead.Wait(7));

    // Completion during the wait authorizes retirement, whatever woke the event.
    FakeFence signalled { 6, 7 };
    assert(signalled.Wait(7));
    assert(signalled.reads == 2 && signalled.registrations == 1 && signalled.waits == 1);

    // A timeout, failed wait, or stale event must not release in-flight resources.
    FakeFence incomplete { 6, 6 };
    assert(!incomplete.Wait(7));
    assert(incomplete.reads == 2 && incomplete.waits == 1);

    // Missing handles and SetEventOnCompletion failure: no unarmed wait.
    FakeFence cannotArm { 6, 7, false };
    assert(!cannotArm.Wait(7));
    assert(cannotArm.reads == 1 && cannotArm.registrations == 1 && cannotArm.waits == 0);

    // D3D12 removal is not a fence reaching every value, before or during a wait.
    constexpr auto removed = (std::numeric_limits<std::uint64_t>::max)();
    FakeFence removedBefore { removed, 7 };
    assert(!removedBefore.Wait(7));
    assert(removedBefore.registrations == 0 && removedBefore.waits == 0);
    FakeFence removedDuring { 6, removed };
    assert(!removedDuring.Wait(7));
    assert(removedDuring.reads == 2 && removedDuring.waits == 1);

    // Keep comparisons at 64-bit precision rather than narrowing to a frame index.
    constexpr std::uint64_t large = std::uint64_t { 1 } << 40;
    FakeFence largeComplete { large, 0 };
    assert(largeComplete.Wait(large));
    assert(!AmdPreSr::FenceReached(large - 1, large));
}
