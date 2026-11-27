# `soc-repo` (`kernel.lnx.6.12.r69-rel`) Exported Patches & Platform Additions for Xiaomi 14 (`houji` / SM8650 `pineapple` v2)

## Upstream Baseline (Pure GKI 2.0)
- `kp5/kernel_platform/common`: `8fc057fab7569433e990d11a2362ccb84f7b301d` (`android16-6.12`, **0 modifications / clean git working tree**)
- `kp5/kernel_platform/soc-repo` base HEAD: `d08fc445800a9ed9466ffed45620a3e69cb43e36` (`kernel.lnx.6.12.r69-rel`)
- Target release / vermagic: `6.12.81-android16-6-g8fc057fab756-4k SMP preempt mod_unload modversions aarch64`

## Artifacts in This Directory
- `0001-houji-sm8650-soc-repo-purified-6.12.patch`: Unified `git diff HEAD` covering all 41 tracked `soc-repo` files, including:
  - Minimal orthogonal Ring-0 fixes (`qcom_ubwcp_heap.c`, `qcom_scm.c`, `qtee_shmbridge.c`, `pm8941-pwrkey.c`, `qcom_common.c`, `qcom_q6v5_pas.c`, `panel_event_notifier.h`).
  - SM8650 `pineapple` Kleaf platform enablement & purified `modules.list.msm.pineapple`.
  - Option 1B zero-regression header stubs (`drivers/interconnect/qcom/icc-debug.h`, `drivers/soc/qcom/debug_symbol.h` without `kallsyms_lookup_name` or literal null-pointer subtraction).
- `untracked/`: Added `pineapple` / Xiaomi support files in `soc-repo`:
  - `configs/pineapple_perf.bzl` (37 debug/trace/test/unused-PHY Kconfigs disabled = `"n"`), `configs/pineapple_consolidate.bzl`
  - `kleaf-scripts/targets/pineapple.bzl`
  - `drivers/soc/qcom/gh_cpusys_vm_mem_access.c`
  - `drivers/soc/qcom/system_health_monitor.c`, `system_health_monitor_v01.c`, `system_health_monitor_v01.h`
  - `drivers/soc/qcom/tmecom/tmecom.c`, `tmecom.h`
  - `drivers/thermal/qcom/gpu_dump_skip_cdev.c`
- `SHA256SUMS`: Integrity checksums across patch and untracked files.
