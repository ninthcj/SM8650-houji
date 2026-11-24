# yaap-houji

Reproducible build configuration, adaptation patches, release auditor, and rootless BYOF installer for **YAAP 17 (Android 17, `seventeen`)** on **Xiaomi 14 (`houji` / `houjiin`)**.

> **Note:** Personal build configuration shared **as-is** in case it helps other Xiaomi 14 owners or builders. No support, OTA hosting, or update schedule is promised. **Never relock your bootloader** when running custom-signed builds on Xiaomi hardware.

## Highlights

- **Region-Agnostic Bring-Your-Own-Firmware (BYOF)**: Ships strictly the **14 OS partitions** (`boot`, `dtbo`, `init_boot`, `odm`, `product`, `recovery`, `system`, `system_dlkm`, `system_ext`, `vbmeta`, `vbmeta_system`, `vendor`, `vendor_boot`, `vendor_dlkm`) and **zero radio/firmware partitions**. Works across CN (`23127PN0CC`) and Global/EEA/IN (`23127PN0CG`, `houji` / `houjiin`) on a **HyperOS 3 (`OS3.0.x`, Android 16 vendor)** firmware baseline.
- **FOSS-First & Native microG**: Uses YAAP's native Vanilla `vendor/microg` integration (preserving upstream microG APK signatures for restricted signature spoofing and pre-extracting `GmsCore` ARM64 JNI libraries), pinned upstream GrapheneOS `Vanadium 158.0.8127.92.0` browser/WebView, `Aperture` multi-lens camera, `Seedvault` backup, and `OpenEUICC` + `XiaomiEsimSwitcher` (auto-enabled on Global SKU, masked on CN SKU). Excludes `vendor/google/gms`, `vendor/google/pixel`, and bundled apps embedding proprietary Play/Firebase SDKs (`YASR`, `TalkBack`, `ImsServiceEntitlement`).
- **Updated Official Google GKI 2.0 LTS Kernel**: Stages Google's official signed `android14-6.1-2026-09_r10` (`6.1.176-android14-11-g6e799b788f49-ab16965211`) `Image` and matched 60 signed `system_dlkm` modules via `fetch-gki-kernel.py`, retaining stock `OS3.0.307.0` DTBs, DTBO, and 654 vendor `.ko` modules with **100% ELF KMI symbol CRC verification** (`39,139 / 39,139` refs, `0` mismatches, `0` missing).
- **Recovery & First-Flash Tooling**:
  - `hardware/qcom-caf/bootctrl` (`android.hardware.boot-service.qti` + `.recovery`) enabled in both Android and Recovery, `vendor/codeaurora/telephony` aligned to `lineage-23.2` (`LA.QSSI.16.0`) to match HyperOS 3 IMS/telephony blobs, and `/sys/class/qcom-battery` + UCSI Type-C SELinux labels wired for Xiaomi 90W private fast-charge authentication (`batterysecret`).
  - Interactive physical confirmation menu (`No / Yes`, default `No`) on unlocked devices in YAAP Recovery when sideloading user-supplied unsigned firmware archives (`--firmware`).
  - Rootless `flash.sh` installer including `super_empty.img` (`fastboot wipe-super`) to cleanly reset Xiaomi HyperOS `mi_ext` / unmerged `*-cow` entries in `super` prior to first-time Recovery sideloading.
  - 112-check offline release verifier (`verify-release.py`) and atomic publisher (`package-release.py`), including static DEX bytecode linkage verification for QTI IMS/ExtPhone and full ELF KMI symbol CRC auditing.

## Repository Layout

- `manifest/houji.xml` — `.repo/local_manifests/houji.xml` pinning device, vendor, hardware, kernel UAPI, QTI telephony, and YAAP project revisions.
- `patches/` & `apply-patches.sh` — Clean `git format-patch` series applied onto branch `yaap-17` across the 8 adapted repositories.
- `fetch-gki-kernel.py` — Downloads and KMI-verifies the pinned official Google `android14-6.1-2026-09_r10` (`6.1.176`, `ab16965211`) GKI `Image` + `system_dlkm` modules into `yaap/kernel/prebuilts/houji-gki/`.
- `Containerfile` — Rootless Podman Ubuntu 24.04 AOSP build environment.
- `verify-preflight.py` — Pre-patch source, Git LFS hydration, kernel module ELF, and upstream APK signature gate.
- `verify-release.py` & `package-release.py` — Post-build 112-check release auditor (OTA/AVB/APEX signatures, VINTF, SELinux, JNI extraction, QTI IMS/ExtPhone DEX linkage, 39,139-symbol KMI CRC parity, GMS-free scan) and `dist/` bundler.
- `flash.sh` — Interactive rootless installer (`verify`, `flash`, `sideload [--firmware ZIP]`, `status`).

## Reproducing the Build

### 1. Build the rootless container & generate your own signing keys

Generate your own 4096-bit Android release, AVB, and APEX signing keys into `./keys/` (keep `./keys/` strictly private and offline; never commit them):

```bash
podman build -t localhost/houji-aosp-builder:ubuntu24.04 -f Containerfile .
mkdir -p keys yaap .ccache preflight
# Generate standard AOSP releasekey, platform, shared, media, networkstack,
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
    python3 -B /workspace/fetch-gki-kernel.py /workspace/yaap
  '
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

### 4. Audit & package `dist/`

Download official Linux `platform-tools_r37.0.3-linux.zip` into `preflight/`, then run:

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
```

## Flashing (`dist/`)

1. Back up all data. Ensure bootloader is unlocked and the device is on matching regional **HyperOS 3 (`OS3.0.x`)** firmware (or supply a regional firmware archive via `--firmware`).
2. Run `./dist/flash.sh flash` (writes the 7 active-slot boot-chain images and resets `super` via `wipe-super` with `images/super_empty.img`), then reboot to YAAP Recovery.
3. In Recovery: **Factory reset -> Format data / factory reset**, then **Apply update -> Apply from ADB**.
4. Run `./dist/flash.sh sideload` (or `./dist/flash.sh sideload --firmware /path/to/fw_houji_*.zip`). Once Recovery reports `Install from ADB completed with status 0`, tap **Reboot system now**.

> **Linux Host Tip (ASMedia ASM4242 USB4 controllers):** On certain Linux laptops with external ASM4242 USB4 controllers, `libusb` 1 MiB ADB bulk URBs can exceed the kernel's 256 KiB `swiotlb` segment limit during `adb sideload`. Prefer a native SoC USB-A/USB-C port if ADB disconnects mid-transfer.
