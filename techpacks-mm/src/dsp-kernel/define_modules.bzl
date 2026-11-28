load("//build/bazel_common_rules/dist:dist.bzl", "copy_to_dist_dir")
load(
    "//build/kernel/kleaf:kernel.bzl",
    "ddk_headers",
    "ddk_module",
)

def define_modules(target, variant):
    tv = "{}_{}".format(target, variant)

    kernel_build = select({
        "//build/qcom_build_extensions:qtisocrepo_true": "//soc-repo:{}_base_kernel".format(tv),
        "//build/qcom_build_extensions:qtisocrepo_false": "//msm-kernel:{}".format(tv),
    })

    ddk_deps = select({
        "//build/qcom_build_extensions:qtisocrepo_true": [
            "//soc-repo:all_headers",
            "//soc-repo:{}/drivers/firmware/qcom/qcom-scm".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/mem_buf/mem_buf_dev".format(tv),
            "//soc-repo:{}/drivers/dma-buf/heaps/qcom_dma_heaps".format(tv),
            "//soc-repo:{}/drivers/iommu/msm_dma_iommu_mapping".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/secure_buffer".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/pdr_interface".format(tv),
            "//soc-repo:{}/drivers/rpmsg/qcom_glink".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/socinfo".format(tv),
            "//soc-repo:{}/kernel/trace/qcom_ipc_logging".format(tv),
            "//soc-repo:{}/drivers/remoteproc/rproc_qcom_common".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/minidump".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/qcom_va_minidump".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/qcom_ramdump".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/smem".format(tv),
            "//soc-repo:{}/drivers/virt/gunyah/gh_rm_drv".format(tv),
            "//soc-repo:{}/drivers/virt/gunyah/gh_msgq".format(tv),
            "//soc-repo:{}/drivers/virt/gunyah/gh_dbl".format(tv),
        ],
        "//build/qcom_build_extensions:qtisocrepo_false": ["//msm-kernel:all_headers"],
    })

    cdsp_deps = select({
        "//build/qcom_build_extensions:qtisocrepo_true": [
            "//soc-repo:all_headers",
            "//soc-repo:{}/drivers/remoteproc/rproc_qcom_common".format(tv),
        ],
        "//build/qcom_build_extensions:qtisocrepo_false": ["//msm-kernel:all_headers"],
    })

    trace_include_path = "../../../{}/dsp".format(native.package_name())

    ddk_module(
        name = "{}_frpc-adsprpc".format(tv),
        kernel_build = kernel_build,
        deps = ddk_deps,
        srcs = [
            "dsp/adsprpc.c",
            "dsp/adsprpc_compat.c",
            "dsp/adsprpc_compat.h",
            "dsp/adsprpc_rpmsg.c",
            "dsp/adsprpc_shared.h",
            "dsp/fastrpc_trace.h",
        ],
        local_defines = [
            "DSP_TRACE_INCLUDE_PATH={}".format(trace_include_path),
            "CONFIG_DSP_PINEAPPLE=1",
            "CONFIG_QCOM_PDR_HELPERS_MODULE=1",
            "CONFIG_QCOM_RPROC_COMMON_MODULE=1",
            "CONFIG_RPROC_QCOM_COMMON_MODULE=1",
            "CONFIG_QCOM_MEM_BUF_DEV_MODULE=1",
            "CONFIG_QCOM_LAZY_MAPPING_MODULE=1",
            "CONFIG_QCOM_SECURE_BUFFER_MODULE=1",
            "CONFIG_GH_RM_DRV_MODULE=1",
            "CONFIG_QCOM_VA_MINIDUMP_MODULE=1",
            "CONFIG_QCOM_MINIDUMP_MODULE=1",
            "CONFIG_QCOM_SMEM_MODULE=1",
            "CONFIG_QCOM_SOCINFO_MODULE=1",
            "CONFIG_IPC_LOGGING_MODULE=1",
        ],
        copts = ["-Wno-format", "-Wno-unused-function", "-Wno-unused-variable", "-fstrict-flex-arrays=0"],
        out = "frpc-adsprpc.ko",
        hdrs = [
            "include/linux/fastrpc.h",
            "include/uapi/fastrpc_shared.h",
        ],
        includes = [
            "include/linux",
            "include/uapi",
        ],
    )

    ddk_module(
        name = "{}_cdsp-loader".format(tv),
        kernel_build = kernel_build,
        deps = cdsp_deps,
        srcs = ["dsp/cdsp-loader.c"],
        copts = ["-Wno-format", "-fstrict-flex-arrays=0"],
        out = "cdsp-loader.ko",
    )

    copy_to_dist_dir(
        name = "{}_dsp-kernel_dist".format(tv),
        data = [
            ":{}_frpc-adsprpc".format(tv),
            ":{}_cdsp-loader".format(tv),
        ],
        dist_dir = "out/target/product/{}/dlkm/lib/modules/".format(target),
        flat = True,
        wipe_dist_dir = False,
        allow_duplicate_filenames = False,
        mode_overrides = {"**/*": "644"},
    )
