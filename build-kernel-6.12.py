#!/usr/bin/env python3
"""
Portable Linux 6.12 (android16-6.12 / KERNEL.PLATFORM.5.0) Kleaf build, wiring,
staging, and ELF64 KMI verification orchestrator for SM8650 (houji).

Preserves 100% pure GKI 2.0 (0 patches to kernel_platform/common), wires in-tree
out-of-tree DDK techpacks + xiaomi-modules, builds //soc-repo:pineapple_perf_dist
and all 99 active external DDK modules, stages deduplicated artifacts, and verifies
every ELF64 vermagic, __versions CRC, and purified module exclusion.
"""

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

EXPECTED_COMMON_COMMIT = "8fc057fab7569433e990d11a2362ccb84f7b301d"
EXPECTED_SOC_BASE_COMMIT = "d08fc445800a9ed9466ffed45620a3e69cb43e36"
EXPECTED_RELEASE = "6.12.81-android16-6-g8fc057fab756-4k"
EXPECTED_VERMAGIC = f"{EXPECTED_RELEASE} SMP preempt mod_unload modversions aarch64"

KP5_MANIFEST_URL = "https://git.codelinaro.org/clo/la/kernelplatform/manifest.git"
KP5_MANIFEST_FILE = "KERNEL.PLATFORM.5.0.r48-00400-kernel.0.xml"

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

# Non-production test drivers, panic stubs, and superseded modules explicitly excluded from staging
EXCLUDED_VENDOR_MODULES = {
    "tmecom-intf_dlkm.ko": "Qualcomm stub in securemsm-kernel whose stub_init() calls panic(); replaced by real soc-repo tmecom-intf.ko.",
    "tornado_mod.ko": "Non-production securemsm test module causing sysfs_warn_dup Call trace on /class/char.",
    "hdcp2p2_test.ko": "Non-production securemsm unit test driver.",
    "seccam_test_driver.ko": "Non-production securemsm unit test driver.",
    "si_core_test.ko": "Non-production securemsm unit test driver.",
    "crm-v2.ko": "Superseded on SM8650 (pineapple) by crm.ko (CONFIG_QCOM_CRM=m).",
    "hwkm.ko": "Legacy HWKM v1 driver (monaco only); SM8650 uses HWKM v2 via TrustZone qcom_scm.",
    "video.ko": "Duplicate vidc build artifact from video-driver; SM8650 uses msm_video.ko.",
    "qseecom_dlkm.ko": "Unused standalone qseecom driver conflicting with qcom-scm built-in QSEECOM + qseecom_proxy.ko + smcinvoke_dlkm.ko.",
    "qcom_dynamic_ramoops.ko": "Unused dynamic qcom,ramoops driver; houji uses static compatible='ramoops' with built-in CONFIG_PSTORE_RAM=y.",
    "qcom-dload-mode.ko": "Superseded on houji by mi_md_kmsg.ko which enforces QCOM_DOWNLOAD_MINIDUMP (0x20).",
    "clk-dummy.ko": "Dummy measure clock only consumed by disabled debugcc-pineapple.",
    "ipclite_test.ko": "Non-production synx-kernel IPC test module.",
    "radio-i2c-rtc6226-qca.ko": "External FM radio tuner IC absent on houji hardware.",
}

