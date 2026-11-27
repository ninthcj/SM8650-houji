# SPDX-License-Identifier: GPL-2.0-only
# Kleaf ddk_module definitions for all 13 Xiaomi 14 (houji, SM8650 pineapple)
# out-of-tree BSP modules targeting strict GKI 2.0 android16-6.12 (KERNEL.PLATFORM.5.0).

load("//build/kernel/kleaf:kernel.bzl", "ddk_module")
load("//build/bazel_common_rules/dist:dist.bzl", "copy_to_dist_dir")

def define_xiaomi_bsp_modules(target = "pineapple", variant = "gki"):
    tv = "{}_{}".format(target, variant)
    kernel_build = "//soc-repo:{}_base_kernel".format(tv)
    soc_headers = ["//soc-repo:all_headers"]

    # 1. hwid.ko
    ddk_module(
        name = "{}_hwid".format(tv),
        out = "hwid.ko",
        srcs = ["hwid/hwid.c", "hwid/hwid.h"],
        includes = ["hwid"],
        deps = soc_headers,
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 2. ir-spi.ko
    ddk_module(
        name = "{}_ir-spi".format(tv),
        out = "ir-spi.ko",
        srcs = ["ir/ir-spi.c"],
        deps = soc_headers,
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 3. dump_display.ko
    ddk_module(
        name = "{}_dump_display".format(tv),
        out = "dump_display.ko",
        srcs = ["dump_display/dump_display.c"],
        deps = soc_headers,
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 4. xiaomi_wifi_gpio.ko
    ddk_module(
        name = "{}_xiaomi_wifi_gpio".format(tv),
        out = "xiaomi_wifi_gpio.ko",
        srcs = ["wifi_gpio/xiaomi_wifi_gpio.c", "hwid/hwid.h"],
        includes = ["hwid"],
        deps = soc_headers + [":{}_hwid".format(tv)],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 5. mi_thermal_interface.ko
    ddk_module(
        name = "{}_mi_thermal_interface".format(tv),
        out = "mi_thermal_interface.ko",
        srcs = ["thermal/mi_thermal_interface.c"],
        copts = [
            "-DCONFIG_MI_THERMAL_MULTI_CHARGE=1",
            "-DCONFIG_QCOM_PANEL_EVENT_NOTIFIER=1",
        ],
        deps = soc_headers + [
            "//soc-repo:{}/drivers/soc/qcom/panel_event_notifier".format(tv),
        ],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 6. goodix_fod.ko
    ddk_module(
        name = "{}_goodix_fod".format(tv),
        out = "goodix_fod.ko",
        srcs = [
            "fingerprint/goodix_fod/gf_spi.c",
            "fingerprint/goodix_fod/gf_spi.h",
            "fingerprint/goodix_fod/platform.c",
            "fingerprint/goodix_fod/netlink.c",
        ],
        includes = ["fingerprint/goodix_fod"],
        copts = ["-DCONFIG_QCOM_PANEL_EVENT_NOTIFIER=1"],
        deps = soc_headers + [
            "//soc-repo:{}/drivers/soc/qcom/panel_event_notifier".format(tv),
        ],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 7. nxp-nci.ko (CLO 6.12 nfc-driver.lnx.16.0.r14-rel for qcom,sn-nci)
    ddk_module(
        name = "{}_nxp-nci".format(tv),
        out = "nxp-nci.ko",
        srcs = [
            "nfc/nxp-nci/nfc/common.c",
            "nfc/nxp-nci/nfc/common.h",
            "nfc/nxp-nci/nfc/common_nxp.c",
            "nfc/nxp-nci/nfc/common_nxp.h",
            "nfc/nxp-nci/nfc/common_qcom.c",
            "nfc/nxp-nci/nfc/ese_cold_reset.c",
            "nfc/nxp-nci/nfc/ese_cold_reset.h",
            "nfc/nxp-nci/nfc/i2c_drv.c",
            "nfc/nxp-nci/nfc/i2c_drv.h",
            "nfc/nxp-nci/config/gki_nfc_conf.h",
            "nfc/nxp-nci/include/uapi/linux/nfc/nfcinfo.h",
            "nfc/nxp-nci/include/uapi/linux/nfc/sn_uapi.h",
        ],
        includes = [
            "nfc/nxp-nci",
            "nfc/nxp-nci/nfc",
            "nfc/nxp-nci/include/uapi/linux/nfc",
        ],
        copts = [
            "-DCONFIG_IPC_LOGGING=1",
            "-include",
            "config/gki_nfc_conf.h",
        ],
        deps = soc_headers + [
            "//soc-repo:{}/drivers/pinctrl/qcom/pinctrl-msm".format(tv),
            "//soc-repo:{}/kernel/trace/qcom_ipc_logging".format(tv),
        ],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 8. xiaomi_touch.ko
    ddk_module(
        name = "{}_xiaomi_touch".format(tv),
        out = "xiaomi_touch.ko",
        srcs = [
            "touch/xiaomi/xiaomi_touch_core.c",
            "touch/xiaomi/xiaomi_touch_device.c",
            "touch/xiaomi/xiaomi_touch_evdev.c",
            "touch/xiaomi/xiaomi_touch_knock_data.c",
            "touch/xiaomi/xiaomi_touch_mode.c",
            "touch/xiaomi/xiaomi_touch_operations.c",
            "touch/xiaomi/xiaomi_touch_proc.c",
            "touch/xiaomi/xiaomi_touch_sys.c",
            "touch/xiaomi/xiaomi_touch.h",
            "touch/xiaomi/xiaomi_touch_type_common.h",
        ],
        includes = ["touch/xiaomi"],
        copts = [
            "-DCONFIG_QCOM_PANEL_EVENT_NOTIFIER=1",
            "-DTOUCH_KNOCK_SUPPORT",
            "-DTOUCH_FOD_SUPPORT",
            "-DTOUCH_THP_SUPPORT",
        ],
        deps = soc_headers + [
            "//soc-repo:{}/drivers/soc/qcom/panel_event_notifier".format(tv),
        ],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 9. synaptics_tcm2.ko
    ddk_module(
        name = "{}_synaptics_tcm2".format(tv),
        out = "synaptics_tcm2.ko",
        srcs = [
            "touch/synaptics_tcm2/syna_tcm2.c",
            "touch/synaptics_tcm2/syna_tcm2.h",
            "touch/synaptics_tcm2/syna_tcm2_cdev.c",
            "touch/synaptics_tcm2/syna_tcm2_cdev.h",
            "touch/synaptics_tcm2/syna_tcm2_platform.h",
            "touch/synaptics_tcm2/syna_tcm2_platform_spi.c",
            "touch/synaptics_tcm2/syna_tcm2_runtime.h",
            "touch/synaptics_tcm2/syna_tcm2_sysfs.c",
            "touch/synaptics_tcm2/syna_tcm2_testing.c",
            "touch/synaptics_tcm2/syna_tcm2_testing.h",
            "touch/synaptics_tcm2/syna_tcm2_testing_limits.h",
            "touch/synaptics_tcm2/syna_xiaomi_driver.c",
            "touch/synaptics_tcm2/syna_xiaomi_driver.h",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_core_dev.h",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_core_v1.c",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_core_v2.c",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_base.c",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_base.h",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_base_flash.h",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_reflash.c",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_reflash.h",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_romboot.c",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_romboot.h",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_touch.c",
            "touch/synaptics_tcm2/tcm/synaptics_touchcom_func_touch.h",
            "touch/xiaomi/xiaomi_touch.h",
            "touch/xiaomi/xiaomi_touch_type_common.h",
        ],
        includes = [
            "touch/synaptics_tcm2",
            "touch/synaptics_tcm2/tcm",
            "touch/xiaomi",
        ],
        copts = [
            "-DCONFIG_TOUCHSCREEN_SYNA_TCM2_REFLASH",
            "-DCONFIG_TOUCHSCREEN_SYNA_TCM2_SYSFS",
            "-DCONFIG_TOUCHSCREEN_SYNA_TCM2_TESTING",
            "-DTOUCH_FOD_SUPPORT",
            "-DTOUCH_THP_SUPPORT",
            "-DTOUCH_KNOCK_SUPPORT",
        ],
        deps = soc_headers + [":{}_xiaomi_touch".format(tv)],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 10. qti_battery_charger.ko (Xiaomi PMIC GLink override for houji)
    ddk_module(
        name = "{}_qti_battery_charger".format(tv),
        out = "qti_battery_charger.ko",
        srcs = [
            "battery/qti_battery_charger_main.c",
            "battery/qti_battery_charger.h",
            "battery/qti_typec_class.c",
            "battery/qti_typec_class.h",
        ],
        includes = ["battery"],
        copts = [
            "-DCONFIG_QTI_BATTERY_CHARGER=1",
            "-DCONFIG_QTI_PMIC_GLINK=1",
            "-DCONFIG_QTI_TYPEC_CLASS=1",
            "-DCONFIG_QCOM_PANEL_EVENT_NOTIFIER=1",
            "-DCONFIG_MI_WIRELESS=1",
            "-DCONFIG_MI_N2N3ANTIBURN=1",
            "-DCONFIG_MI_DTPT=1",
            "-DCONFIG_MI_ENABLE_DP=1",
        ],
        deps = soc_headers + [
            "//soc-repo:{}/drivers/soc/qcom/qti_pmic_glink".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/panel_event_notifier".format(tv),
        ],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 11. qcom-hv-haptics.ko (Xiaomi RichTap + LRA calibration override for houji)
    ddk_module(
        name = "{}_qcom-hv-haptics".format(tv),
        out = "qcom-hv-haptics.ko",
        srcs = [
            "haptics/qcom-hv-haptics/qcom-hv-haptics.c",
            "haptics/qcom-hv-haptics/xm-haptic.h",
        ],
        hdrs = [
            "haptics/qcom-hv-haptics/qcom-hv-haptics-debugfs.c",
        ],
        includes = ["haptics/qcom-hv-haptics"],
        copts = [
            "-DCONFIG_QTI_BATTERY_CHARGER=1",
            "-DRICHTAP_FOR_PMIC_ENABLE=1",
        ],
        deps = soc_headers + [
            ":{}_qti_battery_charger".format(tv),
        ],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # 12. mi_md_kmsg.ko (Xiaomi BlackBox md_kmsg minidump kmsg mirror + Gunyah RM + MINIDUMP 0x20 mode)
    ddk_module(
        name = "{}_mi_md_kmsg".format(tv),
        out = "mi_md_kmsg.ko",
        srcs = ["md_kmsg/mi_md_kmsg.c"],
        copts = [
            "-DCONFIG_GH_RM_DRV=1",
        ],
        deps = soc_headers + [
            "//soc-repo:{}/drivers/soc/qcom/smem".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/minidump".format(tv),
            "//soc-repo:{}/drivers/soc/qcom/qcom_wdt_core".format(tv),
            "//soc-repo:{}/drivers/firmware/qcom/qcom-scm".format(tv),
            "//soc-repo:{}/drivers/virt/gunyah/gh_rm_drv".format(tv),
        ],
        kernel_build = kernel_build,
        visibility = ["//visibility:public"],
    )

    # Note: cs35l41_dlkm.ko and swr_haptics_dlkm.ko are built canonically by
    # //vendor/qcom/opensource/audio-kernel (wired with machine_dlkm & xiaomi qti_battery_charger).

    all_targets = [
        ":{}_hwid".format(tv),
        ":{}_ir-spi".format(tv),
        ":{}_dump_display".format(tv),
        ":{}_xiaomi_wifi_gpio".format(tv),
        ":{}_mi_thermal_interface".format(tv),
        ":{}_goodix_fod".format(tv),
        ":{}_nxp-nci".format(tv),
        ":{}_xiaomi_touch".format(tv),
        ":{}_synaptics_tcm2".format(tv),
        ":{}_qti_battery_charger".format(tv),
        ":{}_qcom-hv-haptics".format(tv),
        ":{}_mi_md_kmsg".format(tv),
    ]

    copy_to_dist_dir(
        name = "{}_xiaomi_bsp_dist".format(tv),
        data = all_targets,
        dist_dir = "out/target/product/houji/dlkm/xiaomi-bsp/",
        flat = True,
        wipe_dist_dir = False,
        allow_duplicate_filenames = False,
        mode_overrides = {"**/*": "644"},
    )
