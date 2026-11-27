# SM8650-houji

Full-stack pure **GKI 2.0** (`6.12` & `6.1` LTS) kernel bringup, out-of-tree DDK techpacks, adaptation patches, release auditor, and rootless BYOF installer for **YAAP 17 (Android 17, `seventeen`)** on **Snapdragon 8 Gen 3 (`SM8650` / `pineapple`, Xiaomi 14 `houji` / `houjiin`)**.

## Branches

| Branch | Kernel Baseline | Vendor Modules & DT | Status |
| :--- | :--- | :--- | :--- |
| **[`6.12`](../../tree/6.12)** *(Default)* | Pure **GKI 2.0 Linux `6.12.81`** (`android16-6.12`, **`0` patches to `kernel/common`**) | **Full out-of-tree DDK sources** (`322` purified `.ko` + `54` `system_dlkm`, single-pass `6.12` DT/DTBO, 5-gate + QEMU `kCFI`) | Active mainline |
| **[`6.1`](../../tree/6.1)** | `android14-6.1` LTS (`6.1.182`, single DRM CWB revert) | Prebuilt `OS3.0.307.0` stock vendor DLKM (`654` `.ko`, `73` Ring-0 modules blocklisted) | Maintained LTS |

> **Note:** Personal engineering repository shared **as-is** (`rootless`, FOSS-first). **Never relock your bootloader** when running custom-signed builds on Xiaomi hardware.

## Highlights (`6.12` Pure GKI 2.0 Branch)

- **Zero Modifications to Upstream Google `kernel/common`**:
  - Runs strictly against unmodified Google GKI 2.0 `android16-6.12` (`8fc057fab7569433e990d11a2362ccb84f7b301d`, `6.12.81-android16-6-g8fc057fab756-4k`, `KMI_GENERATION=6`, `CFI_CLANG` strict + `FORTIFY_SOURCE` + `UBSAN_BOUNDS`).
  - Minimal orthogonal SoC platform enablement and header stubs (`icc-debug.h`, `debug_symbol.h` without `kallsyms_lookup_name`) live cleanly under `kernel/patches-soc-repo/`.
- **Purified Full Out-of-Tree Source Stack (`kernel/`)**:
  - **10 Multimedia & DSP Techpacks** (`kernel/techpacks-mm/src/`: `camera-kernel`, `display-drivers`, `dsp-kernel`, `eva-kernel`, `graphics-kernel`, `mm-drivers`, `mmrm-driver`, `mm-sys-kernel`, `synx-kernel`, `video-driver`).
  - **8 Connectivity, Audio & Security Techpacks** (`kernel/techpacks-conn/src/`: `audio-kernel`, `bt-kernel`, `dataipa`, `datarmnet`, `datarmnet-ext`, `securemsm-kernel`, `spu-kernel`, `wlan`).
  - **12 Xiaomi `houji` BSP Modules** (`kernel/xiaomi-modules/src/`: `xiaomi_touch`, `synaptics_tcm2`, `goodix_fod`, `qti_battery_charger`, `qcom-hv-haptics`, `mi_thermal_interface`, `nxp-nci`, `ir-spi`, `dump_display`, `hwid`, `xiaomi_wifi_gpio`, `mi_md_kmsg`) decoupled from `miev`.
  - **Ring-0 Purification**: 37 debug/tracing/test Kconfigs (`CORESIGHT*`, `STM*`, `QCOM_DEBUG_SYMBOL`, `QCOM_EUD`, `ICC_DEBUG`, `LOGBUF_*`, etc.) disabled at build time; unused VM/PC `system_dlkm` drivers pruned (`54` kept: `40` boot-loaded + `14` hotplug-only OTG; `356` loaded runtime modules with `tipc`, `tipc_diag`, and `hci_uart` retained).
- **Single-Pass `6.12` Device Tree & Hardware Parity (`kernel/devicetree-houji/`)**:
  - Single-pass `cpp + dtc -@ + fdtoverlaymerge + ufdt_apply_overlay` compiler (`build_dt.py`) and binary FDT parity auditor (`audit_dt.py`).
  - Hardware validated on-device under `SELinux = Enforcing`: 120Hz LTPO TCL CSOT N3 OLED (`mi,panel-id` + CWB under-display ambient light + 0 ESD `HandlePanelDead` resets), Wi-Fi 7 (`kiwi_v2`), Bluetooth (`WCN785x`), Dual-SIM 5G (`MPSS` + `TIPC`), 9 camera devices (dynamically resolved `gpiolib` shared camera GPIOs), dual `CS35L41` + `WCD939x` 24-bit audio, EVA (`evass.mbn`), USB3/Type-C (`ssusb@a600000` decoupled from disabled `eud`), NFC, IR, FOD, RichTap haptics, ThermalHAL AIDL 3, 90W fast charge + 80% PMIC bypass charging (`night_charging`) + 10W PowerShare, and Gunyah EL2 `md_kmsg`.
- **5-Gate Offline + ARM64 QEMU `kCFI` Verification (`kernel/assemble-images-6.12.py`)**:
  - Automated topological `depmod`/`softdep` stage-1/recovery/stage-2 closure, EROFS (`lz4hc,9`) dynamic DLKM generation, full `avbtool verify_image` RSA-4096 verification, AOSP `checkvintf`, full ELF64 `vermagic` + `24,697`-symbol KMI CRC parity, and live ARM64 `qemu-system-aarch64` `kCFI` smoke module-loading (`kernel/qemu/`).
