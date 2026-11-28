# SM8650-houji

**ROM-agnostic** pure **GKI 2.0** (`6.18`, `6.12`, & `6.1` LTS) kernel bringup, purified out-of-tree DDK techpacks, single-pass Device Tree compiler & parity auditor, and 5-gate image verifier for **Snapdragon 8 Gen 3 (`SM8650` / `pineapple`, Xiaomi 14 `houji` / `houjiin`)**.

> **Decoupled from Userspace ROM:** This repository contains **zero ROM patches or AOSP manifests** and is consumable by **any** Android 17 ROM (YAAP, LineageOS, AOSP, etc.) via `--rom-dir` / `--target-files`. For the reference **YAAP 17** BYOF ROM adaptation, auditor, and installer, see **[`ninthcj/yaap-houji`](https://github.com/ninthcj/yaap-houji)**.

## Branches

| Branch | Kernel Baseline | Vendor Modules & DT | Status |
| :--- | :--- | :--- | :--- |
| **[`6.18`](../../tree/6.18)** *(Default)* | Pure **GKI 2.0 Linux `6.18.39`** (`android17-6.18`, **`0` patches to `kernel/common`**) | **Full out-of-tree DDK sources** (`320` purified `.ko` + `49` `system_dlkm`, `sched-walt.ko`, single-pass `6.18` DT/DTBO, 5-gate + QEMU `kCFI`) | Active mainline |
| **[`6.12`](../../tree/6.12)** | Pure **GKI 2.0 Linux `6.12.81`** (`android16-6.12`, **`0` patches to `kernel/common`**) | Full out-of-tree DDK sources (`322` purified `.ko` + `54` `system_dlkm`, single-pass `6.12` DT/DTBO, 5-gate + QEMU `kCFI`) | Verified stable |
| **[`6.1`](../../tree/6.1)** | `android14-6.1` LTS (`6.1.182`, single DRM CWB revert) | Prebuilt `OS3.0.307.0` stock vendor DLKM (`654` `.ko`, `73` Ring-0 modules blocklisted) | Maintained LTS |

> **Note:** Personal engineering repository shared **as-is** (`rootless`, FOSS-first). **Never relock your bootloader** when running custom-signed builds on Xiaomi hardware.

## Highlights (`6.18` Pure GKI 2.0 Branch)

- **Zero Modifications to Upstream Google `kernel/common`**:
  - Runs strictly against unmodified Google GKI 2.0 `android17-6.18` (`f2cb2a697d859f06dfce050650b418e904f98ff7`, `6.18.39-android17-6-gf2cb2a697d85-4k`, `KMI_GENERATION=6`, `CLANG_VERSION=r596125`, `RUSTC_VERSION=1.91.1.p4`, strict `CONFIG_CFI=y` + `FORTIFY_SOURCE` + `UBSAN_BOUNDS`).
  - Orthogonal SoC platform enablement, Linux `6.13..6.18` driver API migrations, `sched-walt` vendor-hook updates, Kleaf `genrule` patching of `qcom-pm8008`, and the `qcom_arm_lpae_unmap_pages()` `iommu_iotlb_gather_add_range()` SMMU IOTLB invalidation fix live cleanly under `patches-soc-repo/`.
- **Purified Full Out-of-Tree Source Stack**:
  - **10 Multimedia & DSP Techpacks** (`techpacks-mm/src/`: `camera-kernel`, `display-drivers`, `dsp-kernel`, `eva-kernel`, `graphics-kernel`, `mm-drivers`, `mmrm-driver`, `mm-sys-kernel`, `synx-kernel`, `video-driver`).
  - **8 Connectivity, Audio & Security Techpacks** (`techpacks-conn/src/`: `audio-kernel`, `bt-kernel`, `dataipa`, `datarmnet`, `datarmnet-ext`, `securemsm-kernel`, `spu-kernel`, `wlan`).
  - **12 Xiaomi `houji` BSP Modules** (`xiaomi-modules/src/`: `xiaomi_touch`, `synaptics_tcm2`, `goodix_fod`, `qti_battery_charger`, `qcom-hv-haptics`, `mi_thermal_interface`, `nxp-nci`, `ir-spi`, `dump_display`, `hwid`, `xiaomi_wifi_gpio`, `mi_md_kmsg`) decoupled from `miev`.
  - **Ring-0 Purification**: 37 debug/tracing/test Kconfigs (`CORESIGHT*`, `STM*`, `QCOM_DEBUG_SYMBOL`, `QCOM_EUD`, `ICC_DEBUG`, `LOGBUF_*`, etc.) disabled at build time; unused VM/PC/KUnit/CoreSight/Thunderbolt `system_dlkm` drivers pruned (`49` kept: `34` boot-loaded + `15` hotplug-only OTG/`rust_binder`; `348` boot-loaded runtime modules with `sched_walt`, `tipc`, `tipc_diag`, and `hci_uart` retained).
- **Single-Pass `6.18` Device Tree & Hardware Parity (`devicetree-houji/`)**:
  - Single-pass `cpp + dtc -@ + fdtoverlaymerge + ufdt_apply_overlay` compiler (`build_dt.py`) and binary FDT parity auditor (`audit_dt.py`).
  - Hardware validated on-device under `SELinux = Enforcing`: 120Hz LTPO TCL CSOT N3 OLED (`mi,panel-id` + CWB under-display ambient light + 0 ESD `HandlePanelDead` resets), all 4 CPU clusters governed by `walt`, Wi-Fi 7 (`kiwi_v2`), Bluetooth (`WCN785x`), Dual-SIM 5G (`MPSS` + `TIPC`), 9 camera devices (dynamically resolved `gpiolib` shared camera GPIOs), dual `CS35L41` + `WCD939x` SoundWire audio, EVA (`evass.mbn`), native wired USB3/ADB & Type-C UCSI (`ssusb@a600000` decoupled from disabled `eud` with strict SMMU IOTLB invalidation), NFC, IR, FOD, RichTap haptics, ThermalHAL AIDL, 90W fast charge + 80% PMIC bypass charging (`night_charging`) + 10W PowerShare, and Gunyah EL2 `md_kmsg` (`boot_guard_sec=0` in release builds).
- **5-Gate Offline + Protected-Symbol + ARM64 QEMU `kCFI` Verification (`assemble-images-6.18.py`)**:
  - Automated topological `depmod`/`softdep` stage-1/recovery/stage-2 closure, EROFS (`lz4hc,9`) dynamic DLKM generation, full `avbtool verify_image` RSA-4096 verification, AOSP `checkvintf`, full ELF64 `vermagic` + `24,589`-symbol KMI CRC & `abi_symbollist` protected-symbol parity, and live ARM64 `qemu-system-aarch64` `kCFI` smoke module-loading (`qemu/`).

## Repository Layout

- `patches-soc-repo/` — Orthogonal `soc-repo` (`kernel.lnx.6.12.r69-rel` -> `6.18`) diff and `pineapple` Kleaf platform files.
- `devicetree-houji/` — 6.18 `pineapple` + `houji` `.dts`/`.dtsi` sources, `build_dt.py`, and `audit_dt.py`.
- `techpacks-mm/src/` — Full multimedia & DSP out-of-tree DDK techpack sources.
- `techpacks-conn/src/` — Full connectivity, audio, IPA/RMNet, WLAN, and security out-of-tree DDK techpack sources.
- `xiaomi-modules/` — Xiaomi 14 BSP module sources (`src/`) and `MODULE_MAP.json`.
- `build-kernel-6.18.py` — End-to-end AOSP `common-android17-6.18` + `soc-repo` sync, patching, symlink wiring, Kleaf `bazel` build, and ELF64 KMI / protected-symbol verifier.
- `assemble-images-6.18.py` & `qemu/` — 5-gate ROM-agnostic rootless image assembler (`boot`, `dtbo`, `vendor_boot`, `recovery`, `system_dlkm`, `vendor_dlkm`, `vbmeta*`) and ARM64 QEMU `kCFI` harness.

## Reproducing the Build (`6.18`)

Recommended sibling workspace layout:

```text
houji/
├── SM8650-houji/   # This kernel repo (6.18, 6.12, or 6.1)
├── yaap-houji/     # Companion ROM repo (or any synced Android 17 ROM tree)
└── keys/           # Shared offline AVB/signing keys (never committed)
```

### 1. Sync `common-android17-6.18` + `soc-repo`, build purified GKI 2.0 `6.18.39` + OOT `.ko` & DTBs

```bash
# Sync AOSP common-android17-6.18 + Qualcomm soc-repo (first run only: pass --sync-kp6),
# verify common/ 0 dirty, apply soc-repo patches, compile 320 vendor .ko + GKI system_dlkm + Image,
# and audit all ELF KMI CRCs & protected ABI symbols:
python3 -B ./build-kernel-6.18.py --sync-kp6 --kp6-dir ./kp6 --staged-dir ./out-6.18/staged-modules

# Build & verify 6.18 base DTBs + houji DTBO overlays (points --rom-dir at any ROM tree with host tools/stock DT):
python3 -B ./devicetree-houji/build_dt.py --kp6-dir ./kp6 --rom-dir ../yaap-houji/yaap
python3 -B ./devicetree-houji/audit_dt.py --rom-dir ../yaap-houji/yaap --kp6-dir ./kp6
```

### 2. Assemble 6.18 kernel/DLKM images for ANY Android 17 ROM & run all 5 gates

```bash
podman build -t localhost/houji-qemu-test:latest -f qemu/Containerfile qemu
python3 -B ./assemble-images-6.18.py \
  --mode staged \
  --rom-dir ../yaap-houji/yaap \
  --kp6-dir ./kp6 \
  --staged-dir ./out-6.18/staged-modules \
  --dt-dir ./devicetree-houji/out \
  --keys-dir ../keys \
  --out-dir ./out-6.18/images
```

Check `./out-6.18/images/ASSEMBLY_VERIFICATION.json` (`status == "PASS"` across AVB chain, VINTF, stage-1 closure, KMI/vermagic/protected-ABI, and QEMU ARM64 `kCFI`). `--fingerprint` is auto-detected from `<target_files>/{VENDOR,SYSTEM}/build.prop` unless overridden, and `--target-files` / `--template-dir` accept outputs from non-YAAP ROM builds as well. Flash the generated physical/logical images alongside your ROM release onto an unlocked device on matching HyperOS 3 (`OS3.0.x`) firmware.
