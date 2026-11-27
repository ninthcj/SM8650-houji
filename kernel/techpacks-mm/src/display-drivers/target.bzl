load(":display_modules.bzl", "display_driver_modules")
load(":display_driver_build.bzl", "define_target_variant_modules")
load(":target_variants.bzl", "get_all_la_variants", "get_all_le_variants", "get_all_lxc_variants", "get_all_lunch_target_base_target_variants")

def define_pineapple(t, v, lt=None):
    define_target_variant_modules(
        target = t,
        variant = v,
        registry = display_driver_modules,
        modules = [
            "msm_drm",
        ],
        config_options = [
            "CONFIG_DRM_MSM_SDE",
            "CONFIG_SYNC_FILE",
            "CONFIG_DRM_MSM_DSI",
            "CONFIG_DRM_MSM_DP",
            "CONFIG_DSI_PARSER",
            "CONFIG_DRM_SDE_WB",
            "CONFIG_DRM_SDE_RSC",
            "CONFIG_DRM_MSM_REGISTER_LOGGING",
            "CONFIG_QCOM_MDSS_PLL",
            "CONFIG_HDCP_QSEECOM",
            "CONFIG_QCOM_WCD939X_I2C",
            "CONFIG_QCOM_WCD_USBSS_I2C_MODULE",
            "CONFIG_THERMAL_OF",
            "CONFIG_MSM_MMRM",
            "CONFIG_QTI_HW_FENCE",
            "CONFIG_QCOM_SPEC_SYNC",
            "CONFIG_MSM_EXT_DISPLAY",
            "CONFIG_QCOM_LLCC_MODULE",
            "CONFIG_QCOM_LAZY_MAPPING_MODULE",
            "CONFIG_QCOM_PANEL_EVENT_NOTIFIER_MODULE",
            "CONFIG_QCOM_RPMH_MODULE",
            "CONFIG_IPC_LOGGING_MODULE",
            "CONFIG_DRM_SDE_EVTLOG_DEBUG",
            "CONFIG_QCOM_VA_MINIDUMP_MODULE",
            "CONFIG_QTI_ALTMODE_GLINK_MODULE",
            "CONFIG_USB_DWC3_MSM_MODULE",
            "CONFIG_QCOM_SMEM_MODULE",
            "CONFIG_SPMI_MSM_PMIC_ARB_MODULE",
            "CONFIG_QCOM_MEM_BUF_DEV_MODULE",
            "CONFIG_QCOM_MEM_BUF_GH_MODULE",
            "CONFIG_QCOM_SECURE_BUFFER_MODULE",
            "CONFIG_GH_RM_DRV_MODULE",
            "CONFIG_GH_IRQ_LEND_MODULE",
            "CONFIG_GH_MEM_NOTIFIER_MODULE",
            "CONFIG_GH_MSGQ_MODULE",
            "CONFIG_GH_DBL_MODULE",
            "CONFIG_GH_ARM64_DRV_MODULE",
            "MI_DISPLAY_MODIFY",
        ],
        lunch_target = lt,
)

def define_blair(t, v, lt=None):
    define_target_variant_modules(
        target = t,
        variant = v,
        registry = display_driver_modules,
        modules = [
            "msm_drm",
        ],
         config_options = [
            "CONFIG_DRM_MSM_SDE",
            "CONFIG_DRM_MSM_DSI",
            "CONFIG_THERMAL_OF",
            "CONFIG_DSI_PARSER",
            "CONFIG_DRM_MSM_REGISTER_LOGGING",
            "CONFIG_QCOM_MDSS_PLL",
            "CONFIG_MSM_SDE_ROTATOR",
            "CONFIG_SYNC_FILE",
            "CONFIG_MSM_SDE_ROTATOR_EVTLOG_DEBUG",
            "CONFIG_DEBUG_FS",
        ],
        lunch_target = lt,
)

def define_pitti(t, v, lt=None):
    define_target_variant_modules(
        target = t,
        variant = v,
        registry = display_driver_modules,
        modules = [
            "msm_drm",
        ],
         config_options = [
            "CONFIG_DRM_MSM_SDE",
            "CONFIG_DRM_MSM_DSI",
            "CONFIG_THERMAL_OF",
            "CONFIG_DSI_PARSER",
            "CONFIG_DRM_MSM_REGISTER_LOGGING",
            "CONFIG_QCOM_MDSS_PLL",
            "CONFIG_MSM_SDE_ROTATOR",
            "CONFIG_SYNC_FILE",
            "CONFIG_MSM_SDE_ROTATOR_EVTLOG_DEBUG",
            "CONFIG_DEBUG_FS",
        ],
        lunch_target = lt,
)

def define_volcano(t, v, lt=None):
    define_target_variant_modules(
        target = t,
        variant = v,
        registry = display_driver_modules,
        modules = [
            "msm_drm",
        ],
         config_options = [
            "CONFIG_DRM_MSM_SDE",
            "CONFIG_SYNC_FILE",
            "CONFIG_DRM_MSM_DSI",
            "CONFIG_DRM_MSM_DP",
            "CONFIG_DRM_MSM_DP_MST",
            "CONFIG_DSI_PARSER",
            "CONFIG_DRM_SDE_WB",
            "CONFIG_DRM_SDE_RSC",
            "CONFIG_DRM_MSM_REGISTER_LOGGING",
            "CONFIG_QCOM_MDSS_PLL",
            "CONFIG_HDCP_QSEECOM",
            "CONFIG_DRM_SDE_VM",
            "CONFIG_QCOM_WCD939X_I2C",
            "CONFIG_THERMAL_OF",
            "CONFIG_QCOM_SPEC_SYNC",
            "CONFIG_MSM_EXT_DISPLAY",
            "CONFIG_DEBUG_FS",
        ],
        lunch_target = lt,
)

def define_neo_la(t, v, lt=None):
    define_target_variant_modules(
        target = t,
        variant = v,
        registry = display_driver_modules,
        modules = [
            "msm_drm",
        ],
         config_options = [
            "CONFIG_DRM_MSM",
            "CONFIG_DRM_MSM_SDE",
            "CONFIG_SYNC_FILE",
            "CONFIG_DRM_MSM_DSI",
            "CONFIG_DSI_PARSER",
            "CONFIG_QCOM_MDSS_PLL",
            "CONFIG_DRM_SDE_RSC",
            "CONFIG_DRM_SDE_WB",
            "CONFIG_DRM_MSM_REGISTER_LOGGING",
            "CONFIG_DISPLAY_BUILD",
            "CONFIG_THERMAL_OF",
            "CONFIG_DEBUG_FS",
        ],
        lunch_target = lt,
)

def define_display_target():
    for (t, v) in get_all_la_variants() + get_all_le_variants() + get_all_lxc_variants():
        if t == "blair":
            define_blair(t, v)
        if t == "pitti":
            define_pitti(t, v)
        if t == "pineapple":
            define_pineapple(t, v)
        if t == "neo-la":
            define_neo_la(t, v)

    for (lt, t, v) in get_all_lunch_target_base_target_variants():
        if lt == "volcano":
            define_volcano(t, v, lt)
