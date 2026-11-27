load(":eva_modules.bzl", "eva_modules")
load(":eva_module_build.bzl", "define_consolidate_gki_modules")

def define_pineapple():
    define_consolidate_gki_modules(
        target = "pineapple",
        registry = eva_modules,
        modules = [
            "msm-eva",
        ],
        config_options = [
            #"CONFIG_TARGET_SYNX_ENABLE",
            "TARGET_SYNX_ENABLE",
            "TARGET_DSP_ENABLE",
            "CONFIG_EVA_PINEAPPLE",
            "CONFIG_MSM_MMRM",
            "CONFIG_QCOM_LLCC_MODULE",
            "CONFIG_QCOM_MEM_BUF_DEV_MODULE",
            "CONFIG_QCOM_LAZY_MAPPING_MODULE",
            "CONFIG_QCOM_SECURE_BUFFER_MODULE",
            "CONFIG_GH_RM_DRV_MODULE",
            "CONFIG_GH_MSGQ_MODULE",
            "CONFIG_MSM_MMRM_MODULE",
            "CONFIG_INTERCONNECT_QCOM_MODULE",
            "CONFIG_QCOM_RPROC_COMMON_MODULE",
            "CONFIG_RPROC_QCOM_COMMON_MODULE",
            "CONFIG_QCOM_MDT_LOADER_MODULE",
        ],
    )
