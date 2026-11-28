# `soc-repo` & Kleaf Build-Glue Patches for Pure GKI 2.0 Linux `6.18` (`houji` / SM8650 `pineapple` v2)

## Upstream Baseline (Pure GKI 2.0)
- `kp6/common`: `f2cb2a697d859f06dfce050650b418e904f98ff7` (`android17-6.18`, **0 modifications / clean git working tree**)
- `kp6/soc-repo` base HEAD: `d08fc445800a9ed9466ffed45620a3e69cb43e36` (`kernel.lnx.6.12.r69-rel`, `https://git.codelinaro.org/clo/la/kernel/qcom.git`)
- `kp6/build/kernel` base HEAD: `2bebe7c443d0e6d5d549fb2a2377dce980d1b15e` (`https://android.googlesource.com/kernel/build`)
- `kp6/build/bazel_common_rules` base HEAD: `17ed69d097b3ea08d86ee93d3c898f9b9cc304b4` (`https://git.codelinaro.org/clo/la/kernel_platform/build/bazel_common_rules.git`)
- `kp6/qcom/opensource/devicetree` base HEAD: `0e8fa33bcdcc121faa9b1160673031a1b586a5f3` (`https://git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/devicetree.git`)
- Target release / vermagic: `6.18.39-android17-6-gf2cb2a697d85-4k SMP preempt mod_unload modversions aarch64`

## Artifacts in This Directory
- `0001-houji-sm8650-soc-repo-purified-6.18.patch`: Unified diff covering all modified/deleted tracked `soc-repo` files for Linux `6.18` KMI/C-API compatibility and Ring-0 purification, including:
  - Linux `6.13..6.18` driver API migrations (`MODULE_IMPORT_NS("...")`, `platform_driver.remove`, `hrtimer_setup`, `timer_delete_sync`, `timer_container_of`, `sched-walt` `6.18` vendor hooks & `scx_enabled`).
  - Strict DMA SMMU IOTLB invalidation fix in `drivers/iommu/qcom-io-pgtable-arm.c` (`qcom_arm_lpae_unmap_pages` populates `iotlb_gather` via `iommu_iotlb_gather_add_range` for `6.18` `iommu_iotlb_sync()`).
  - Build-time `genrule` patching of `qcom-pm8008` in `BUILD.bazel` so `kp6/common` stays at `0` modifications.
  - SM8650 `pineapple` Kleaf platform enablement, purified `modules.list.msm.pineapple`, and zero-overhead header stubs (`icc-debug.h`, `debug_symbol.h` without `kallsyms_lookup_name`).
- `0002-kleaf-ddk-disable-global-stack-protector.patch`: Appends `-fno-stack-protector` to Kleaf `ddk_module` copts in `build/kernel` so out-of-tree modules do not emit unexported `__stack_chk_guard` references against ARM64 `sp_el0` GKI `vmlinux`.
- `0003-bazel-common-rules-rules-python-py-binary.patch`: Updates `copy_to_dist_dir` in `build/bazel_common_rules` to load `py_binary` from `@rules_python//python:defs.bzl` for Bazel 8 compatibility.
- `0004-qcom-devicetree-pineapple-pmic-spmi-tz.patch`: Updates `qcom/pineapple-pmic-overlay.dtsi` in `qcom/opensource/devicetree` for Linux `6.18` `&spmi_bus` / `&thermal_zones` DTB compilation in `//soc-repo:pineapple_perf_dist`.
- `untracked/`: Added `pineapple` / Xiaomi support files in `soc-repo`:
  - `configs/pineapple_perf.bzl` (37 debug/trace/test/unused-PHY Kconfigs disabled = `"n"`), `configs/pineapple_consolidate.bzl`, `no_autofdo_defconfig`
  - `kleaf-scripts/targets/pineapple.bzl`
  - `drivers/mfd/qcom_pm8008_fix.diff`, `drivers/regulator/qcom_pm8008_regulator_fix.diff`
  - `drivers/soc/qcom/gh_cpusys_vm_mem_access.c`
  - `drivers/soc/qcom/system_health_monitor.c`, `system_health_monitor_v01.c`, `system_health_monitor_v01.h`
  - `drivers/soc/qcom/tmecom/tmecom.c`, `tmecom.h`
  - `drivers/thermal/qcom/gpu_dump_skip_cdev.c`
  - `include/linux/mi_display_pwrkey.h`, `include/soc/qcom/sys_pm_vx.h`, `include/soc/qcom/system_health_monitor.h`
- `SHA256SUMS`: Integrity checksums across all patches, README, and untracked files.
