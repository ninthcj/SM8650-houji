# SM8650-houji

Full-stack pure **GKI 2.0** (`6.12` & `6.1` LTS) kernel bringup, out-of-tree DDK techpacks, adaptation patches, release auditor, and rootless BYOF installer for **YAAP 17 (Android 17, `seventeen`)** on **Snapdragon 8 Gen 3 (`SM8650` / `pineapple`, Xiaomi 14 `houji` / `houjiin`)**.

## Branches

| Branch | Kernel Baseline | Vendor Modules & DT | Status |
| :--- | :--- | :--- | :--- |
| **[`6.12`](../../tree/6.12)** *(Default)* | Pure **GKI 2.0 Linux `6.12.81`** (`android16-6.12`, **`0` patches to `kernel/common`**) | **Full out-of-tree DDK sources** (322 purified `.ko` + 54 `system_dlkm`, single-pass `6.12` DT/DTBO, 5-gate + QEMU `kCFI`) | Active mainline |
| **[`6.1`](../../tree/6.1)** *(This branch)* | **`android14-6.1` LTS (`6.1.182`)** (single DRM CWB clone-check revert) | Prebuilt `OS3.0.307.0` stock vendor DLKM (654 `.ko`, 73 Ring-0 modules blocklisted, 39,140 KMI CRCs verified) | Maintained LTS |

> **Note:** Personal engineering repository shared **as-is** (`rootless`, FOSS-first). **Never relock your bootloader** when running custom-signed builds on Xiaomi hardware.

## Highlights (`6.1` LTS Branch)

- **Region-Agnostic Bring-Your-Own-Firmware (BYOF)**: Ships strictly the **14 OS partitions** (`boot`, `dtbo`, `init_boot`, `odm`, `product`, `recovery`, `system`, `system_dlkm`, `system_ext`, `vbmeta`, `vbmeta_system`, `vendor`, `vendor_boot`, `vendor_dlkm`) and **zero radio/firmware partitions**. Works across CN (`23127PN0CC`) and Global/EEA/IN (`23127PN0CG`, `houji` / `houjiin`) on a **HyperOS 3 (`OS3.0.x`)** firmware baseline.
- **FOSS-First & Native microG**: Uses YAAP's native Vanilla `vendor/microg` integration, pinned upstream GrapheneOS `Vanadium 158.0.8127.92.0`, `Aperture` multi-lens camera, `Seedvault` backup, and `OpenEUICC` + `XiaomiEsimSwitcher` (auto-enabled on Global SKU, masked on CN SKU). Excludes `vendor/google/gms`, `vendor/google/pixel`, and bundled proprietary Play/Firebase apps (`YASR`, `TalkBack`, `ImsServiceEntitlement`).
- **Updated & Purified `android14-6.1` GKI 2.0 LTS Kernel (`6.1.182`)**: Built reproducibly from pinned Google `kernel/common` `android14-6.1` (`7bae370c0f35c4188943c4d8a4d5a357d157767f`, `6.1.182`) with official Android `clang-r487747c` via `kernel/build-kernel-6.1.py`, applying a single surgical revert (`kernel/patches/0001-Revert-drm-Add-valid-clones-check.patch`) of upstream Linux `ffb55ddf2685` (`drm: Add valid clones check`, `6.1.142+`) so Qualcomm's prebuilt `6.1.68` `msm_drm.ko` CWB under-display RGB ambient light sensor runs cleanly without `-EINVAL` flicker. Retains stock `OS3.0.307.0` DTBs, DTBO, and 654 signed vendor `.ko` modules with **100% ELF KMI symbol CRC verification** (`39,140 / 39,140` refs, `0` mismatches, `0` missing), while blocklisting and pruning 73 foreign-SoC, absent-IC, unused socket/hypervisor (`gzvm`, `kheaders`, `can*`, `ieee802154*`, `6lowpan`), and proprietary HyperOS Ring-0 hook modules (`496 -> 423` loaded modules, retaining `tipc` + `tipc/diag` for SM8650 `nicmd`/`tlpd`).
- **ROM, Recovery & Tooling**:
  - **EROFS (`lz4hc,9` + block deduplication)** across all 7 read-only dynamic partitions (`system`, `system_ext`, `product`, `vendor`, `odm`, `system_dlkm`, `vendor_dlkm`) and pruning of orphaned payment/SLA daemons.
  - `hardware/qcom-caf/bootctrl`, `vendor/codeaurora/telephony` (`LA.QSSI.16.0`), `/odm/lib64/libcamxexternalformatutils.so` SP-HAL labeling, `atfwd` `lseek` seccomp, real-time Gaussian blur, 10W `Wireless PowerShare`, upgraded `android.hardware.health-service.xiaomi` (`IHealth` V5) with 80% PMIC bypass charging + SOH, and Xiaomi 90W fast-charge SELinux wiring.
  - Interactive physical confirmation on unlocked devices in Recovery (`--firmware`), rootless `flash.sh` with `super_empty.img` (`wipe-super`), 119-check `verify-release.py`, and atomic `package-release.py`.

## Repository Layout

