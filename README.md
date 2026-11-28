# SM8650-houji

**ROM-agnostic** pure **GKI 2.0** (`6.18`, `6.12`, & `6.1` LTS) kernel builders and out-of-tree DDK / DT stack for **Snapdragon 8 Gen 3 (`SM8650` / `pineapple`, Xiaomi 14 `houji` / `houjiin`)**.

> **Decoupled from Userspace ROM:** This repository contains **zero ROM patches or AOSP manifests** and can be paired with **any** compatible Android ROM tree. For the reference **YAAP 17** BYOF ROM adaptation, auditor, and installer, see **[`ninthcj/yaap-houji`](https://github.com/ninthcj/yaap-houji)**.

## Branches

| Branch | Kernel Baseline | Vendor Modules & DT | Status |
| :--- | :--- | :--- | :--- |
| **[`6.18`](../../tree/6.18)** *(Default)* | Pure **GKI 2.0 Linux `6.18.39`** (`android17-6.18`, `0` patches to `kernel/common`) | Full out-of-tree DDK sources (`320` purified `.ko` + `49` `system_dlkm`, `sched-walt.ko`, single-pass `6.18` DT/DTBO, 5-gate + QEMU `kCFI`) | Active mainline |
| **[`6.12`](../../tree/6.12)** | Pure **GKI 2.0 Linux `6.12.81`** (`android16-6.12`, `0` patches to `kernel/common`) | Full out-of-tree DDK sources (`322` purified `.ko` + `54` `system_dlkm`, single-pass `6.12` DT/DTBO, 5-gate + QEMU `kCFI`) | Verified stable |
| **[`6.1`](../../tree/6.1)** *(This branch)* | **`android14-6.1` LTS (`6.1.182`)** (single DRM CWB clone-check revert) | Stock `OS3.0.307.0` vendor DLKM (`654` `.ko`, `39,140` KMI CRCs verified) | Maintained LTS |

> **Note:** Personal engineering repository shared **as-is** (`rootless`, FOSS-first). **Never relock your bootloader** when running custom-signed builds on Xiaomi hardware.

## Highlights (`6.1` LTS Branch)

- **Updated & Purified `android14-6.1` GKI 2.0 LTS Kernel (`6.1.182`)**:
  - Reproducibly built from pinned Google `kernel/common` `android14-6.1` (`7bae370c0f35c4188943c4d8a4d5a357d157767f`, `6.1.182`) with official Android `clang-r487747c` via `build-kernel-6.1.py`.
  - Applies a single surgical revert (`patches/0001-Revert-drm-Add-valid-clones-check.patch`) of upstream Linux `ffb55ddf2685` (`drm: Add valid clones check`, `6.1.142+`) so Qualcomm's prebuilt `6.1.68` `msm_drm.ko` CWB under-display RGB ambient light sensor operates without `-EINVAL` flicker.
  - Verifies **100% ELF64 KMI symbol CRC parity** (`39,140 / 39,140` symbol refs across `system_dlkm`, stock `vendor_ramdisk`, and stock `vendor_dlkm`; `0` mismatches, `0` missing), while pruning `21` unused hypervisor/debug/socket GKI modules (`gzvm`, `kheaders`, `can*`, `ieee802154*`, `6lowpan`), retaining `tipc` + `tipc/diag` for SM8650 `nicmd`/`tlpd`.

## Repository Layout

- `build-kernel-6.1.py` — Fetches pinned Google `android14-6.1` (`6.1.182`) + `clang-r487747c`, applies `patches/0001-Revert-drm-Add-valid-clones-check.patch`, builds `Image` + `60` signed `system_dlkm` modules, verifies all KMI CRCs, and stages into `<rom_dir>/kernel/prebuilts/houji-gki/`.
- `patches/` — Minimal GKI patch series (`0001-Revert-drm-Add-valid-clones-check.patch`).

## Reproducing the Build (`6.1` LTS)

```bash
# Point at any synced Android ROM tree root (pass --container "" to build directly on host without Podman):
python3 -B ./build-kernel-6.1.py ../yaap-houji/yaap
```

Artifacts are staged cleanly into `<rom_dir>/kernel/prebuilts/houji-gki/` (`Image`, `system_dlkm/*.ko`, `vmlinux.symvers`, `provenance.json`, `SHA256SUMS`), ready to be picked up by your ROM build.
