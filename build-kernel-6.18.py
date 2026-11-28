#!/usr/bin/env python3
"""
Portable Linux 6.18 (android17-6.18) Kleaf build, wiring, staging, and ELF64 KMI
verification orchestrator for SM8650 (houji).

Preserves 100% pure GKI 2.0 (0 patches to common/), fetches exact pinned AOSP
common-android17-6.18 + Qualcomm Kleaf/soc-repo repositories, applies orthogonal
patches-soc-repo/, wires out-of-tree DDK techpacks + xiaomi-modules, builds
//common:kernel_aarch64[_images], //soc-repo:pineapple_perf_dist, and all active
external DDK modules, stages deduplicated artifacts, and verifies vermagic,
__versions CRCs, protected ABI symbols, and purified module exclusions.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile

KERNEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = KERNEL_DIR
PROJECT_ROOT = REPO_ROOT.parent

EXPECTED_COMMON_COMMIT = "f2cb2a697d859f06dfce050650b418e904f98ff7"
EXPECTED_SOC_BASE_COMMIT = "d08fc445800a9ed9466ffed45620a3e69cb43e36"
EXPECTED_RELEASE = "6.18.39-android17-6-gf2cb2a697d85-4k"

AOSP_BASE = "https://android.googlesource.com"
CLO_BASE = "https://git.codelinaro.org/clo/la"

# Exact verified git repositories & commit SHAs comprising the hermetic KP6 workspace.
KP6_PINNED_REPOS: tuple[tuple[str, str, str], ...] = (
    ("common", f"{AOSP_BASE}/kernel/common", EXPECTED_COMMON_COMMIT),
    ("build/kernel", f"{AOSP_BASE}/kernel/build", "2bebe7c443d0e6d5d549fb2a2377dce980d1b15e"),
    ("build/bazel_common_rules", f"{CLO_BASE}/kernel_platform/build/bazel_common_rules.git", "17ed69d097b3ea08d86ee93d3c898f9b9cc304b4"),
    ("soc-repo", f"{CLO_BASE}/kernel/qcom.git", EXPECTED_SOC_BASE_COMMIT),
    ("qcom/opensource/devicetree", f"{CLO_BASE}/platform/vendor/qcom-opensource/devicetree.git", "0e8fa33bcdcc121faa9b1160673031a1b586a5f3"),
    ("tools/mkbootimg", f"{CLO_BASE}/kernel_platform/system/tools/mkbootimg.git", "fbb061b39bfe93ff7f564f22e5e7ab528e292528"),
    ("external/qcom-dtc", f"{CLO_BASE}/kernel_platform/external/dtc.git", "92532f48874eb59ba34f5d0e0df236fe0ffbfd1e"),
    ("prebuilts/ndk-r26", f"{CLO_BASE}/kernel_platform/toolchain/prebuilts/ndk/r26.git", "e535051ebc04204cec44bde38f62385d63180388"),
    ("prebuilts/clang/host/linux-x86", f"{AOSP_BASE}/platform/prebuilts/clang/host/linux-x86", "29182889399818f97baa65ea238c80e8117981eb"),
    ("prebuilts/build-tools", f"{AOSP_BASE}/platform/prebuilts/build-tools", "e25d4821f50af3f6e0b87d7751dfd27037445fe5"),
    ("prebuilts/clang-tools", f"{AOSP_BASE}/platform/prebuilts/clang-tools", "4155cb466dcd9b53c6f873829c53aa6f905449dd"),
    ("prebuilts/kernel-build-tools", f"{AOSP_BASE}/kernel/prebuilts/build-tools", "78af2daf5d9d36fc6e1ddb28ee0d7b6516709eb4"),
    ("prebuilts/rust-toolchain/linux-x86", f"{AOSP_BASE}/platform/prebuilts/rust-toolchain/linux-x86", "844e1621bcf67c25ceae15bdd3ddd698cbe8df7c"),
    ("prebuilts/gcc/linux-x86/host/x86_64-linux-glibc2.17-4.8", f"{AOSP_BASE}/platform/prebuilts/gcc/linux-x86/host/x86_64-linux-glibc2.17-4.8", "b1d5a0acb5df6aed7db41faedd5ff06496df6141"),
    ("external/argp-standalone", f"{AOSP_BASE}/platform/external/argp-standalone", "29ae3b2a652d911463989a0274581553ae210bec"),
    ("external/avb", f"{AOSP_BASE}/platform/external/avb", "514f55780226bd2832b13442e7d59966153c1283"),
    ("external/bazelbuild-bazel-central-registry", f"{AOSP_BASE}/platform/external/bazelbuild-bazel-central-registry", "37c7fdc1ef273e518f1ded9495c5fc3afc2dabe9"),
    ("external/bazelbuild-platforms", f"{AOSP_BASE}/platform/external/bazelbuild-platforms", "3898a9b2e50a74bfb160dc4f0bf1272ec069ec4a"),
    ("external/bazelbuild-rules_cc", f"{AOSP_BASE}/platform/external/bazelbuild-rules_cc", "404dee97d87af768861d6132d50ff2d05764a330"),
    ("external/bazelbuild-rules_license", f"{AOSP_BASE}/platform/external/bazelbuild-rules_license", "299fc8d0c4b73b8b02a13e85bb147a1cc68d54e2"),
    ("external/bazelbuild-rules_pkg", f"{AOSP_BASE}/platform/external/bazelbuild-rules_pkg", "dfa24037af2af74ccbe49c71239bf3e1f57e165c"),
    ("external/bazelbuild-rules_python", f"{AOSP_BASE}/platform/external/bazelbuild-rules_python", "69d171976d1e64086fc4eaedea0742acf2b431ac"),
    ("external/bazelbuild-rules_rust", f"{AOSP_BASE}/platform/external/bazelbuild-rules_rust", "4d485a2924e0fe6d4f6e476fbab76b5dd6d70428"),
    ("external/bazelbuild-rules_shell", f"{AOSP_BASE}/platform/external/bazelbuild-rules_shell", "c4e9ac5cc4bcaf43c98deade100a1ab2651ead35"),
    ("external/bazel-contrib-bazel_features", f"{AOSP_BASE}/platform/external/bazel-contrib-bazel_features", "b0305957ee55fc3c2401ec3f2fe476276e350904"),
    ("external/bazel-contrib-rules_devicetree", f"{AOSP_BASE}/platform/external/bazel-contrib-rules_devicetree", "1664374016a47b392b1a16df5d460479975c3998"),
    ("external/bazel-contrib-supply-chain", f"{AOSP_BASE}/platform/external/bazel-contrib-supply-chain", "23bc331c0a9874dad8a49bcf4fc518613f64c3e1"),
    ("external/bazel-skylib", f"{AOSP_BASE}/platform/external/bazel-skylib", "dd13671f0fd8fb53f513e03d56e037a0d88a114f"),
    ("external/dtc", f"{AOSP_BASE}/platform/external/dtc", "b1b38550eb3c0df409295aa93560356e12c7fb7d"),
    ("external/dwarves", f"{AOSP_BASE}/platform/external/dwarves", "d884153887ce06fec729061016f7a0b412f08fc6"),
    ("external/kmod", f"{AOSP_BASE}/platform/external/kmod", "720e028e1c864ccfb61b2befad2c38f3f511cf86"),
    ("external/libcap", f"{AOSP_BASE}/platform/external/libcap", "6c0cfcad103ffa9fa0feb570644ac925b7ae944c"),
    ("external/libcap-ng", f"{AOSP_BASE}/platform/external/libcap-ng", "1ee5524f0bfcbbf9c6e4bc7c6aa2181e902f5d79"),
    ("external/lz4", f"{AOSP_BASE}/platform/external/lz4", "1370096b77602666f8e1f2550aaa489b6867939a"),
    ("external/obstack", f"{AOSP_BASE}/platform/external/obstack", "d4e6bd505ba5868e89d00cf1613f1629e31c15cb"),
    ("external/pigz", f"{AOSP_BASE}/platform/external/pigz", "9bc9fa17d499ddde88b77820f6d063e16c0cdd42"),
    ("external/python/absl-py", f"{AOSP_BASE}/platform/external/python/absl-py", "a63f315f9cc96721e6b78e2ca93a562d9ee913a3"),
    ("external/rust/android-crates-io", f"{AOSP_BASE}/platform/external/rust/android-crates-io", "6d25dfba2c7f9c91805f4a783bce9d7ab283f8c5"),
    ("external/toybox", f"{AOSP_BASE}/platform/external/toybox", "7c1c6410fe50cd5b5b1c1d4690a43b15ad80df86"),
    ("external/zlib", f"{AOSP_BASE}/platform/external/zlib", "ba77121a327c13eb7a446cde5997df1453a87355"),
    ("external/zopfli", f"{AOSP_BASE}/platform/external/zopfli", "a2eb9ba9578db4a4a0c86234483f13d9cd3ce93b"),
    ("external/zstd", f"{AOSP_BASE}/platform/external/zstd", "b000196fb5a7988dd038b4b856ab6be0f91c1f9e"),
)

VENDOR_TECHPACK_LINKS = [
    ("vendor/qcom/opensource/audio-kernel", KERNEL_DIR / "techpacks-conn/src/audio-kernel"),
    ("vendor/qcom/opensource/bt-kernel", KERNEL_DIR / "techpacks-conn/src/bt-kernel"),
    ("vendor/qcom/opensource/dataipa", KERNEL_DIR / "techpacks-conn/src/dataipa"),
    ("vendor/qcom/opensource/datarmnet", KERNEL_DIR / "techpacks-conn/src/datarmnet"),
    ("vendor/qcom/opensource/datarmnet-ext", KERNEL_DIR / "techpacks-conn/src/datarmnet-ext"),
    ("vendor/qcom/opensource/securemsm-kernel", KERNEL_DIR / "techpacks-conn/src/securemsm-kernel"),
    ("vendor/qcom/opensource/spu-kernel", KERNEL_DIR / "techpacks-conn/src/spu-kernel"),
    ("vendor/qcom/opensource/wlan/fw-api", KERNEL_DIR / "techpacks-conn/src/wlan/fw-api"),
    ("vendor/qcom/opensource/wlan/platform", KERNEL_DIR / "techpacks-conn/src/wlan/platform"),
    ("vendor/qcom/opensource/wlan/qcacld-3.0", KERNEL_DIR / "techpacks-conn/src/wlan/qcacld-3.0"),
    ("vendor/qcom/opensource/wlan/qca-wifi-host-cmn", KERNEL_DIR / "techpacks-conn/src/wlan/qca-wifi-host-cmn"),
    ("vendor/qcom/opensource/camera-kernel", KERNEL_DIR / "techpacks-mm/src/camera-kernel"),
    ("vendor/qcom/opensource/display-drivers", KERNEL_DIR / "techpacks-mm/src/display-drivers"),
    ("vendor/qcom/opensource/dsp-kernel", KERNEL_DIR / "techpacks-mm/src/dsp-kernel"),
    ("vendor/qcom/opensource/eva-kernel", KERNEL_DIR / "techpacks-mm/src/eva-kernel"),
    ("vendor/qcom/opensource/graphics-kernel", KERNEL_DIR / "techpacks-mm/src/graphics-kernel"),
    ("vendor/qcom/opensource/mm-drivers", KERNEL_DIR / "techpacks-mm/src/mm-drivers"),
    ("vendor/qcom/opensource/mmrm-driver", KERNEL_DIR / "techpacks-mm/src/mmrm-driver"),
    ("vendor/qcom/opensource/mm-sys-kernel", KERNEL_DIR / "techpacks-mm/src/mm-sys-kernel"),
    ("vendor/qcom/opensource/synx-kernel", KERNEL_DIR / "techpacks-mm/src/synx-kernel"),
    ("vendor/qcom/opensource/video-driver", KERNEL_DIR / "techpacks-mm/src/video-driver"),
    ("vendor/nxp/opensource/driver", KERNEL_DIR / "xiaomi-modules/src/nfc/nxp-nci"),
    ("xiaomi-modules", KERNEL_DIR / "xiaomi-modules"),
]

KLEAF_WORKSPACE_LINKS = [
    ("build/BUILD.bazel", "kernel/BUILD.bazel"),
    ("build/abi", "kernel/abi"),
    ("build/build_utils.sh", "kernel/build_utils.sh"),
    ("build/config.sh", "kernel/config.sh"),
    ("build/envsetup.sh", "kernel/envsetup.sh"),
    ("build/kleaf", "kernel/kleaf"),
    ("build/_setup_env.sh", "kernel/_setup_env.sh"),
    ("build/_wrapper_common.sh", "kernel/_wrapper_common.sh"),
    ("build/prebuilts/kernel-build-tools", "../../prebuilts/kernel-build-tools"),
    ("build/msm_kernel_extensions.bzl", "../soc-repo/kleaf-scripts/msm_kernel_extensions.bzl"),
    ("build/qcom_build_extensions", "../soc-repo/qcom_build_extensions"),
    ("build_with_bazel.py", "soc-repo/build_with_bazel.py"),
    ("device.bazelrc", "soc-repo/device.bazelrc"),
    ("WORKSPACE", "soc-repo/bazel.WORKSPACE"),
    ("MODULE.bazel", "build/kernel/kleaf/bzlmod/bazel.MODULE.bazel"),
    (".source_date_epoch_dir", "common"),
    ("tools/bazel", "../build/kernel/kleaf/bazel.sh"),
]

EXCLUDED_VENDOR_MODULES = {
    "tmecom-intf_dlkm.ko": "Replaced by real soc-repo tmecom-intf.ko.",
    "tornado_mod.ko": "Non-production securemsm test module.",
    "hdcp2p2_test.ko": "Non-production securemsm unit test driver.",
    "seccam_test_driver.ko": "Non-production securemsm unit test driver.",
    "si_core_test.ko": "Non-production securemsm unit test driver.",
    "crm-v2.ko": "Superseded on SM8650 by crm.ko.",
    "hwkm.ko": "Legacy HWKM v1 driver (monaco only).",
    "video.ko": "Duplicate vidc build artifact; houji uses msm_video.ko.",
    "qseecom_dlkm.ko": "Conflicts with qcom-scm + qseecom_proxy.ko + smcinvoke_dlkm.ko.",
    "qcom_dynamic_ramoops.ko": "Replaced by static ramoops node with built-in CONFIG_PSTORE_RAM=y.",
    "qcom-dload-mode.ko": "Superseded on houji by mi_md_kmsg.ko.",
    "clk-dummy.ko": "Dummy clock for disabled debugcc-pineapple.",
    "ipclite_test.ko": "Non-production synx-kernel test module.",
    "radio-i2c-rtc6226-qca.ko": "External FM radio tuner IC absent on houji.",
}

FORBIDDEN_PURIFIED_MODULES = frozenset({
    "coresight.ko", "coresight-csr.ko", "coresight-cti.ko", "coresight-dummy.ko",
    "coresight-etm4x.ko", "coresight-funnel.ko", "coresight-qmi.ko",
    "coresight-remote-etm.ko", "coresight-replicator.ko", "coresight-stm.ko",
    "coresight-tgu.ko", "coresight-tmc.ko", "coresight-tmc-sec.ko",
    "coresight-tpda.ko", "coresight-tpdm.ko", "coresight-trace-noc.ko",
    "coresight-trbe.ko", "coresight-uetm.ko",
    "stm_core.ko", "stm_console.ko", "stm_ftrace.ko", "stm_heartbeat.ko", "stm_p_ost.ko",
    "debug_symbol.ko", "debug-regulator.ko", "qti-regmap-debugfs.ko", "icc-debug.ko",
    "debugcc-pineapple.ko", "clk-dummy.ko", "eud.ko", "rdbg.ko",
    "spmi-pmic-arb-debug.ko", "qcom-cpufreq-hw-debug.ko", "pmic-glink-debug.ko",
    "qti_battery_debug.ko", "qcom-iommu-debug.ko", "hung_task_enh.ko", "boot_stats.ko",
    "msm_show_epoch.ko", "msm_show_resume_irq.ko", "llcc_perfmon.ko",
    "qcom_logbuf_boot_log.ko", "qcom_logbuf_vendor_hooks.ko", "f_fs_ipc_log.ko",
    "phy-qcom-ufs-qrbtc-sdm845.ko",
})

EXCLUDED_BAZEL_TARGETS = frozenset({
    "//vendor/qcom/opensource/bt-kernel:pineapple_perf_radio-i2c-rtc6226-qca",
    "//vendor/qcom/opensource/securemsm-kernel:pineapple_perf_tmecom-intf_dlkm",
    "//vendor/qcom/opensource/securemsm-kernel:pineapple_perf_tornado_mod",
    "//vendor/qcom/opensource/securemsm-kernel:pineapple_perf_hdcp2p2_test",
    "//vendor/qcom/opensource/securemsm-kernel:pineapple_perf_seccam_test_driver",
    "//vendor/qcom/opensource/securemsm-kernel:pineapple_perf_si_core_test",
    "//vendor/qcom/opensource/securemsm-kernel:pineapple_perf_qseecom_dlkm",
    "//vendor/qcom/opensource/synx-kernel:pineapple_perf_ipclite_test_synx",
    "//vendor/qcom/opensource/video-driver:pineapple_perf_video",
    "//vendor/qcom/opensource/wlan/qcacld-3.0:pineapple_perf_qca_cld_peach",
    "//vendor/qcom/opensource/wlan/qcacld-3.0:pineapple_perf_qca_cld_peach-v2",
    "//vendor/qcom/opensource/wlan/qcacld-3.0:pineapple_perf_qca_cld_qca6750",
    "//vendor/qcom/opensource/wlan/qcacld-3.0:pineapple_perf_qca_cld_wcn6450",
})


def run(cmd: list[str], cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=capture)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_pinned_git_repo(dst: Path, url: str, sha: str) -> str:
    if dst.is_symlink():
        return f"SKIP symlink {dst}"
    if (dst / ".git").exists():
        cur = subprocess.run(["git", "-C", str(dst), "rev-parse", "HEAD"], text=True, capture_output=True)
        if cur.returncode == 0 and cur.stdout.strip() == sha:
            return f"OK cached {dst.name} @ {sha[:12]}"
    else:
        dst.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=dst, check=True)
    rems = subprocess.check_output(["git", "-C", str(dst), "remote"], text=True).split()
    if "origin" in rems:
        subprocess.run(["git", "-C", str(dst), "remote", "set-url", "origin", url], check=True)
    else:
        subprocess.run(["git", "-C", str(dst), "remote", "add", "origin", url], check=True)
    subprocess.run(["git", "-C", str(dst), "fetch", "--depth=1", "--no-tags", "origin", sha], check=True)
    subprocess.run(["git", "-C", str(dst), "checkout", "-q", "--detach", "FETCH_HEAD"], check=True)
    return f"FETCHED {dst} @ {sha[:12]}"


def sync_kp6(kp6_dir: Path, rom_dir: Path | None, jobs: int) -> None:
    kp6_dir.mkdir(parents=True, exist_ok=True)

    # Reuse ROM clang-r596125 prebuilt via symlink if available locally to save ~6 GB download;
    # otherwise _fetch_pinned_git_repo downloads the exact AOSP prebuilt commit.
    clang_dst = kp6_dir / "prebuilts/clang/host/linux-x86"
    if not clang_dst.exists() and rom_dir:
        rom_clang = rom_dir / "prebuilts/clang/host/linux-x86"
        if (rom_clang / "clang-r596125/bin/clang").is_file():
            clang_dst.parent.mkdir(parents=True, exist_ok=True)
            clang_dst.symlink_to(rom_clang.resolve())
            print(f"Reused local clang-r596125 prebuilt from {rom_clang}")

    workers = max(1, min(jobs, 8))
    print(f"Syncing {len(KP6_PINNED_REPOS)} pinned KP6 repositories (parallel={workers})...", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_fetch_pinned_git_repo, kp6_dir / rel, url, sha): rel
            for rel, url, sha in KP6_PINNED_REPOS
        }
        for fut in as_completed(futs):
            rel = futs[fut]
            msg = fut.result()
            print(f"  [{rel}] {msg}", flush=True)


def _apply_patch_idempotent(repo_dir: Path, marker_file: Path, needle: str, patch_file: Path, expected_base: str) -> None:
    if marker_file.is_file() and needle in marker_file.read_text(errors="replace"):
        return
    head = subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], text=True).strip()
    if head != expected_base:
        print(f"WARNING: {repo_dir} HEAD {head} != expected {expected_base}", file=sys.stderr)
    run(["git", "-C", str(repo_dir), "apply", "--whitespace=nowarn", str(patch_file)])


def ensure_workspace_patched(kp6_dir: Path) -> None:
    patch_dir = KERNEL_DIR / "patches-soc-repo"
    soc = kp6_dir / "soc-repo"
    if not soc.exists():
        raise FileNotFoundError(f"Missing {soc}; pass --sync-kp6 to fetch pinned AOSP + Qualcomm KP6 repositories.")

    if not ((soc / "configs/pineapple_perf.bzl").is_file() and (soc / "drivers/mfd/qcom_pm8008_fix.diff").is_file()):
        head = subprocess.check_output(["git", "-C", str(soc), "rev-parse", "HEAD"], text=True).strip()
        if head != EXPECTED_SOC_BASE_COMMIT:
            print(f"WARNING: soc-repo HEAD {head} != expected {EXPECTED_SOC_BASE_COMMIT}", file=sys.stderr)
        status = subprocess.check_output(["git", "-C", str(soc), "status", "--porcelain"], text=True).strip()
        if status:
            raise RuntimeError(f"soc-repo has unexpected partial modifications before patching:\n{status}")
        run(["git", "-C", str(soc), "apply", "--whitespace=nowarn", str(patch_dir / "0001-houji-sm8650-soc-repo-purified-6.18.patch")])
        untracked_dir = patch_dir / "untracked"
        for src in sorted(untracked_dir.rglob("*")):
            if src.is_file():
                rel = src.relative_to(untracked_dir)
                dst = soc / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        print("Applied 0001-houji-sm8650-soc-repo-purified-6.18.patch and untracked files onto soc-repo.")

    _apply_patch_idempotent(
        kp6_dir / "build/kernel",
        kp6_dir / "build/kernel/kleaf/impl/ddk/makefiles.bzl",
        "-fno-stack-protector",
        patch_dir / "0002-kleaf-ddk-disable-global-stack-protector.patch",
        "2bebe7c443d0e6d5d549fb2a2377dce980d1b15e",
    )
    _apply_patch_idempotent(
        kp6_dir / "build/bazel_common_rules",
        kp6_dir / "build/bazel_common_rules/dist/dist.bzl",
        "@rules_python//python:defs.bzl",
        patch_dir / "0003-bazel-common-rules-rules-python-py-binary.patch",
        "17ed69d097b3ea08d86ee93d3c898f9b9cc304b4",
    )
    _apply_patch_idempotent(
        kp6_dir / "qcom/opensource/devicetree",
        kp6_dir / "qcom/opensource/devicetree/qcom/pineapple-pmic-overlay.dtsi",
        "pm8010m_temp_alarm",
        patch_dir / "0004-qcom-devicetree-pineapple-pmic-spmi-tz.patch",
        "0e8fa33bcdcc121faa9b1160673031a1b586a5f3",
    )


def wire_symlinks(kp6_dir: Path, force_rewire: bool = False) -> None:
    for rel_link, rel_target in KLEAF_WORKSPACE_LINKS:
        link_p = kp6_dir / rel_link
        link_p.parent.mkdir(parents=True, exist_ok=True)
        if link_p.is_symlink() or link_p.exists():
            if not force_rewire and link_p.exists():
                continue
            if link_p.is_symlink() or link_p.is_file():
                link_p.unlink()
            else:
                shutil.rmtree(link_p)
        link_p.symlink_to(rel_target)

    qcom_dt = (kp6_dir / "qcom/opensource/devicetree").resolve()
    soc_real = (kp6_dir / "soc-repo").resolve()
    for dt_link_rel in ("arch/arm64/boot/dts/vendor", "scripts/dtc/include-prefixes/arm64/vendor"):
        link_p = soc_real / dt_link_rel
        link_p.parent.mkdir(parents=True, exist_ok=True)
        if link_p.is_symlink() or link_p.exists():
            if not force_rewire and link_p.exists():
                continue
            if link_p.is_symlink() or link_p.is_file():
                link_p.unlink()
            else:
                shutil.rmtree(link_p)
        link_p.symlink_to(qcom_dt)

    for rel_link, target in VENDOR_TECHPACK_LINKS:
        link_p = kp6_dir / rel_link
        link_p.parent.mkdir(parents=True, exist_ok=True)
        if link_p.is_symlink():
            if not force_rewire and link_p.exists():
                continue
            if link_p.resolve() == target.resolve():
                continue
            link_p.unlink()
        elif link_p.exists():
            if not force_rewire:
                continue
            shutil.rmtree(link_p)
        link_p.symlink_to(target)


def verify_common_clean(kp6_dir: Path) -> str:
    common_dir = kp6_dir / "common"
    common_status = subprocess.check_output(["git", "-C", str(common_dir), "status", "--porcelain"], text=True).strip()
    if common_status:
        raise RuntimeError(f"FATAL: common/ MUST remain 100% unmodified GKI 2.0, found dirty files:\n{common_status}")
    common_head = subprocess.check_output(["git", "-C", str(common_dir), "rev-parse", "HEAD"], text=True).strip()
    if common_head != EXPECTED_COMMON_COMMIT:
        print(f"NOTE: common/ HEAD is {common_head} (verified pin {EXPECTED_COMMON_COMMIT})", file=sys.stderr)
    return common_head


def bazel_common_and_override_flags(bazel_cache: Path) -> tuple[list[str], list[str]]:
    bazel_cache.mkdir(parents=True, exist_ok=True)
    common_flags = [
        f"--output_root={bazel_cache}",
        f"--output_user_root={bazel_cache / 'user'}",
    ]
    override_flags = [
        "--noenable_workspace",
        "--override_module=rules_kotlin=%workspace%/build/kernel/kleaf/bzlmod/fake_modules/rules_kotlin",
        "--override_module=protobuf=%workspace%/build/kernel/kleaf/bzlmod/fake_modules/protobuf",
        "--override_module=rules_java=%workspace%/build/kernel/kleaf/bzlmod/fake_modules/rules_java",
    ]
    return common_flags, override_flags


def locate_bazel_bin(kp6_dir: Path, bazel_cache: Path) -> Path:
    direct = kp6_dir / "bazel-bin"
    if direct.exists() and (direct / "common").is_dir():
        return direct.resolve()
    for pat in ("user/*/execroot/_main/bazel-out/k8-fastbuild/bin", "bazel-cache/*/execroot/_main/bazel-out/k8-fastbuild/bin"):
        for c in sorted(bazel_cache.glob(pat)):
            if c.is_dir():
                return c
    raise FileNotFoundError(f"Could not locate bazel-out/k8-fastbuild/bin under {kp6_dir} or {bazel_cache}")


def build_kleaf(kp6_dir: Path, bazel_cache: Path, jobs: int) -> None:
    common_flags, override_flags = bazel_common_and_override_flags(bazel_cache)
    q_res = run(
        [
            str(kp6_dir / "tools/bazel"),
            *common_flags,
            "query",
            *override_flags,
            'kind("^_kernel_module ", filter("pineapple_perf", //vendor/... + //xiaomi-modules/src:all))',
        ],
        cwd=kp6_dir,
        capture=True,
    )
    ext_targets = sorted(
        line.strip()
        for line in q_res.stdout.splitlines()
        if line.strip().startswith("//") and line.strip() not in EXCLUDED_BAZEL_TARGETS
    )
    all_targets = [
        "//common:kernel_aarch64",
        "//common:kernel_aarch64_images",
        "//soc-repo:pineapple_perf_dist",
        *ext_targets,
    ]
    print(f"Building common + //soc-repo:pineapple_perf_dist + {len(ext_targets)} purified external ddk_module targets...")
    run(
        [
            str(kp6_dir / "tools/bazel"),
            *common_flags,
            "build",
            f"--jobs={jobs}",
            "--config=stamp",
            "--incompatible_sandbox_hermetic_tmp=false",
            "--symlink_prefix=/",
            *override_flags,
            *all_targets,
        ],
        cwd=kp6_dir,
    )


def parse_elf64_ko(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2:
        raise ValueError(f"Not ELF64: {path}")
    e_shoff = struct.unpack_from("<Q", data, 40)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 58)

    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh_name, sh_type, _, _, sh_offset, sh_size, sh_link, _, _, sh_entsize = struct.unpack_from(
            "<IIQQQQIIQQ", data, off
        )
        sections.append({
            "name_off": sh_name, "type": sh_type, "offset": sh_offset,
            "size": sh_size, "link": sh_link, "entsize": sh_entsize,
        })

    shstr = sections[e_shstrndx]
    shstr_bytes = data[shstr["offset"]:shstr["offset"] + shstr["size"]]

    def get_cstr(buf: bytes, off: int) -> str:
        return buf[off:buf.index(b"\x00", off)].decode("utf-8", errors="replace")

    by_name = {}
    for s in sections:
        s["name"] = get_cstr(shstr_bytes, s["name_off"])
        by_name[s["name"]] = s

    vermagic = None
    depends = []
    if ".modinfo" in by_name:
        mi = by_name[".modinfo"]
        raw = data[mi["offset"]:mi["offset"] + mi["size"]]
        for entry in raw.split(b"\x00"):
            if entry.startswith(b"vermagic="):
                vermagic = entry[9:].decode("utf-8", errors="replace")
            elif entry.startswith(b"depends="):
                dep_str = entry[8:].decode("utf-8", errors="replace").strip()
                if dep_str:
                    depends = [d for d in dep_str.split(",") if d]

    imports = {}
    if "__versions" in by_name:
        vs = by_name["__versions"]
        raw = data[vs["offset"]:vs["offset"] + vs["size"]]
        for pos in range(0, len(raw), 64):
            if pos + 64 > len(raw):
                break
            crc = struct.unpack_from("<Q", raw, pos)[0] & 0xFFFFFFFF
            name = raw[pos + 8:pos + 64].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            if name:
                imports[name] = crc

    exports = {}
    undef_symbols = set()
    if ".symtab" in by_name:
        st = by_name[".symtab"]
        strtab = sections[st["link"]]
        str_bytes = data[strtab["offset"]:strtab["offset"] + strtab["size"]]
        entsize = st["entsize"] or 24
        for pos in range(st["offset"], st["offset"] + st["size"], entsize):
            st_name, _, _, st_shndx, st_value, _ = struct.unpack_from("<IBBHQQ", data, pos)
            sym_name = get_cstr(str_bytes, st_name)
            if sym_name.startswith("__crc_"):
                if st_shndx == 0xFFF1:
                    crc_val = st_value & 0xFFFFFFFF
                elif 0 < st_shndx < len(sections):
                    sec = sections[st_shndx]
                    crc_val = struct.unpack_from("<I", data, sec["offset"] + st_value)[0]
                else:
                    crc_val = st_value & 0xFFFFFFFF
                exports[sym_name[6:]] = crc_val
            elif st_shndx == 0 and sym_name and sym_name not in ("__this_module", "module_layout"):
                undef_symbols.add(sym_name)

    signed = b"~Module signature appended~" in data
    return {
        "vermagic": vermagic,
        "depends": depends,
        "imports": imports,
        "exports": exports,
        "undef_symbols": undef_symbols,
        "signed": signed,
        "size": len(data),
    }


def load_vmlinux_symvers(path: Path) -> dict[str, int]:
    syms = {}
    for line in path.read_text().splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2:
            syms[parts[1]] = int(parts[0], 16) & 0xFFFFFFFF
    return syms


def pick_first_existing(candidates: list[Path], desc: str) -> Path:
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(f"Missing {desc}; checked: {[str(c) for c in candidates]}")


def stage_and_verify(kp6_dir: Path, bazel_cache: Path, staged_dir: Path) -> int:
    common_head = verify_common_clean(kp6_dir)
    soc_dist = kp6_dir / "out/msm-kernel-pineapple-perf/dist"
    bin_dir = locate_bazel_bin(kp6_dir, bazel_cache)

    sys_dlkm_dir = staged_dir / "system_dlkm"
    vendor_all_dir = staged_dir / "vendor_all"
    if staged_dir.exists():
        shutil.rmtree(staged_dir)
    sys_dlkm_dir.mkdir(parents=True)
    vendor_all_dir.mkdir(parents=True)

    pure_gki_cfg = pick_first_existing([
        bin_dir / "common/kernel_aarch64_config/out_dir/.config",
        bin_dir / "common/kernel_aarch64/.config",
    ], "pure GKI .config")
    img_src = pick_first_existing([soc_dist / "Image", bin_dir / "common/kernel_aarch64/Image"], "Image")
    sym_src = pick_first_existing([soc_dist / "vmlinux.symvers", bin_dir / "common/kernel_aarch64/vmlinux.symvers"], "vmlinux.symvers")
    abi_src = pick_first_existing([
        soc_dist / "abi_symbollist",
        bin_dir / "common/kernel_aarch64/abi_symbollist",
        bin_dir / "common/kernel_aarch64_kmi_symbol_list/abi_symbollist",
    ], "abi_symbollist")
    perf_cfg_src = pick_first_existing([soc_dist / ".config", bin_dir / "soc-repo/pineapple_perf_dtb_build/.config", pure_gki_cfg], "pineapple_perf .config")
    sys_tar_src = pick_first_existing([
        soc_dist / "system_dlkm_staging_archive.tar.gz",
        bin_dir / "common/kernel_aarch64_images/system_dlkm_staging_archive.tar.gz",
        bin_dir / "common/kernel_aarch64_system_dlkm_image/system_dlkm_staging_archive.tar.gz",
    ], "system_dlkm_staging_archive.tar.gz")

    for dst_name, src_path in {
        "Image": img_src,
        "vmlinux.symvers": sym_src,
        "abi_symbollist": abi_src,
        "common.config": pure_gki_cfg,
        "pineapple_perf.config": perf_cfg_src,
        "system_dlkm_staging_archive.tar.gz": sys_tar_src,
    }.items():
        shutil.copy2(src_path, staged_dir / dst_name)

    img_bytes = (staged_dir / "Image").read_bytes()
    m = re.search(rb"Linux version ([^\s]+)", img_bytes)
    image_release = m.group(1).decode("utf-8") if m else "UNKNOWN"
    if common_head == EXPECTED_COMMON_COMMIT and image_release != EXPECTED_RELEASE:
        raise RuntimeError(f"Image release mismatch: got {image_release}, expected {EXPECTED_RELEASE}")
    if not image_release.startswith("6.18.") or "android17" not in image_release:
        raise RuntimeError(f"Unexpected Image release banner: {image_release}")
    expected_vermagic = f"{image_release} SMP preempt mod_unload modversions aarch64"

    gki_cfg = (staged_dir / "common.config").read_text()
    assert "CONFIG_TIPC=m" in gki_cfg and "PINEAPPLE" not in gki_cfg

    sys_dlkm_kos: list[str] = []
    with tarfile.open(staged_dir / "system_dlkm_staging_archive.tar.gz", "r:gz") as tf:
        for member in tf.getmembers():
            if member.isfile() and member.name.startswith("./flatten/lib/modules/"):
                fn = os.path.basename(member.name)
                dst = sys_dlkm_dir / fn
                with tf.extractfile(member) as src_f, dst.open("wb") as dst_f:
                    assert src_f is not None
                    shutil.copyfileobj(src_f, dst_f)
                if fn.endswith(".ko"):
                    sys_dlkm_kos.append(fn)
    sys_dlkm_kos.sort()
    assert "tipc.ko" in sys_dlkm_kos and "tipc_diag.ko" in sys_dlkm_kos

    active_soc_list_path = bin_dir / "soc-repo/modules.list.vendor_dlkm.pineapple_perf"
    active_soc_kos = {ln.strip() for ln in active_soc_list_path.read_text().splitlines() if ln.strip()}
    sys_dlkm_set = set(sys_dlkm_kos)

    sources = [
        ("soc-repo", soc_dist if soc_dist.is_dir() else bin_dir / "soc-repo"),
        ("soc-repo", bin_dir / "soc-repo"),
        ("techpacks", bin_dir / "vendor"),
        ("xiaomi-modules", bin_dir / "xiaomi-modules"),
    ]
    found: dict[str, list[tuple[str, Path, Path]]] = {}
    for group, root in sources:
        if not root.is_dir():
            continue
        for dirpath, _, files in os.walk(root):
            if any(s in dirpath for s in ("unstripped", "_staging", "intermediates", ".runfiles", "/staging/")):
                continue
            for fn in sorted(files):
                if not fn.endswith(".ko") or fn in EXCLUDED_VENDOR_MODULES or fn in sys_dlkm_set:
                    continue
                if group == "soc-repo" and fn not in active_soc_kos:
                    continue
                abs_p = Path(dirpath) / fn
                if abs_p.is_file():
                    found.setdefault(fn, []).append((group, abs_p.relative_to(root), abs_p))

    rank = {"soc-repo": 1, "techpacks": 2, "xiaomi-modules": 3}
    selected_vendor: dict[str, dict] = {}
    group_counts: dict[str, int] = defaultdict(int)
    for fn in sorted(found):
        winner = sorted(found[fn], key=lambda x: rank[x[0]], reverse=True)[0]
        dst = vendor_all_dir / fn
        shutil.copy2(winner[2], dst)
        group_counts[winner[0]] += 1
        selected_vendor[fn] = {
            "group": winner[0],
            "rel_path": str(winner[1]),
            "size": dst.stat().st_size,
            "sha256": sha256_file(dst),
        }

    assert selected_vendor["qti_battery_charger.ko"]["group"] == "xiaomi-modules"
    assert selected_vendor["qcom-hv-haptics.ko"]["group"] == "xiaomi-modules"
    assert b"N3_PANEL_PA" in (vendor_all_dir / "msm_drm.ko").read_bytes()
    sys_overlap = sorted(sys_dlkm_set & set(selected_vendor))
    assert not sys_overlap, f"Vendor modules duplicate system_dlkm: {sys_overlap}"
    leaked_purified = sorted(FORBIDDEN_PURIFIED_MODULES & set(selected_vendor))
    assert not leaked_purified, f"Purified debug modules still present in vendor_all: {leaked_purified}"

    vmlinux_syms = load_vmlinux_symvers(staged_dir / "vmlinux.symvers")
    all_kos = [("system_dlkm", fn, sys_dlkm_dir / fn) for fn in sys_dlkm_kos] + [
        ("vendor_all", fn, vendor_all_dir / fn) for fn in sorted(selected_vendor)
    ]

    parsed = {}
    all_exports = dict(vmlinux_syms)
    vermagics: dict[str | None, list[str]] = defaultdict(list)

    for bucket, fn, full_path in all_kos:
        info = parse_elf64_ko(full_path)
        parsed[(bucket, fn)] = info
        vermagics[info["vermagic"]].append(f"{bucket}/{fn}")
        for sym, crc in info["exports"].items():
            all_exports[sym] = crc

    abi_set = {
        ln.strip()
        for ln in (staged_dir / "abi_symbollist").read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("[")
    }
    mod_exported_syms = set()
    for bucket, fn, _ in all_kos:
        mod_exported_syms.update(parsed[(bucket, fn)]["exports"].keys())

    missing_symbols = []
    crc_mismatches = []
    protected_or_unexported = []
    total_refs = 0
    for bucket, fn, _ in all_kos:
        info = parsed[(bucket, fn)]
        for sym, req_crc in info["imports"].items():
            total_refs += 1
            if sym not in all_exports:
                missing_symbols.append({"module": f"{bucket}/{fn}", "symbol": sym, "crc": hex(req_crc)})
            elif all_exports[sym] != req_crc:
                crc_mismatches.append({"module": f"{bucket}/{fn}", "symbol": sym})
        if not info["signed"]:
            for sym in sorted(info["undef_symbols"]):
                if sym not in mod_exported_syms and sym not in abi_set:
                    protected_or_unexported.append({"module": f"{bucket}/{fn}", "symbol": sym})

    verdict = (
        "PASS"
        if (
            len(vermagics) == 1
            and expected_vermagic in vermagics
            and not missing_symbols
            and not crc_mismatches
            and not protected_or_unexported
        )
        else "FAIL"
    )
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "common_commit": common_head,
        "common_dirty": 0,
        "image_release": image_release,
        "expected_vermagic": expected_vermagic,
        "vermagics_seen": {str(k): len(v) for k, v in vermagics.items()},
        "counts": {
            "system_dlkm": len(sys_dlkm_kos),
            "vendor_all": len(selected_vendor),
            "vendor_breakdown": dict(group_counts),
        },
        "elf_kmi_verification": {
            "total_versioned_symbol_refs": total_refs,
            "missing_symbol_count": len(missing_symbols),
            "crc_mismatch_count": len(crc_mismatches),
            "protected_symbol_count": len(protected_or_unexported),
            "missing_symbols_sample": missing_symbols[:40],
            "crc_mismatches_sample": crc_mismatches[:40],
            "protected_symbols_sample": protected_or_unexported[:40],
            "verdict": verdict,
        },
    }
    (staged_dir / "UNIFIED_MODULES_REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if verdict == "PASS" else 2


def _default_rom_dir() -> Path | None:
    for cand in (PROJECT_ROOT / "yaap-houji/yaap", PROJECT_ROOT / "yaap", REPO_ROOT / "yaap"):
        if cand.is_dir():
            return cand
    return None


def main() -> int:
    default_kp6 = REPO_ROOT / "kernel-6.18/kp6"
    if not default_kp6.is_dir():
        default_kp6 = REPO_ROOT / "kp6"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kp6-dir", type=Path, default=default_kp6, help="KP6 workspace root")
    parser.add_argument("--rom-dir", type=Path, default=_default_rom_dir(), help="Optional ROM root to reuse clang-r596125 prebuilt")
    parser.add_argument("--bazel-cache", type=Path, default=None, help="Bazel output cache root")
    parser.add_argument("--staged-dir", type=Path, default=REPO_ROOT / "out-6.18/staged-modules", help="Staged output dir")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 8, help="Parallel Bazel/fetch jobs")
    parser.add_argument("--sync-kp6", action="store_true", help="Fetch pinned AOSP common-android17-6.18 + Qualcomm KP6 repos before building")
    parser.add_argument("--wire-only", action="store_true", help="Only apply workspace patches and create symlinks")
    parser.add_argument("--stage-only", action="store_true", help="Skip Bazel build and restage/verify existing outputs")
    args = parser.parse_args()

    kp6_dir = args.kp6_dir.resolve()
    rom_dir = args.rom_dir.resolve() if args.rom_dir else None
    staged_dir = args.staged_dir.resolve()
    if args.bazel_cache:
        bazel_cache = args.bazel_cache.resolve()
    elif kp6_dir.parent.name == "kernel-6.18" and (kp6_dir.parent / "bazel-cache").is_dir():
        bazel_cache = (kp6_dir.parent / "bazel-cache").resolve()
    else:
        bazel_cache = (kp6_dir / "bazel-cache").resolve()

    if args.sync_kp6:
        sync_kp6(kp6_dir, rom_dir, args.jobs)
    ensure_workspace_patched(kp6_dir)
    wire_symlinks(kp6_dir, force_rewire=args.wire_only)
    if args.wire_only:
        return 0
    if not args.stage_only:
        build_kleaf(kp6_dir, bazel_cache, args.jobs)
    return stage_and_verify(kp6_dir, bazel_cache, staged_dir)


if __name__ == "__main__":
    sys.exit(main())