- `manifest/houji.xml` — `.repo/local_manifests/houji.xml` pinning device, vendor, hardware, kernel UAPI, QTI telephony, Vanadium, and YAAP revisions.
- `patches/` & `apply-patches.sh` — Clean `git format-patch` series applied onto branch `yaap-17` across the 8 adapted ROM repositories.
- `kernel/build-kernel-6.1.py` & `kernel/patches/` — Fetches pinned Google `android14-6.1` (`6.1.182`) + `clang-r487747c`, applies `0001-Revert-drm-Add-valid-clones-check.patch`, builds `Image` + 60 signed `system_dlkm` modules (pruning 21 unused hypervisor/socket modules), verifies all 39,140 KMI CRCs, and stages into `yaap/kernel/prebuilts/houji-gki/`.
- `Containerfile` — Rootless Podman Ubuntu 24.04 AOSP build environment.
- `verify-preflight.py`, `verify-release.py`, `package-release.py`, `flash.sh` — Source preflight gate, 119-check release auditor, `dist/` publisher, and interactive rootless installer.

## Reproducing the Build (`6.1`)

### 1. Build the rootless container & generate your own signing keys

```bash
podman build -t localhost/houji-aosp-builder:ubuntu24.04 -f Containerfile .
mkdir -p keys yaap .ccache preflight
# Generate AOSP releasekey, platform, shared, media, networkstack,
# nfc, bluetooth, sdk_sandbox, avb.pem, and APEX key pairs inside ./keys/
```

### 2. Sync sources, hydrate Git LFS, verify inputs, and apply patches

```bash
podman run --rm -it --userns=keep-id --pids-limit=-1 --ulimit nofile=65536:65536 \
  -v "$PWD:/workspace" localhost/houji-aosp-builder:ubuntu24.04 bash -lc '
    cd /workspace/yaap
    repo init -u https://github.com/yaap/manifest.git -b seventeen --git-lfs
    mkdir -p .repo/local_manifests
    cp /workspace/manifest/houji.xml .repo/local_manifests/houji.xml
    repo sync -c -j8 --force-sync --no-clone-bundle --no-tags
    repo forall vendor/xiaomi/houji vendor/xiaomi/sm8650-common vendor/microg \
      -c "git lfs install --local --skip-repo && git lfs pull && git lfs checkout"
    python3 -B /workspace/verify-preflight.py /workspace/yaap > /workspace/preflight/SYNCED_SOURCE_VERIFICATION.json
    /workspace/apply-patches.sh /workspace/yaap
  '
python3 -B ./kernel/build-kernel-6.1.py ./yaap
```

### 3. Build signed `user` OTA (`release-keys`)

```bash
podman run --rm -it --userns=keep-id --pids-limit=-1 --ulimit nofile=65536:65536 \
  -v "$PWD:/workspace" \
  -v "$PWD/.ccache:/ccache" \
  -v "$PWD/keys:/workspace/yaap/vendor/yaap/signing/keys:ro" \
  localhost/houji-aosp-builder:ubuntu24.04 bash -lc '
    cd /workspace/yaap
    repo manifest -r > /workspace/preflight/FINAL_BUILD_MANIFEST.xml
    export USE_CCACHE=1 CCACHE_EXEC=/usr/bin/ccache CCACHE_DIR=/ccache
    source build/envsetup.sh
    lunch yaap_houji-cp2a-user
    m yaap out/target/product/houji/module-info.json
  '
```

### 4. Audit, package `dist/`, and flash

Download official Linux `platform-tools_r37.0.3-linux.zip` into `preflight/`, audit and bundle `dist/`, then verify and install:

```bash
OTA_ZIP=$(ls -1 yaap/out/target/product/houji/YAAP-17-*houji*.zip | tail -n 1)
python3 -B ./verify-release.py \
  --rom-dir yaap \
  --product-out yaap/out/target/product/houji \
  --host-bin yaap/out/host/linux-x86/bin \
  --source-json preflight/SYNCED_SOURCE_VERIFICATION.json \
  --manifest-file preflight/FINAL_BUILD_MANIFEST.xml \
  --ota "$OTA_ZIP" \
  --target-files yaap/out/target/product/houji/obj/PACKAGING/target_files_intermediates/yaap_houji-target_files \
  --ota-cert keys/releasekey.x509.pem \
  --avb-key keys/avb.pem \
  --keys-dir keys \
  --work-dir preflight/release-audit-work \
  --output preflight/FINAL_VERIFICATION.json

python3 -B ./package-release.py \
  --product-out yaap/out/target/product/houji \
  --images-dir preflight/release-audit-work/payload \
  --verification preflight/FINAL_VERIFICATION.json \
  --platform-tools preflight/platform-tools_r37.0.3-linux.zip \
  --flash-script ./flash.sh \
  --dist ./dist

rm -rf preflight/release-audit-work
./dist/flash.sh verify
./dist/flash.sh flash
# In Recovery: Factory reset -> Format data / factory reset -> Apply update -> Apply from ADB
./dist/flash.sh sideload
```

> **Linux Host Tip (ASMedia ASM4242 USB4 controllers):** Prefer a native SoC USB-A/USB-C port if ADB disconnects mid-transfer during `adb sideload`.
