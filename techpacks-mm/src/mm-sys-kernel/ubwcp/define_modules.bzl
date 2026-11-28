load("//build/kernel/kleaf:kernel.bzl", "ddk_module")
load("//build/bazel_common_rules/dist:dist.bzl", "copy_to_dist_dir")


def define_modules(target, variant):
    tv = "{}_{}".format(target, variant)

    deps = select({
        "//build/qcom_build_extensions:qtisocrepo_true": [
            "//soc-repo:all_headers",
            "//soc-repo:{}/drivers/firmware/qcom/qcom-scm".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/llcc-qcom".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/mem_buf/mem_buf_dev".format(tv),
            "//soc-repo:{}/drivers/dma-buf/heaps/qcom_dma_heaps".format(tv),
            "//soc-repo:{}/drivers/iommu/msm_dma_iommu_mapping".format(tv),
        ],
        "//build/qcom_build_extensions:qtisocrepo_false": ["//msm-kernel:all_headers"],
    })
    kernel_build = select({
        "//build/qcom_build_extensions:qtisocrepo_true": "//soc-repo:{}_base_kernel".format(tv),
        "//build/qcom_build_extensions:qtisocrepo_false": "//msm-kernel:{}".format(tv),
    })

    ddk_module(
        name = "{}_ubwcp".format(tv),
        out = "ubwcp.ko",
        srcs = [
            "ubwcp_main.c",
            "ubwcp_hw.c",
            "ubwcp_hw.h",
            "ubwcp_trace.h",
        ],
        hdrs=["include/uapi/ubwcp_ioctl.h", "include/kernel/ubwcp.h"],
        deps = deps,
        copts = ["-Wno-format", "-fstrict-flex-arrays=0"],
        includes = ["include", "include/kernel"],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"]
    )

    copy_to_dist_dir(
        name = "{}_ubwcp_dist".format(tv),
        data = [":{}_ubwcp".format(tv)],
        dist_dir = "out/target/product/{}/dlkm/lib/modules/".format(target),
        flat = True,
        wipe_dist_dir = False,
        allow_duplicate_filenames = False,
        mode_overrides = {"**/*": "644"},
    )
