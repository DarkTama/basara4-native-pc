// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/logging/log.h"
#include "shader_recompiler/resource.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/texture_cache/image.h"
#include "video_core/texture_cache/image_view.h"
#include "video_core/renderer_vulkan/vk_gpu_checkpoint.h"

#include <magic_enum/magic_enum.hpp>

namespace VideoCore {

vk::ImageViewType ConvertImageViewType(AmdGpu::ImageType type) {
    switch (type) {
    case AmdGpu::ImageType::Color1D:
        return vk::ImageViewType::e1D;
    case AmdGpu::ImageType::Color1DArray:
        return vk::ImageViewType::e1DArray;
    case AmdGpu::ImageType::Color2D:
    case AmdGpu::ImageType::Color2DMsaa:
        return vk::ImageViewType::e2D;
    case AmdGpu::ImageType::Color2DArray:
        return vk::ImageViewType::e2DArray;
    case AmdGpu::ImageType::Color3D:
        return vk::ImageViewType::e3D;
    default:
        UNREACHABLE();
    }
}

bool IsViewTypeCompatible(AmdGpu::ImageType view_type, AmdGpu::ImageType image_type) {
    switch (view_type) {
    case AmdGpu::ImageType::Color1D:
    case AmdGpu::ImageType::Color1DArray:
        return image_type == AmdGpu::ImageType::Color1D;
    case AmdGpu::ImageType::Color2D:
    case AmdGpu::ImageType::Color2DArray:
    case AmdGpu::ImageType::Color2DMsaa:
    case AmdGpu::ImageType::Color2DMsaaArray:
        return image_type == AmdGpu::ImageType::Color2D || image_type == AmdGpu::ImageType::Color3D;
    case AmdGpu::ImageType::Color3D:
        return image_type == AmdGpu::ImageType::Color3D;
    default:
        UNREACHABLE();
    }
}

ImageViewInfo::ImageViewInfo(const AmdGpu::Image& image, const Shader::ImageResource& desc) noexcept
    : is_storage{desc.is_written} {
    const auto dfmt = image.GetDataFmt();
    auto nfmt = image.GetNumberFmt();
    if (is_storage && nfmt == AmdGpu::NumberFormat::Srgb) {
        nfmt = AmdGpu::NumberFormat::Unorm;
    }
    format = Vulkan::LiverpoolToVK::SurfaceFormat(dfmt, nfmt);
    if (desc.is_depth) {
        format = Vulkan::LiverpoolToVK::PromoteFormatToDepth(format);
    }

    range.base.level = image.base_level;
    range.base.layer = std::min<u32>(image.base_array, image.NumLayers() - 1);
    range.extent.levels = image.NumViewLevels(desc.is_array);
    range.extent.layers = image.NumViewLayers(desc.is_array);
    type = image.GetViewType(desc.is_array);
    min_lod = static_cast<u32>(image.min_lod);

    if (!is_storage) {
        mapping = Vulkan::LiverpoolToVK::ComponentMapping(image.DstSelect());
    }
}

ImageViewInfo::ImageViewInfo(const AmdGpu::ColorBuffer& col_buffer) noexcept {
    range.base.layer = col_buffer.BaseSlice();
    range.extent.layers = col_buffer.NumSlices() - range.base.layer;
    type = range.extent.layers > 1 ? AmdGpu::ImageType::Color2DArray : AmdGpu::ImageType::Color2D;
    format =
        Vulkan::LiverpoolToVK::SurfaceFormat(col_buffer.GetDataFmt(), col_buffer.GetNumberFmt());
}

ImageViewInfo::ImageViewInfo(const AmdGpu::DepthBuffer& depth_buffer, AmdGpu::DepthView view,
                             AmdGpu::DepthControl ctl) {
    format = Vulkan::LiverpoolToVK::DepthFormat(depth_buffer.z_info.format,
                                                depth_buffer.stencil_info.format);
    is_storage = ctl.depth_write_enable;
    range.base.layer = view.slice_start;
    range.extent.layers = view.NumSlices() - range.base.layer;
    type = range.extent.layers > 1 ? AmdGpu::ImageType::Color2DArray : AmdGpu::ImageType::Color2D;
}

ImageView::ImageView(const Vulkan::Instance& instance, const ImageViewInfo& info_,
                     const Image& image)
    : info{info_} {
    {
        // Basara 4 PC: a view may not reach past the image it looks at (VUID-VkImageViewCreateInfo-
        // subresourceRange-01718/-01720). The guest descriptor (T#) of a cutscene texture asked for 3 mip
        // levels of an image that has 1; the driver was handed an invalid view and the frame never
        // finished (black screen, the game spinning on a GPU label). Clamp to what the image has.
        const u32 image_levels = std::max<u32>(1u, image.info.resources.levels);
        const u32 image_layers = std::max<u32>(1u, image.info.resources.layers);
        const auto before = info.range;
        info.range.base.level = std::min<u32>(info.range.base.level, image_levels - 1);
        info.range.extent.levels = std::max<u32>(
            1u, std::min<u32>(info.range.extent.levels, image_levels - info.range.base.level));
        info.range.base.layer = std::min<u32>(info.range.base.layer, image_layers - 1);
        info.range.extent.layers = std::max<u32>(
            1u, std::min<u32>(info.range.extent.layers, image_layers - info.range.base.layer));
        if (before.base.level != info.range.base.level || before.extent.levels != info.range.extent.levels ||
            before.base.layer != info.range.base.layer || before.extent.layers != info.range.extent.layers) {
            static u32 logged = 0;
            if (logged++ < 8) {
                LOG_WARNING(Render_Vulkan,
                            "[{}] image view range (levels {}+{}, layers {}+{}) exceeds its image {:#x} "
                            "{}x{}x{} ({} levels, {} layers, fmt {}); clamped to levels {}+{}, layers {}+{}",
                            Vulkan::GpuCheckpoint::WallClock(), before.base.level, before.extent.levels, before.base.layer,
                            before.extent.layers, image.info.guest_address, image.info.size.width,
                            image.info.size.height, image.info.size.depth, image_levels, image_layers,
                            vk::to_string(image.info.pixel_format), info.range.base.level,
                            info.range.extent.levels, info.range.base.layer, info.range.extent.layers);
            }
        }
    }
    vk::ImageViewUsageCreateInfo usage_ci{.usage = image.usage_flags};
    if (!info.is_storage) {
        usage_ci.usage &= ~vk::ImageUsageFlagBits::eStorage;
    }
    vk::ImageViewMinLodCreateInfoEXT min_lod_ci{};
    if (info.min_lod != 0 && instance.IsImageViewMinLodSupported()) {
        const float last_level =
            static_cast<float>(info.range.base.level + info.range.extent.levels - 1);
        min_lod_ci.minLod = std::min(static_cast<float>(info.min_lod) / 256.f, last_level);
        usage_ci.pNext = &min_lod_ci;
    }
    // When sampling D32/D16 texture from shader, the T# specifies R32/R16 format so adjust it.
    vk::Format format = info.format;
    vk::ImageAspectFlags aspect = image.aspect_mask;
    if (image.aspect_mask & vk::ImageAspectFlagBits::eDepth &&
        Vulkan::LiverpoolToVK::IsFormatDepthCompatible(format)) {
        format = image.info.pixel_format;
        aspect = vk::ImageAspectFlagBits::eDepth;
    }
    if (image.aspect_mask & vk::ImageAspectFlagBits::eStencil &&
        Vulkan::LiverpoolToVK::IsFormatStencilCompatible(format)) {
        format = image.info.pixel_format;
        aspect = vk::ImageAspectFlagBits::eStencil;
    }

    // Basara 4 PC: a view of a depth/stencil image must use the image's own format and, to be sampled,
    // exactly one aspect (VUID-VkImageViewCreateInfo-image-01761, VUID-VkDescriptorImageInfo-imageView-
    // 01976).  A guest texture that re-uses the memory of a depth buffer with another format (here a
    // 3-level texture on a 1920x1080 D32S8 image) used to get a view in the T#'s format with both
    // aspects: invalid, and the NVIDIA driver lost the device in the first draw that sampled it
    // (Windows event nvlddmkm 153, draw checkpoint: vs 0xd5df186b / ps 0x3c869134).
    if ((image.aspect_mask & (vk::ImageAspectFlagBits::eDepth | vk::ImageAspectFlagBits::eStencil)) &&
        format != image.info.pixel_format) {
        static u32 logged = 0;
        if (logged++ < 8) {
            LOG_WARNING(Render_Vulkan,
                        "[{}] view of depth/stencil image {:#x} {} asked for format {} aspect {}: using the image's format, one aspect",
                        Vulkan::GpuCheckpoint::WallClock(), image.info.guest_address, vk::to_string(image.info.pixel_format),
                        vk::to_string(format), vk::to_string(aspect));
        }
        format = image.info.pixel_format;
        aspect = (image.aspect_mask & vk::ImageAspectFlagBits::eDepth) ? vk::ImageAspectFlagBits::eDepth
                                                                       : vk::ImageAspectFlagBits::eStencil;
    }

    // spartanport: storage writes never convert to sRGB and sRGB formats cannot be storage images
    // (AMD's driver loses the device on such a view; NVIDIA tolerates it): write through the
    // UNORM format, which stores the same bytes.
    if (info.is_storage) {
        switch (format) {
        case vk::Format::eR8G8B8A8Srgb:
            format = vk::Format::eR8G8B8A8Unorm;
            break;
        case vk::Format::eB8G8R8A8Srgb:
            format = vk::Format::eB8G8R8A8Unorm;
            break;
        case vk::Format::eA8B8G8R8SrgbPack32:
            format = vk::Format::eA8B8G8R8UnormPack32;
            break;
        case vk::Format::eR8Srgb:
            format = vk::Format::eR8Unorm;
            break;
        case vk::Format::eR8G8Srgb:
            format = vk::Format::eR8G8Unorm;
            break;
        default:
            break;
        }
    }

    const vk::ImageViewCreateInfo image_view_ci = {
        .pNext = &usage_ci,
        .image = image.GetImage(),
        .viewType = ConvertImageViewType(info.type),
        .format = instance.GetSupportedFormat(format, image.format_features),
        .components = info.mapping,
        .subresourceRange{
            .aspectMask = aspect,
            .baseMipLevel = info.range.base.level,
            .levelCount = info.range.extent.levels,
            .baseArrayLayer = info.range.base.layer,
            .layerCount = info.range.extent.layers,
        },
    };
    if (!IsViewTypeCompatible(info.type, image.info.type)) {
        LOG_ERROR(Render_Vulkan, "image view type {} is incompatible with image type {}",
                  magic_enum::enum_name(info.type), magic_enum::enum_name(image.info.type));
    }

    auto [view_result, view] = instance.GetDevice().createImageViewUnique(image_view_ci);
    ASSERT_MSG(view_result == vk::Result::eSuccess, "Failed to create image view: {}",
               vk::to_string(view_result));
    image_view = std::move(view);

    const auto view_aspect = aspect & vk::ImageAspectFlagBits::eDepth     ? "Depth"
                             : aspect & vk::ImageAspectFlagBits::eStencil ? "Stencil"
                                                                          : "Color";
    Vulkan::SetObjectName(
        instance.GetDevice(), *image_view, "ImageView {}x{}x{} {:#x}:{:#x} {}:{} {}:{} ({})",
        image.info.size.width, image.info.size.height, image.info.size.depth,
        image.info.guest_address, image.info.guest_size, info.range.base.level,
        info.range.base.level + info.range.extent.levels - 1, info.range.base.layer,
        info.range.base.layer + info.range.extent.layers - 1, view_aspect);
}

ImageView::~ImageView() = default;

} // namespace VideoCore
