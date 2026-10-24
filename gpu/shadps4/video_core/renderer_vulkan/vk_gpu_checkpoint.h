// SPDX-License-Identifier: GPL-2.0-or-later
// Basara 4 PC (Razz): find the draw that kills the GPU.
//
// When the NVIDIA driver loses the device (Windows event nvlddmkm 153) the game used to wait forever on
// a semaphore at 100% CPU, with nothing in the log. VK_NV_device_diagnostic_checkpoints lets the GPU
// remember the last marker it passed in each queue. Every draw and dispatch sets a marker whose value is an
// id into a small ring of descriptions (shader hashes, counts); on VK_ERROR_DEVICE_LOST the markers the GPU
// reached are read back and written to the log.

#pragma once

#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <vector>

#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan::GpuCheckpoint {

struct Entry {
    std::atomic<u64> id{0};
    char kind[12]{};
    u64 a{}, b{}, c{}, d{};
};

inline constexpr u32 RingSize = 1 << 14;

inline std::array<Entry, RingSize>& Ring() {
    static std::array<Entry, RingSize> ring;
    return ring;
}

inline std::atomic<bool>& EnabledFlag() {
    static std::atomic<bool> enabled{false};
    return enabled;
}
inline std::atomic<u64>& Counter() {
    static std::atomic<u64> counter{0};
    return counter;
}

[[nodiscard]] inline bool Enabled() {
    return EnabledFlag().load(std::memory_order_relaxed);
}

/// Records a description and returns the marker for it (0 when checkpoints are unavailable).
inline u64 Note(const char* kind, u64 a = 0, u64 b = 0, u64 c = 0, u64 d = 0) {
    if (!Enabled()) {
        return 0;
    }
    const u64 id = Counter().fetch_add(1, std::memory_order_relaxed) + 1;
    auto& e = Ring()[id % RingSize];
    e.id.store(0, std::memory_order_relaxed);
    std::snprintf(e.kind, sizeof(e.kind), "%s", kind);
    e.a = a;
    e.b = b;
    e.c = c;
    e.d = d;
    e.id.store(id, std::memory_order_release);
    return id;
}

inline void Mark(vk::CommandBuffer cmdbuf, u64 id) {
    if (id) {
        cmdbuf.setCheckpointNV(reinterpret_cast<const void*>(static_cast<uintptr_t>(id)));
    }
}

/// Logs where the GPU stopped.  Call once the device is known to be lost.
inline void ReportLost(vk::Queue queue, const char* where) {
    static std::atomic<bool> reported{false};
    if (reported.exchange(true)) {
        return;
    }
    std::fprintf(stderr, "GPU: DEVICE LOST in %s (Windows event nvlddmkm 153 / a driver-reported GPU error)\n", where);
    std::fprintf(stdout, "GPU: DEVICE LOST in %s (Windows event nvlddmkm 153 / a driver-reported GPU error)\n", where);
    if (!Enabled()) {
        std::printf("GPU: checkpoints were not available, the faulting draw is unknown\n");
        std::fflush(stdout);
        return;
    }
    const auto data = queue.getCheckpointDataNV();
    std::printf("GPU: %zu checkpoint(s) read back from the queue; last draws/dispatches issued before it, newest id last: ", data.size());
    std::printf("\n");
    for (const auto& cp : data) {
        const u64 id = static_cast<u64>(reinterpret_cast<uintptr_t>(cp.pCheckpointMarker));
        const auto& e = Ring()[id % RingSize];
        if (e.id.load(std::memory_order_acquire) == id) {
            std::printf("  stage %s: #%llu %s a=%#llx b=%#llx c=%llu d=%llu\n", vk::to_string(cp.stage).c_str(),
                        static_cast<unsigned long long>(id), e.kind, static_cast<unsigned long long>(e.a),
                        static_cast<unsigned long long>(e.b), static_cast<unsigned long long>(e.c),
                        static_cast<unsigned long long>(e.d));
        } else {
            std::printf("  stage %s: #%llu (description already overwritten)\n", vk::to_string(cp.stage).c_str(),
                        static_cast<unsigned long long>(id));
        }
    }
    const u64 last = Counter().load();
    std::printf("GPU: the newest 12 descriptions (most recent last):\n");
    for (u64 id = last > 12 ? last - 11 : 1; id <= last; ++id) {
        const auto& e = Ring()[id % RingSize];
        if (e.id.load(std::memory_order_acquire) == id) {
            std::printf("  #%llu %s a=%#llx b=%#llx c=%llu d=%llu\n", static_cast<unsigned long long>(id), e.kind,
                        static_cast<unsigned long long>(e.a), static_cast<unsigned long long>(e.b),
                        static_cast<unsigned long long>(e.c), static_cast<unsigned long long>(e.d));
        }
    }
    std::fflush(stdout);
}

} // namespace Vulkan::GpuCheckpoint