FORBIDDEN_PURIFIED_MODULES = frozenset({
    "coresight.ko", "coresight-csr.ko", "coresight-cti.ko", "coresight-dummy.ko",
    "coresight-funnel.ko", "coresight-qmi.ko", "coresight-remote-etm.ko",
    "coresight-replicator.ko", "coresight-stm.ko", "coresight-tgu.ko",
    "coresight-tmc.ko", "coresight-tmc-sec.ko", "coresight-tpda.ko",
    "coresight-tpdm.ko", "coresight-trace-noc.ko", "coresight-uetm.ko",
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


def run(cmd: list[str], cwd: Path | None = None, capture: bool = False):
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=capture)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sync_kp5(kp5_dir: Path, jobs: int) -> None:
    kp5_dir.mkdir(parents=True, exist_ok=True)
    if not (kp5_dir / ".repo").is_dir():
        run(["repo", "init", "-u", KP5_MANIFEST_URL, "-b", "release", "-m", KP5_MANIFEST_FILE, "--depth=1"], cwd=kp5_dir)
    run(["repo", "sync", "-c", f"-j{jobs}", "--no-clone-bundle", "--no-tags"], cwd=kp5_dir)


def ensure_soc_repo_patched(kp5_dir: Path) -> None:
    soc = kp5_dir / "kernel_platform/soc-repo"
    patch_dir = KERNEL_DIR / "patches-soc-repo"
    patch_file = patch_dir / "0001-houji-sm8650-soc-repo-purified-6.12.patch"
    untracked_dir = patch_dir / "untracked"

    if (soc / "configs/pineapple_perf.bzl").is_file() and (soc / "modules-lists/modules.list.msm.pineapple").is_file():
        print("soc-repo already patched with pineapple_perf.bzl; skipping patch application.")
        return

    head = subprocess.check_output(["git", "-C", str(soc), "rev-parse", "HEAD"], text=True).strip()
    if head != EXPECTED_SOC_BASE_COMMIT:
        print(f"WARNING: soc-repo HEAD {head} != expected {EXPECTED_SOC_BASE_COMMIT}", file=sys.stderr)
    status = subprocess.check_output(["git", "-C", str(soc), "status", "--porcelain"], text=True).strip()
    if status:
        raise RuntimeError(f"soc-repo has unexpected partial modifications before patching:\n{status}")

    run(["git", "-C", str(soc), "apply", "--whitespace=nowarn", str(patch_file)])
    for src in sorted(untracked_dir.rglob("*")):
        if src.is_file():
            rel = src.relative_to(untracked_dir)
            dst = soc / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    print(f"Applied {patch_file.name} and untracked files onto soc-repo cleanly.")


def wire_symlinks(kp5_dir: Path) -> None:
    kp = kp5_dir / "kernel_platform"
    for rel_link, target in VENDOR_TECHPACK_LINKS:
        link_p = kp / rel_link
        link_p.parent.mkdir(parents=True, exist_ok=True)
        if link_p.is_symlink():
            if link_p.resolve() == target.resolve():
                continue
            link_p.unlink()
        elif link_p.exists():
            # Replace upstream placeholder directory with symlink to our in-repo tree
            shutil.rmtree(link_p)
        link_p.symlink_to(target)


def locate_bazel_bin(kp_dir: Path, bazel_cache: Path) -> Path:
    direct = kp_dir / "bazel-bin"
    if direct.exists() and (direct / "common/kernel_aarch64_config/out_dir/.config").is_file():
        return direct.resolve()
    cands = sorted(bazel_cache.glob("bazel-cache/*/execroot/_main/bazel-out/k8-fastbuild/bin"))
    for c in cands:
        if c.is_dir():
            return c
    raise FileNotFoundError(f"Could not locate bazel-out/k8-fastbuild/bin under {kp_dir} or {bazel_cache}")


def build_kleaf(kp5_dir: Path, jobs: int) -> None:
    kp = kp5_dir / "kernel_platform"
    bazel_cache = kp5_dir / "bazel-cache"
    bazel_user_cache = bazel_cache / "bazel-cache"
    bazel_cache.mkdir(parents=True, exist_ok=True)

    common_flags = [
        f"--output_root={bazel_cache}",
        f"--output_user_root={bazel_user_cache}",
    ]
    override_flags = [
        "--noenable_workspace",
        "--override_module=rules_kotlin=%workspace%/build/kernel/kleaf/bzlmod/fake_modules/rules_kotlin",
        "--override_module=protobuf=%workspace%/build/kernel/kleaf/bzlmod/fake_modules/protobuf",
        "--override_module=rules_java=%workspace%/build/kernel/kleaf/bzlmod/fake_modules/rules_java",
    ]

    q_res = run(
        [
            str(kp / "tools/bazel"),
            *common_flags,
            "query",
            *override_flags,
            'kind("ddk_module", filter("pineapple_perf", //vendor/... + //xiaomi-modules/src:all))',
        ],
        cwd=kp,
        capture=True,
    )
    ext_targets = [
        line.strip()
        for line in q_res.stdout.splitlines()
        if line.strip().startswith("//")
        and not line.strip().endswith("_config")
        and line.strip() not in EXCLUDED_BAZEL_TARGETS
    ]
    ext_targets.sort()
    all_targets = ["//soc-repo:pineapple_perf_dist"] + ext_targets
    print(f"Building //soc-repo:pineapple_perf_dist + {len(ext_targets)} purified external ddk_module targets...")

    run(
        [
            str(kp / "tools/bazel"),
            *common_flags,
            "build",
            f"--jobs={jobs}",
            "--config=stamp",
            "--incompatible_sandbox_hermetic_tmp=false",
            "--symlink_prefix=/",
            *override_flags,
            *all_targets,
        ],
        cwd=kp,
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

    return {"vermagic": vermagic, "depends": depends, "imports": imports, "exports": exports, "size": len(data)}


def load_vmlinux_symvers(path: Path) -> dict[str, int]:
    syms = {}
    for line in path.read_text().splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2:
            syms[parts[1]] = int(parts[0], 16) & 0xFFFFFFFF
    return syms


def stage_and_verify(kp5_dir: Path, staged_dir: Path) -> int:
    kp_dir = kp5_dir / "kernel_platform"
    common_dir = kp_dir / "common"
    soc_dist = kp_dir / "out/msm-kernel-pineapple-perf/dist"
    bin_dir = locate_bazel_bin(kp_dir, kp5_dir / "bazel-cache")

    common_status = subprocess.check_output(["git", "-C", str(common_dir), "status", "--porcelain"], text=True).strip()
    common_head = subprocess.check_output(["git", "-C", str(common_dir), "rev-parse", "HEAD"], text=True).strip()
    if common_status:
        raise RuntimeError(f"FATAL: common/ must remain 100% unmodified GKI 2.0, found dirty files:\n{common_status}")
    if common_head != EXPECTED_COMMON_COMMIT:
        print(f"WARNING: common/ HEAD {common_head} != pinned {EXPECTED_COMMON_COMMIT}", file=sys.stderr)

    sys_dlkm_dir = staged_dir / "system_dlkm"
    vendor_all_dir = staged_dir / "vendor_all"
    if staged_dir.exists():
        shutil.rmtree(staged_dir)
    sys_dlkm_dir.mkdir(parents=True)
    vendor_all_dir.mkdir(parents=True)

    pure_gki_cfg = bin_dir / "common/kernel_aarch64_config/out_dir/.config"
    core_copies = {
        "Image": soc_dist / "Image",
        "vmlinux.symvers": soc_dist / "vmlinux.symvers",
        "abi_symbollist": soc_dist / "abi_symbollist",
        "common.config": pure_gki_cfg,
        "pineapple_perf.config": soc_dist / ".config",
        "system_dlkm_staging_archive.tar.gz": soc_dist / "system_dlkm_staging_archive.tar.gz",
    }
    for dst_name, src_path in core_copies.items():
        if not src_path.is_file():
            raise FileNotFoundError(f"Missing core build artifact: {src_path}")
        shutil.copy2(src_path, staged_dir / dst_name)

    img_bytes = (staged_dir / "Image").read_bytes()
    m = re.search(rb"Linux version ([^\s]+)", img_bytes)
    image_release = m.group(1).decode("utf-8") if m else "UNKNOWN"
    if image_release != EXPECTED_RELEASE:
        raise RuntimeError(f"Image release mismatch: got {image_release}, expected {EXPECTED_RELEASE}")

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

    sources = [
        ("soc-repo", soc_dist),
        ("techpacks", bin_dir / "vendor"),
        ("xiaomi-modules", bin_dir / "xiaomi-modules"),
    ]
    found: dict[str, list[tuple[str, Path, Path]]] = {}
    for group, root in sources:
        for dirpath, _, files in os.walk(root):
            if any(s in dirpath for s in ("unstripped", "_staging", "intermediates", ".runfiles", "/staging/")):
                continue
            for fn in sorted(files):
                if not fn.endswith(".ko") or fn in EXCLUDED_VENDOR_MODULES:
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
        selected_vendor[fn] = {"group": winner[0], "rel_path": str(winner[1]), "size": dst.stat().st_size, "sha256": sha256_file(dst)}

    assert selected_vendor["qti_battery_charger.ko"]["group"] == "xiaomi-modules"
    assert selected_vendor["qcom-hv-haptics.ko"]["group"] == "xiaomi-modules"
    assert b"N3_PANEL_PA" in (vendor_all_dir / "msm_drm.ko").read_bytes()
    leaked_purified = sorted(FORBIDDEN_PURIFIED_MODULES & set(selected_vendor))
    assert not leaked_purified, f"Purified debug modules still present in vendor_all: {leaked_purified}"

    vmlinux_syms = load_vmlinux_symvers(staged_dir / "vmlinux.symvers")
    all_kos = [("system_dlkm", fn, sys_dlkm_dir / fn) for fn in sys_dlkm_kos] + [
        ("vendor_all", fn, vendor_all_dir / fn) for fn in sorted(selected_vendor)
    ]

    parsed = {}
    all_exports = dict(vmlinux_syms)
    export_owner = {sym: "vmlinux" for sym in vmlinux_syms}
    vermagics: dict[str | None, list[str]] = defaultdict(list)

    for bucket, fn, full_path in all_kos:
        info = parse_elf64_ko(full_path)
        parsed[(bucket, fn)] = info
        vermagics[info["vermagic"]].append(f"{bucket}/{fn}")
        for sym, crc in info["exports"].items():
            all_exports[sym] = crc
            export_owner[sym] = f"{bucket}/{fn}"

    missing_symbols = []
    crc_mismatches = []
    total_refs = 0
    for bucket, fn, _ in all_kos:
        for sym, req_crc in parsed[(bucket, fn)]["imports"].items():
            total_refs += 1
            if sym not in all_exports:
                missing_symbols.append({"module": f"{bucket}/{fn}", "symbol": sym, "crc": hex(req_crc)})
            elif all_exports[sym] != req_crc:
                crc_mismatches.append({"module": f"{bucket}/{fn}", "symbol": sym})

    verdict = (
        "PASS"
        if (len(vermagics) == 1 and EXPECTED_VERMAGIC in vermagics and not missing_symbols and not crc_mismatches)
        else "FAIL"
    )
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "common_commit": common_head,
        "common_dirty": 0,
        "image_release": image_release,
        "counts": {
            "system_dlkm": len(sys_dlkm_kos),
            "vendor_all": len(selected_vendor),
            "vendor_breakdown": dict(group_counts),
        },
        "elf_kmi_verification": {
            "total_versioned_symbol_refs": total_refs,
            "missing_symbol_count": len(missing_symbols),
            "crc_mismatch_count": len(crc_mismatches),
            "verdict": verdict,
        },
    }
    (staged_dir / "UNIFIED_MODULES_REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if verdict == "PASS" else 2


def main() -> int:
    default_kp5 = REPO_ROOT / "kernel-6.12/kp5"
    if not default_kp5.is_dir():
        default_kp5 = REPO_ROOT / "kp5"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kp5-dir", type=Path, default=default_kp5, help="KP5 workspace root")
    parser.add_argument("--staged-dir", type=Path, default=REPO_ROOT / "out-6.12/staged-modules", help="Staged output dir")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 8, help="Parallel Bazel/repo jobs")
    parser.add_argument("--sync-kp5", action="store_true", help="Sync KERNEL.PLATFORM.5.0 via repo before building")
    parser.add_argument("--wire-only", action="store_true", help="Only apply soc-repo patches and create vendor symlinks")
    parser.add_argument("--stage-only", action="store_true", help="Skip Bazel build and restage/verify existing outputs")
    args = parser.parse_args()

    kp5_dir = args.kp5_dir.resolve()
    staged_dir = args.staged_dir.resolve()

    if args.sync_kp5:
        sync_kp5(kp5_dir, args.jobs)
    ensure_soc_repo_patched(kp5_dir)
    wire_symlinks(kp5_dir)
    if args.wire_only:
        return 0
    if not args.stage_only:
        build_kleaf(kp5_dir, args.jobs)
    return stage_and_verify(kp5_dir, staged_dir)


if __name__ == "__main__":
    sys.exit(main())