- **Region-Agnostic BYOF Android 17 ROM & Tooling**:
  - `manifest/houji.xml`, `patches/`, `apply-patches.sh`, `Containerfile`, `verify-preflight.py`, `verify-release.py`, `package-release.py`, and rootless `flash.sh` (`wipe-super` + Interactive Recovery `--firmware` prompt + EROFS OS partitions + FOSS microG + GrapheneOS Vanadium + OpenEUICC).

## Repository Layout

- `kernel/patches-soc-repo/` — Orthogonal `soc-repo` (`kernel.lnx.6.12.r69-rel`) diff and `pineapple` Kleaf platform files.
- `kernel/devicetree-houji/` — 6.12 `pineapple` + `houji` `.dts`/`.dtsi` sources, `build_dt.py`, and `audit_dt.py`.
- `kernel/techpacks-mm/src/` — Full multimedia & DSP out-of-tree DDK techpack sources.
- `kernel/techpacks-conn/src/` — Full connectivity, audio, IPA/RMNet, WLAN, and security out-of-tree DDK techpack sources.
- `kernel/xiaomi-modules/` — Xiaomi 14 BSP module sources (`src/`) and `MODULE_MAP.json`.
- `kernel/build-kernel-6.12.py` — End-to-end KP5 repo sync, `soc-repo` patching, symlink wiring, Kleaf `bazel` build, and ELF64 KMI verifier.
- `kernel/assemble-images-6.12.py` & `kernel/qemu/` — 5-gate rootless image assembler (`boot`, `dtbo`, `vendor_boot`, `recovery`, `system_dlkm`, `vendor_dlkm`, `vbmeta*`) and ARM64 QEMU `kCFI` harness.
- `manifest/`, `patches/`, `apply-patches.sh`, `Containerfile`, `verify-preflight.py`, `verify-release.py`, `package-release.py`, `flash.sh` — YAAP 17 (Android 17) BYOF ROM build, audit, and rootless flash pipeline.

## Reproducing the Build (`6.12`)

### 1. Build the ROM baseline & generate offline signing keys

```bash
podman build -t localhost/houji-aosp-builder:ubuntu24.04 -f Containerfile .
mkdir -p keys yaap kp5 out-6.12 .ccache preflight
# Generate AOSP releasekey, platform, shared, media, networkstack,
# nfc, bluetooth, sdk_sandbox, avb.pem, and APEX key pairs inside ./keys/
```

Sync YAAP 17 sources (`manifest/houji.xml`), hydrate Git LFS, run `verify-preflight.py`, `/workspace/apply-patches.sh`, and build `lunch yaap_houji-cp2a-user && m yaap out/target/product/houji/module-info.json` inside `localhost/houji-aosp-builder:ubuntu24.04` (keeping `kernel_xiaomi_houji` for userspace UAPI headers during the ROM build).

### 2. Sync `KERNEL.PLATFORM.5.0`, build purified GKI 2.0 `6.12.81` + OOT `.ko` & DTBs

```bash
# Sync KP5 (first run only: pass --sync-kp5), verify common/ 0 dirty, apply soc-repo patches,
# compile 322 vendor .ko + 54 GKI system_dlkm + Image, and audit all ELF KMI CRCs:
python3 -B ./kernel/build-kernel-6.12.py --sync-kp5 --kp5-dir ./kp5 --staged-dir ./out-6.12/staged-modules

# Build & verify 6.12 base DTBs + houji DTBO overlays:
python3 -B ./kernel/devicetree-houji/build_dt.py --kp5-dir ./kp5 --yaap-dir ./yaap
python3 -B ./kernel/devicetree-houji/audit_dt.py --yaap-dir ./yaap
```

### 3. Assemble 6.12 kernel/DLKM images & run all 5 verification gates

```bash
podman build -t localhost/houji-qemu-test:latest -f kernel/qemu/Containerfile kernel/qemu
python3 -B ./kernel/assemble-images-6.12.py \
  --mode staged \
  --yaap-dir ./yaap \
  --kp5-dir ./kp5 \
  --staged-dir ./out-6.12/staged-modules \
  --dt-dir ./kernel/devicetree-houji/out \
  --keys-dir ./keys \
  --out-dir ./out-6.12/images
```

Check `./out-6.12/images/ASSEMBLY_VERIFICATION.json` (`status == "PASS"` across AVB chain, VINTF, stage-1 closure, KMI/vermagic, and QEMU ARM64 `kCFI`), then flash the assembled physical/logical images alongside the audited YAAP 17 `dist/` release onto an unlocked device on matching HyperOS 3 (`OS3.0.x`) firmware (`./dist/flash.sh flash` / `sideload`, plus updating `boot`, `dtbo`, `vendor_boot`, `recovery`, `system_dlkm`, `vendor_dlkm`, `vbmeta*` from `./out-6.12/images/`).

> **Linux Host Tip (ASMedia ASM4242 USB4 controllers):** Prefer a native SoC USB-A/USB-C port if ADB disconnects mid-transfer during `adb sideload`.
