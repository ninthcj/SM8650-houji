#!/usr/bin/env python3
"""Assemble flashable Android 17 / GKI 2.0 (6.12) images for Xiaomi 14 (houji),
verify AVB chain, VINTF compatibility, depmod/softdep closure, KMI CRCs,
and run a live ARM64 QEMU kCFI module-loading smoke test (rootless).
"""

import argparse
from collections import defaultdict, deque
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
import uuid

KERNEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = KERNEL_DIR
PROJECT_ROOT = REPO_ROOT.parent
QEMU_IMAGE = "localhost/houji-qemu-test:latest"
QEMU_INIT_SRC = KERNEL_DIR / "qemu/init_modtest.c"
MODULE_MAP_PATH = KERNEL_DIR / "xiaomi-modules/MODULE_MAP.json"
MODULE_SIG_MARKER = b"~Module signature appended~\n"

BOOT_PARTITION_SIZE = 100663296
VENDOR_BOOT_PARTITION_SIZE = 100663296
DTBO_PARTITION_SIZE = 25165824
RECOVERY_PARTITION_SIZE = 104857600

BOOT_SALT = "0e36fbc78500a20db37872b0f0ade02bc8e0deb676b307737e15f1793db51ae7"
DEFAULT_SALT = "dd7d008a9feb34a85fd8ebbe8e3745231669829f90fb540076ff65c1d194e24e"
AVB_ROLLBACK_INDEX = "1793491200"
DEFAULT_FINGERPRINT = "Xiaomi/yaap_houji/houji:17/CP2A.260605.016/eng.android:user/release-keys"
FIXED_TIMESTAMP = "1230768000"

EXTRA_VENDOR_CMDLINE = "android_arch_task_struct_size=512 console=ramoops panic=5 qcom_scm.download_mode=0 fw_devlink.strict=0"

# Pruned GKI system_dlkm modules absent on houji hardware (tipc, tipc_diag, hci_uart retained).
SYSTEM_DLKM_PRUNE = frozenset({
    "gzvm", "kheaders", "exynos_hvm",
    "can", "can_raw", "can_bcm", "can_gw", "can_dev", "vcan", "slcan",
    "ieee802154", "ieee802154_socket", "ieee802154_6lowpan", "mac802154",
    "6lowpan", "nhc_dest", "nhc_fragment", "nhc_hop", "nhc_ipv6", "nhc_mobility", "nhc_routing", "nhc_udp",
    "9pnet", "9pnet_fd", "ptp_kvm", "vcpu_stall_detector",
    "virtio_balloon", "virtio_blk", "virtio_console", "virtio_pci",
    "virtio_pci_legacy_dev", "virtio_pci_modern_dev", "vmw_vsock_virtio_transport",
    "xhci_pci_renesas", "snd_hda_codec_hdmi", "usbmon",
})

# Keep USB Ethernet / USB-Serial OTG drivers inside system_dlkm.img (with depmod/alias for ueventd hotplug)
# while excluding them from boot-time modules.load so Ring-0 memory stays lean at boot.
SYSTEM_DLKM_HOTPLUG_ONLY = frozenset({
    "aqc111", "asix", "ax88179_178a", "cdc_acm", "cdc_eem", "cdc_ether", "cdc_ncm",
    "ftdi_sio", "mii", "r8152", "r8153_ecm", "rtl8150", "usbnet", "usbserial",
})

NON_PRODUCTION_TEST_MODULES = frozenset({
    "tornado_mod", "hdcp2p2_test", "seccam_test_driver", "si_core_test", "ipclite_test",
    "video", "qseecom_dlkm", "qcom_dynamic_ramoops", "qcom_dload_mode",
    "crm_v2", "clk_dummy", "radio_i2c_rtc6226_qca", "phy_qcom_ufs_qrbtc_sdm845",
})

STOCK_6_1_TO_6_12_MAP: dict[str, list[str]] = {
    "crm.ko": ["crm.ko"],
    "crypto-qti.ko": ["qcom_ice.ko", "ufshcd-crypto-qti.ko"],
    "ufs_qcom.ko": ["ufs-qcom.ko"],
    "phy-qcom-ufs-qmp-v4.ko": ["phy-qcom-ufs-qmp-v4-pineapple.ko"],
    "pmic_glink.ko": ["qti_pmic_glink.ko"],
    "ucsi_glink.ko": ["ucsi_qti_glink.ko"],
    "pwm-qti-lpg.ko": ["leds-qcom-lpg.ko"],
    "qcom_pm8008-regulator.ko": ["qcom-pm8008.ko", "qcom-pm8008-regulator.ko"],
    "qpnp-amoled-regulator.ko": ["qcom-amoled-regulator.ko"],
    "dynpf_scmi.ko": ["qti_dmof_scmi.ko"],
    "c1dcvs_scmi_v2.ko": ["c1dcvs_scmi_v2.ko", "qcom-dcvs.ko", "dcvs_fp.ko", "qcom_scmi_client.ko"],
    "mpam_arch.ko": ["mpam.ko", "cpu_mpam.ko", "platform_mpam.ko"],
    "mpam_policy.ko": ["mpam.ko", "cpu_mpam.ko", "platform_mpam.ko"],
    "spss_utils.ko": ["qcom_spss.ko"],
    "rpmh_master_stat.ko": ["qcom_stats.ko"],
    "diag.ko": ["tipc_diag.ko"],
}

ESSENTIAL_STAGE1_6_12 = [
    "qtee_shmbridge.ko", "crm.ko", "mi_md_kmsg.ko", "nvmem_qfprom.ko", "qti-fixed-regulator.ko",
    "llcc-qcom.ko", "pm8941-pwrkey.ko", "qcom_ice.ko", "ufshcd-crypto-qti.ko", "ufs-qcom.ko",
    "phy-qcom-ufs.ko", "phy-qcom-ufs-qmp-v4-pineapple.ko", "gcc-pineapple.ko", "tcsrcc-pineapple.ko",
    "pinctrl-pineapple.ko", "qnoc-pineapple.ko", "qcom_stats.ko", "qti_pmic_glink.ko",
    "ucsi_qti_glink.ko", "qcom-pm8008.ko", "qcom-pm8008-regulator.ko", "qcom-amoled-regulator.ko",
    "leds-qcom-lpg.ko", "qti_dmof_scmi.ko", "cpu_phys_log_map.ko", "deferred-free-helper.ko",
    "qcom_pd_mapper.ko", "qcom_pdr_msg.ko", "si_core_module.ko",
]

ESSENTIAL_RECOVERY_6_12 = [
    "msm_drm.ko", "qti_battery_charger.ko", "qcom-hv-haptics.ko", "mi_thermal_interface.ko",
    "hwid.ko", "dump_display.ko", "dwc3-msm.ko", "usb_f_ccid.ko", "usb_f_cdev.ko",
    "usb_f_gsi.ko", "usb_f_qdss.ko", "phy-msm-snps-eusb2.ko", "phy-msm-ssusb-qmp.ko",
    "phy-qcom-emu.ko",
]


def norm_mod(name: str) -> str:
    stem = Path(name).name
    if stem.endswith(".ko"):
        stem = stem[:-3]
    return stem.replace("-", "_")


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def find_llvm_strip(kp5_dir: Path) -> Path | None:
    kp_clang = kp5_dir / "kernel_platform/prebuilts/clang/host/linux-x86"
    if kp_clang.is_dir():
        for cand in sorted(kp_clang.glob("clang-*/bin/llvm-strip"), reverse=True):
            if cand.is_file():
                return cand
    for tool in ("llvm-strip", "aarch64-linux-gnu-strip"):
        hit = shutil.which(tool)
        if hit:
            return Path(hit)
    return None


def copy_ko_for_vendor(src: Path, dst: Path, strip_bin: Path | None) -> bool:
    shutil.copy2(src, dst)
    with dst.open("rb") as f:
        f.seek(0, os.SEEK_END)
        sz = f.tell()
        f.seek(max(0, sz - 64))
        tail = f.read(64)
    if MODULE_SIG_MARKER in tail:
        return False
    if strip_bin and strip_bin.is_file():
        subprocess.run([str(strip_bin), "--strip-debug", str(dst)], check=True)
        return True
    return False


def host_env(host_bin: Path, host_lib64: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{host_bin}:{env.get('PATH', '')}"
    prev_ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{host_lib64}:{prev_ld}".rstrip(":")
    return env


def run(cmd: list, cwd: Path | None = None, env: dict[str, str] | None = None, capture: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(
        [str(c) for c in cmd],
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )


def parse_elf64(data: bytes):
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("Not a little-endian ELF64 object")
    e_shoff = struct.unpack_from("<Q", data, 40)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 58)
    shstr_off, shstr_sz = struct.unpack_from("<QQ", data, e_shoff + e_shstrndx * e_shentsize + 24)
    shstr = data[shstr_off:shstr_off + shstr_sz]
    by_idx = []
    by_name = {}
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh_name = struct.unpack_from("<I", data, off)[0]
        sh_offset, sh_size = struct.unpack_from("<QQ", data, off + 24)
        sh_entsize = struct.unpack_from("<Q", data, off + 56)[0]
        end = shstr.index(b"\x00", sh_name)
        name = shstr[sh_name:end].decode("utf-8", "ignore")
        rec = (name, sh_offset, sh_size, sh_entsize)
        by_idx.append(rec)
        by_name[name] = rec
    return by_idx, by_name


def parse_ko_metadata(ko_path: Path) -> dict:
    data = ko_path.read_bytes()
    by_idx, by_name = parse_elf64(data)

    modinfo: dict[str, list[str]] = defaultdict(list)
    if ".modinfo" in by_name:
        _, off, sz, _ = by_name[".modinfo"]
        raw = data[off:off + sz]
        for entry in raw.split(b"\x00"):
            if not entry or b"=" not in entry:
                continue
            k, v = entry.split(b"=", 1)
            modinfo[k.decode("utf-8", "ignore")].append(v.decode("utf-8", "ignore"))

    depends = [item.strip() for d in modinfo.get("depends", []) for item in d.split(",") if item.strip()]

    softdep_pre: list[str] = []
    softdep_post: list[str] = []
    for sd in modinfo.get("softdep", []):
        mode = None
        for tok in sd.split():
            if tok == "pre:":
                mode = "pre"
            elif tok == "post:":
                mode = "post"
            elif mode == "pre":
                softdep_pre.append(tok)
            elif mode == "post":
                softdep_post.append(tok)

    exported_crcs: dict[str, int] = {}
    if ".symtab" in by_name and ".strtab" in by_name:
        _, sym_off, sym_sz, entsz = by_name[".symtab"]
        _, str_off, str_sz, _ = by_name[".strtab"]
        strtab = data[str_off:str_off + str_sz]
        for pos in range(sym_off, sym_off + sym_sz, entsz):
            st_name, _, _, st_shndx, st_value, _ = struct.unpack_from("<IBBHQQ", data, pos)
            end = strtab.index(b"\x00", st_name)
            name = strtab[st_name:end].decode("utf-8", "ignore")
            if name.startswith("__crc_"):
                if st_shndx == 0xFFF1:
                    crc = st_value & 0xFFFFFFFF
                elif st_shndx < len(by_idx):
                    _, sec_off, _, _ = by_idx[st_shndx]
                    crc = struct.unpack_from("<I", data, sec_off + st_value)[0]
                else:
                    continue
                exported_crcs[name[6:]] = crc

    req_versions: list[tuple[str, int]] = []
    if "__versions" in by_name:
        _, off, sz, _ = by_name["__versions"]
        for pos in range(off, off + sz, 64):
            crc = struct.unpack_from("<Q", data, pos)[0] & 0xFFFFFFFF
            name = data[pos + 8:pos + 64].split(b"\x00", 1)[0].decode("utf-8", "ignore")
            if name:
                req_versions.append((name, crc))

    vermagic = modinfo["vermagic"][0] if modinfo.get("vermagic") else ""
    return {
        "path": ko_path,
        "filename": ko_path.name,
        "norm": norm_mod(ko_path.name),
        "vermagic": vermagic,
        "depends": depends,
        "softdep_pre": softdep_pre,
        "softdep_post": softdep_post,
        "exported_crcs": exported_crcs,
        "req_versions": req_versions,
    }


def extract_image_release(image_path: Path) -> str:
    m = re.search(rb"Linux version (\d+\.\d+\.\d+[^\s]+)", image_path.read_bytes())
    if not m:
        raise ValueError(f"Could not find 'Linux version ...' banner in {image_path}")
    return m.group(1).decode("ascii")


def verify_kmi_and_vermagic(symvers_path: Path, ko_meta_by_group: dict[str, list[dict]], expected_release: str) -> dict:
    vmlinux_crcs: dict[str, int] = {}
    for line in symvers_path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            vmlinux_crcs[parts[1]] = int(parts[0], 16)

    mod_crcs: dict[str, int] = {}
    for metas in ko_meta_by_group.values():
        for m in metas:
            for sym, crc in m["exported_crcs"].items():
                mod_crcs.setdefault(sym, crc)

    vermagics: set[str] = set()
    mismatches = []
    missing = []
    group_stats = {}
    total_refs = 0

    for label, metas in ko_meta_by_group.items():
        refs = 0
        for m in metas:
            vermagics.add(m["vermagic"])
            for sym, req_crc in m["req_versions"]:
                refs += 1
                if sym in vmlinux_crcs:
                    if vmlinux_crcs[sym] != req_crc:
                        mismatches.append([label, m["filename"], sym, hex(req_crc), hex(vmlinux_crcs[sym])])
                elif sym in mod_crcs:
                    if mod_crcs[sym] != req_crc:
                        mismatches.append([label, m["filename"], sym, hex(req_crc), hex(mod_crcs[sym])])
                else:
                    missing.append([label, m["filename"], sym, hex(req_crc)])
        group_stats[label] = {"modules": len(metas), "symbol_refs": refs}
        total_refs += refs

    bad_vermagics = [v for v in sorted(vermagics) if not v.startswith(expected_release + " ")]
    return {
        "ok": (not mismatches and not missing and len(vermagics) == 1 and not bad_vermagics),
        "total_symbol_refs": total_refs,
        "unique_vermagics": sorted(vermagics),
        "bad_vermagics": bad_vermagics,
        "crc_mismatches_count": len(mismatches),
        "crc_mismatches_sample": mismatches[:20],
        "missing_symbols_count": len(missing),
        "missing_symbols_sample": missing[:20],
        "groups": group_stats,
    }


def build_sys_rel_path_map(sys_root: Path, release: str, kp5_dist_dir: Path) -> dict[str, Path]:
    rel_map: dict[str, Path] = {}
    for base in (sys_root / "lib/modules" / release, sys_root / release, sys_root):
        if (base / "kernel").is_dir():
            for ko in sorted((base / "kernel").rglob("*.ko")):
                rel_map[ko.relative_to(base).as_posix()] = ko
            if rel_map:
                return rel_map

    kp5_arch = kp5_dist_dir / "system_dlkm_staging_archive.tar.gz"
    name_to_rel: dict[str, str] = {}
    if kp5_arch.is_file():
        with tarfile.open(kp5_arch, "r:gz") as tf:
            for member in tf.getmembers():
                if member.isfile() and member.name.endswith(".ko") and "/kernel/" in member.name:
                    rel = member.name[member.name.index("kernel/"):]
                    name_to_rel[Path(rel).name] = rel

    for ko in sorted(sys_root.rglob("*.ko")):
        if "flatten" in ko.parts:
            continue
        rel_map[name_to_rel.get(ko.name, f"kernel/extra/{ko.name}")] = ko
    return rel_map


def resolve_inputs(mode: str, staged_dir: Path, kp5_dist_dir: Path, dt_dir: Path, work_dir: Path) -> dict:
    staged_ready = (
        (staged_dir / "Image").is_file()
        and (staged_dir / "vmlinux.symvers").is_file()
        and (staged_dir / "system_dlkm").is_dir()
        and (staged_dir / "vendor_all").is_dir()
        and any((staged_dir / "vendor_all").glob("*.ko"))
    )
    if mode == "staged" and not staged_ready:
        raise FileNotFoundError(f"--mode staged requested, but {staged_dir} is not populated yet")
    use_staged = (mode == "staged") or (mode == "auto" and staged_ready)

    if use_staged:
        image_path = staged_dir / "Image"
        symvers_path = staged_dir / "vmlinux.symvers"
        config_path = staged_dir / "common.config"
        if not config_path.is_file():
            config_path = staged_dir / ".config"
        if not config_path.is_file():
            config_path = kp5_dist_dir / ".config"
        release = extract_image_release(image_path)
        sys_rel_map = build_sys_rel_path_map(staged_dir / "system_dlkm", release, kp5_dist_dir)
        vendor_kos = sorted((staged_dir / "vendor_all").glob("*.ko"))
        resolved_mode = "staged"
    else:
        image_path = kp5_dist_dir / "Image"
        symvers_path = kp5_dist_dir / "vmlinux.symvers"
        config_path = kp5_dist_dir / ".config"
        release = extract_image_release(image_path)
        sys_extract = work_dir / "kp5_sys_extract"
        if sys_extract.exists():
            shutil.rmtree(sys_extract)
        sys_extract.mkdir(parents=True)
        with tarfile.open(kp5_dist_dir / "system_dlkm_staging_archive.tar.gz", "r:gz") as tf:
            tf.extractall(sys_extract, filter="tar")
        sys_rel_map = build_sys_rel_path_map(sys_extract, release, kp5_dist_dir)
        vendor_kos = sorted(kp5_dist_dir.glob("*.ko"))
        resolved_mode = "dry-run"

    return {
        "mode": resolved_mode,
        "release": release,
        "image": image_path,
        "symvers": symvers_path,
        "config": config_path,
        "dtb": dt_dir / "dtb.img",
        "dtbo": dt_dir / "dtbo.img",
        "sys_rel_map": sys_rel_map,
        "vendor_kos": vendor_kos,
    }


def run_depmod_flat(ko_paths: list[Path], symvers_path: Path, prefix: str, tmp_dir: Path) -> dict:
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    mod_dir = tmp_dir / "lib/modules/0.0" / prefix.strip("/")
    mod_dir.mkdir(parents=True)
    for p in ko_paths:
        shutil.copy2(p, mod_dir / p.name)

    proc = subprocess.run(["depmod", "-b", str(tmp_dir), "-E", str(symvers_path), "0.0"], text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"depmod failed (rc={proc.returncode}):\n{proc.stderr}\n{proc.stdout}")

    out_root = tmp_dir / "lib/modules/0.0"
    prefixed_dep_lines = []
    deps_graph: dict[str, list[str]] = {}
    for line in (out_root / "modules.dep").read_text().splitlines():
        if not line.strip() or ":" not in line:
            continue
        lhs, rhs = line.split(":", 1)
        lhs_fixed = "/" + lhs.strip().lstrip("/")
        rhs_toks = ["/" + t.strip().lstrip("/") for t in rhs.split() if t.strip()]
        prefixed_dep_lines.append(f"{lhs_fixed}: {' '.join(rhs_toks)}".rstrip())
        deps_graph[Path(lhs_fixed).name] = [Path(t).name for t in rhs_toks]

    return {
        "modules.dep": "\n".join(prefixed_dep_lines) + "\n",
        "modules.alias": (out_root / "modules.alias").read_text(),
        "modules.softdep": (out_root / "modules.softdep").read_text(),
        "deps_graph": deps_graph,
    }


def topo_sort_modules(
    selected_filenames: set[str],
    meta_by_name: dict[str, dict],
    deps_graph: dict[str, list[str]],
    order_hint: list[str],
) -> list[str]:
    norm_to_fn = {norm_mod(fn): fn for fn in selected_filenames}
    hint_rank = {fn: i for i, fn in enumerate(order_hint)}
    max_rank = len(order_hint) + 1

    prereqs: dict[str, set[str]] = {fn: set() for fn in selected_filenames}
    dependents: dict[str, set[str]] = {fn: set() for fn in selected_filenames}

    for fn in selected_filenames:
        for dep_fn in deps_graph.get(fn, []):
            if dep_fn in selected_filenames and dep_fn != fn:
                prereqs[fn].add(dep_fn)
                dependents[dep_fn].add(fn)
        if fn in ("camcc-pineapple.ko", "pci-msm-drv.ko") and "crm.ko" in selected_filenames:
            prereqs[fn].add("crm.ko")
            dependents["crm.ko"].add(fn)
        if fn in ("qcom-scm.ko", "smcinvoke_dlkm.ko", "qseecom_dlkm.ko") and "qtee_shmbridge.ko" in selected_filenames:
            prereqs[fn].add("qtee_shmbridge.ko")
            dependents["qtee_shmbridge.ko"].add(fn)
        if fn == "qcom-cpufreq-hw.ko":
            for sup in ("clk-qcom.ko", "clk-rpmh.ko", "gcc-pineapple.ko"):
                if sup in selected_filenames:
                    prereqs[fn].add(sup)
                    dependents[sup].add(fn)
        if fn == "qnoc-pineapple.ko":
            for sup in (
                "clk-qcom.ko", "clk-rpmh.ko", "gcc-pineapple.ko", "tcsrcc-pineapple.ko",
                "dispcc-pineapple.ko", "camcc-pineapple.ko", "gpucc-pineapple.ko", "videocc-pineapple.ko",
            ):
                if sup in selected_filenames:
                    prereqs[fn].add(sup)
                    dependents[sup].add(fn)
        if fn == "arm_smmu.ko" and "qnoc-pineapple.ko" in selected_filenames:
            prereqs[fn].add("qnoc-pineapple.ko")
            dependents["qnoc-pineapple.ko"].add(fn)
        if fn == "ufs-qcom.ko":
            for sup in (
                "qcom-cpufreq-hw.ko", "phy-qcom-ufs.ko", "phy-qcom-ufs-qmp-v4-pineapple.ko",
                "gcc-pineapple.ko", "tcsrcc-pineapple.ko", "gdsc-regulator.ko",
                "rpmh-regulator.ko", "arm_smmu.ko", "qnoc-pineapple.ko",
                "pinctrl-pineapple.ko", "qcom_ice.ko", "ufshcd-crypto-qti.ko",
            ):
                if sup in selected_filenames:
                    prereqs[fn].add(sup)
                    dependents[sup].add(fn)
        m = meta_by_name.get(fn)
        if m:
            for d in m["depends"] + m["softdep_pre"]:
                dfn = norm_to_fn.get(norm_mod(d))
                if dfn and dfn != fn:
                    prereqs[fn].add(dfn)
                    dependents[dfn].add(fn)
            for sp in m["softdep_post"]:
                sfn = norm_to_fn.get(norm_mod(sp))
                if sfn and sfn != fn:
                    prereqs[sfn].add(fn)
                    dependents[fn].add(sfn)

    in_deg = {fn: len(prereqs[fn]) for fn in selected_filenames}
    ready = sorted([fn for fn, deg in in_deg.items() if deg == 0], key=lambda fn: (hint_rank.get(fn, max_rank), fn))
    result: list[str] = []
    while ready:
        cur = ready.pop(0)
        result.append(cur)
        for nxt in dependents[cur]:
            in_deg[nxt] -= 1
            if in_deg[nxt] == 0:
                ready.append(nxt)
                ready.sort(key=lambda fn: (hint_rank.get(fn, max_rank), fn))

    if len(result) < len(selected_filenames):
        result.extend(sorted(selected_filenames - set(result), key=lambda fn: (hint_rank.get(fn, max_rank), fn)))
    return result


def write_fs_config(root_dir: Path, mount_point: str, out_path: Path) -> None:
    lines = [" 0 0 755 capabilities=0x0"]
    for p in sorted(root_dir.rglob("*")):
        rel = p.relative_to(root_dir).as_posix()
        mode = "755" if p.is_dir() else "644"
        lines.append(f"{mount_point}/{rel} 0 0 {mode} capabilities=0x0")
    out_path.write_text("\n".join(lines) + "\n")


def build_erofs_and_hashtree(
    stage_dir: Path,
    mount_point: str,
    out_img: Path,
    work_dir: Path,
    avb_props: list[str],
    host_bin: Path,
    file_contexts_bin: Path,
    fingerprint: str,
    env: dict[str, str],
) -> dict:
    fs_cfg = work_dir / f"{mount_point}_fs_config.txt"
    write_fs_config(stage_dir, mount_point, fs_cfg)
    fs_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{mount_point}-{fingerprint}"))

    if out_img.exists():
        out_img.unlink()

    run(
        [
            host_bin / "mkfs.erofs", "-z", "lz4hc,9", "-b", "4096", "-C", "4096",
            "--chunksize", "4096", "--mount-point", mount_point, "--fs-config-file", fs_cfg,
            "--file-contexts", file_contexts_bin, "-T", FIXED_TIMESTAMP, "-U", fs_uuid,
            out_img, stage_dir,
        ],
        env=env,
    )
    erofs_size = out_img.stat().st_size
    run([host_bin / "fsck.erofs", out_img], env=env)

    cmd = [
        host_bin / "avbtool", "add_hashtree_footer", "--image", out_img,
        "--partition_size", "0", "--partition_name", mount_point,
        "--hash_algorithm", "sha1", "--fec_num_roots", "2", "--salt", DEFAULT_SALT,
    ]
    for prop in avb_props:
        cmd.extend(["--prop", prop])
    run(cmd, env=env)
    run([host_bin / "fsck.erofs", out_img], env=env)

    raw_erofs = out_img.read_bytes()[:erofs_size]
    expected_ctx = b"u:object_r:system_dlkm_file:s0" if mount_point == "system_dlkm" else b"u:object_r:vendor_file:s0"
    selinux_ok = (expected_ctx in raw_erofs) and (b"u:object_r:unlabeled:s0" not in raw_erofs)
    if not selinux_ok:
        raise RuntimeError(f"SELinux context verification failed on {out_img}")

    return {
        "path": str(out_img),
        "erofs_bytes": erofs_size,
        "final_bytes": out_img.stat().st_size,
        "sha256": sha256_file(out_img),
        "uuid": fs_uuid,
        "fsck_erofs_ok": True,
        "selinux_context": expected_ctx.decode(),
        "selinux_xattrs_ok": selinux_ok,
    }


def extract_templates(template_dir: Path, work_dir: Path, host_bin: Path, env: dict[str, str]) -> dict:
    gdir = work_dir / "templates"
    if gdir.exists():
        shutil.rmtree(gdir)
    for sub in ("vendor_boot", "vendor_ramdisk", "system_dlkm", "vendor_dlkm"):
        (gdir / sub).mkdir(parents=True)

    vb_info = run(
        [host_bin / "unpack_bootimg", "--boot_img", template_dir / "vendor_boot.img", "--out", gdir / "vendor_boot"],
        env=env,
        capture=True,
    ).stdout

    m_cmd = re.search(r"^vendor command line args:\s*(.*)$", vb_info, re.M)
    if not m_cmd:
        raise ValueError("Could not parse vendor command line from template vendor_boot.img")

    lz4_proc = subprocess.Popen([str(host_bin / "lz4"), "-dc", str(gdir / "vendor_boot/vendor_ramdisk00")], stdout=subprocess.PIPE)
    cpio_proc = subprocess.Popen(["cpio", "-idmu", "--quiet"], cwd=gdir / "vendor_ramdisk", stdin=lz4_proc.stdout, stderr=subprocess.PIPE)
    assert lz4_proc.stdout is not None
    lz4_proc.stdout.close()
    cpio_proc.communicate()
    lz4_proc.wait()

    run([host_bin / "fsck.erofs", f"--extract={gdir / 'system_dlkm'}", template_dir / "system_dlkm.img"], env=env)
    run([host_bin / "fsck.erofs", f"--extract={gdir / 'vendor_dlkm'}", template_dir / "vendor_dlkm.img"], env=env)

    return {
        "dir": gdir,
        "vendor_cmdline": m_cmd.group(1).strip(),
        "bootconfig": gdir / "vendor_boot/bootconfig",
        "vendor_ramdisk_root": gdir / "vendor_ramdisk",
        "system_dlkm_etc": gdir / "system_dlkm/etc",
        "vendor_dlkm_etc": gdir / "vendor_dlkm/etc",
        "vendor_blocklist": gdir / "vendor_dlkm/lib/modules/modules.blocklist",
        "sys_blocklist": gdir / "vendor_dlkm/lib/modules/system_dlkm.modules.blocklist",
    }


def parse_blocklist(path: Path) -> set[str]:
    out = set()
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("blocklist "):
            out.add(norm_mod(line.split()[1]))
    return out


def _cpio_pad4(n: int) -> int:
    return (4 - (n % 4)) % 4


def _make_cpio_newc_entry(name: str, data: bytes, ino: int, mode: int = 0o100644, nlink: int = 1) -> bytes:
    name_b = name.encode("utf-8") + b"\x00"
    hdr = (
        f"070701{ino:08x}{mode:08x}{0:08x}{0:08x}{nlink:08x}{0:08x}{len(data):08x}"
        f"{0:08x}{0:08x}{0:08x}{0:08x}{len(name_b):08x}{0:08x}"
    ).encode("ascii")
    out = bytearray(hdr)
    out.extend(name_b)
    out.extend(b"\x00" * _cpio_pad4(len(hdr) + len(name_b)))
    out.extend(data)
    out.extend(b"\x00" * _cpio_pad4(len(data)))
    return bytes(out)


def rewrite_recovery_cpio(src_cpio: Path, dst_cpio: Path, new_mod_dir: Path) -> dict:
    raw = src_cpio.read_bytes()
    pos, total = 0, len(raw)
    out = bytearray()
    max_ino, kept_entries, dropped_old_mod_entries = 1000, 0, 0
    saw_lib_modules_dir = False

    while pos + 110 <= total:
        if raw[pos:pos + 6] != b"070701":
            raise ValueError(f"Unsupported CPIO magic at offset {pos}")
        ino = int(raw[pos + 6:pos + 14], 16)
        filesize = int(raw[pos + 54:pos + 62], 16)
        namesize = int(raw[pos + 94:pos + 102], 16)
        max_ino = max(max_ino, ino)
        name_end = pos + 110 + namesize
        name = raw[pos + 110:name_end - 1].decode("utf-8", "surrogateescape")
        data_start = name_end + _cpio_pad4(110 + namesize)
        next_pos = data_start + filesize + _cpio_pad4(filesize)
        if name == "TRAILER!!!":
            break
        norm_name = name.lstrip("./")
        if norm_name == "lib/modules":
            saw_lib_modules_dir = True
            out.extend(raw[pos:next_pos])
            kept_entries += 1
        elif norm_name.startswith("lib/modules/"):
            dropped_old_mod_entries += 1
        else:
            out.extend(raw[pos:next_pos])
            kept_entries += 1
        pos = next_pos

    if not saw_lib_modules_dir:
        max_ino += 1
        out.extend(_make_cpio_newc_entry("lib/modules", b"", ino=max_ino, mode=0o040755, nlink=2))

    added_files = 0
    for p in sorted(new_mod_dir.iterdir()):
        if not p.is_file() or p.name == "modules.load":
            continue
        max_ino += 1
        out.extend(_make_cpio_newc_entry(f"lib/modules/{p.name}", p.read_bytes(), ino=max_ino, mode=0o100644))
        added_files += 1

    out.extend(_make_cpio_newc_entry("TRAILER!!!", b"", ino=0, mode=0, nlink=1))
    rem = len(out) % 512
    if rem:
        out.extend(b"\x00" * (512 - rem))
    dst_cpio.write_bytes(bytes(out))
    return {
        "kept_rootfs_entries": kept_entries,
        "dropped_old_module_entries": dropped_old_mod_entries,
        "added_6_12_module_entries": added_files,
        "cpio_bytes": len(out),
    }


def run_qemu_kcfi_smoke_test(
    out_images_dir: Path,
    work_dir: Path,
    release: str,
    host_bin: Path,
    env: dict[str, str],
    exported_by_mod: dict[str, set[str]],
) -> dict:
    qwork = work_dir / "qemu_smoke"
    if qwork.exists():
        shutil.rmtree(qwork)
    qwork.mkdir(parents=True)

    boot_unpack = qwork / "boot_unpack"
    boot_unpack.mkdir()
    run([host_bin / "unpack_bootimg", "--boot_img", out_images_dir / "boot.img", "--out", boot_unpack], env=env)

    ext_sys, ext_vdlkm, ext_vb, ext_vram = qwork / "ext_sys", qwork / "ext_vdlkm", qwork / "ext_vb", qwork / "ext_vram"
    for d in (ext_sys, ext_vdlkm, ext_vb, ext_vram):
        d.mkdir()
    run([host_bin / "fsck.erofs", f"--extract={ext_sys}", out_images_dir / "system_dlkm.img"], env=env)
    run([host_bin / "fsck.erofs", f"--extract={ext_vdlkm}", out_images_dir / "vendor_dlkm.img"], env=env)
    run([host_bin / "unpack_bootimg", "--boot_img", out_images_dir / "vendor_boot.img", "--out", ext_vb], env=env)
    p1 = subprocess.Popen([str(host_bin / "lz4"), "-dc", str(ext_vb / "vendor_ramdisk00")], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["cpio", "-idmu", "--quiet"], cwd=ext_vram, stdin=p1.stdout, stderr=subprocess.PIPE)
    assert p1.stdout is not None
    p1.stdout.close()
    p2.communicate()
    p1.wait()

    initrd_root = qwork / "initrd_root"
    for sub in ("etc", "mods/sys", "mods/vram", "mods/vdlkm"):
        (initrd_root / sub).mkdir(parents=True)

    order_lines: list[str] = []
    seen_mods: set[str] = set()

    sys_mod_root = ext_sys / "lib/modules" / release
    for line in (sys_mod_root / "modules.load").read_text().splitlines():
        rel = line.strip()
        if not rel:
            continue
        src = sys_mod_root / rel
        if src.is_file() and norm_mod(src.name) not in seen_mods:
            seen_mods.add(norm_mod(src.name))
            shutil.copy2(src, initrd_root / "mods/sys" / src.name)
            order_lines.append(f"system_dlkm /mods/sys/{src.name}")

    vram_mod_dir = ext_vram / "lib/modules"
    vram_block = parse_blocklist(vram_mod_dir / "modules.blocklist")
    for load_name in ("modules.load", "modules.load.recovery"):
        lf = vram_mod_dir / load_name
        if not lf.is_file():
            continue
        for line in lf.read_text().splitlines():
            fn = Path(line.strip()).name
            if not fn or not fn.endswith(".ko"):
                continue
            nm = norm_mod(fn)
            if nm in vram_block or nm in seen_mods:
                continue
            src = vram_mod_dir / fn
            if src.is_file():
                seen_mods.add(nm)
                shutil.copy2(src, initrd_root / "mods/vram" / fn)
                order_lines.append(f"vendor_ramdisk /mods/vram/{fn}")

    vdlkm_mod_dir = ext_vdlkm / "lib/modules"
    vdlkm_block = parse_blocklist(vdlkm_mod_dir / "modules.blocklist")
    for line in (vdlkm_mod_dir / "modules.load").read_text().splitlines():
        fn = Path(line.strip()).name
        if not fn or not fn.endswith(".ko"):
            continue
        nm = norm_mod(fn)
        if nm in vdlkm_block or nm in seen_mods:
            continue
        src = vdlkm_mod_dir / fn
        if src.is_file():
            seen_mods.add(nm)
            shutil.copy2(src, initrd_root / "mods/vdlkm" / fn)
            order_lines.append(f"vendor_dlkm /mods/vdlkm/{fn}")

    (initrd_root / "etc/modtest_order.txt").write_text("\n".join(order_lines) + "\n")
    shutil.copy2(QEMU_INIT_SRC, qwork / "init_modtest.c")

    runner_script = r"""
set -euo pipefail
aarch64-linux-gnu-gcc -O2 -static -Wall -Wextra /qwork/init_modtest.c -o /qwork/initrd_root/init
chmod 0755 /qwork/initrd_root/init
(cd /qwork/initrd_root && find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --owner=0:0 2>/dev/null | gzip -1 > /qwork/initrd.cpio.gz)
timeout 180 qemu-system-aarch64 \
    -M virt -cpu max -smp 4 -m 2048 -nographic -no-reboot -nic none \
    -kernel /qwork/boot_unpack/kernel \
    -initrd /qwork/initrd.cpio.gz \
    -append "console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 panic=-1" \
    > /qwork/qemu_serial.log 2>&1 || true
"""
    run(["podman", "run", "--rm", "--userns=keep-id", "-v", f"{qwork}:/qwork:Z", QEMU_IMAGE, "bash", "-lc", runner_script])

    log_path = out_images_dir / "qemu_kcfi_smoke.log"
    shutil.copy2(qwork / "qemu_serial.log", log_path)
    log_text = log_path.read_text(errors="replace")

    booted_banner = f"Linux version {release}" in log_text
    done_marker = "=== [MODTEST] DONE ===" in log_text
    fatal_hits: dict[str, list[str]] = {}
    for key, pat in {
        "cfi_failure": r"CFI failure",
        "kernel_panic": r"Kernel panic",
        "oops_or_internal_error": r"Internal error:|Oops:|Unable to handle kernel",
        "kernel_bug": r"kernel BUG at|BUG: failure",
        "kernel_warn_or_trace": r"Call trace:|WARNING:",
        "crc_mismatch": r"disagrees about version of symbol",
        "invalid_format": r"Invalid module format|Exec format error",
    }.items():
        hits = [ln for ln in log_text.splitlines() if re.search(pat, ln)]
        if hits:
            fatal_hits[key] = hits[:10]

    failed_init_mods: set[str] = set()
    for ln in log_text.splitlines():
        m_ie = re.search(r"\[MODTEST\] INIT_ERR .*path=/mods/\w+/([^\s]+)\.ko errno=(\d+)", ln)
        if m_ie:
            failed_init_mods.add(norm_mod(m_ie.group(1)))

    unloaded_parent_syms: set[str] = set()
    for nm in failed_init_mods:
        unloaded_parent_syms.update(exported_by_mod.get(nm, set()))

    cascaded_unknown, unexplained_unknown = [], []
    for ln in log_text.splitlines():
        m_unk = re.search(r"Unknown symbol (\S+) \(err", ln)
        if m_unk:
            (cascaded_unknown if m_unk.group(1) in unloaded_parent_syms else unexplained_unknown).append(ln)
    if unexplained_unknown:
        fatal_hits["unexplained_unknown_symbol"] = unexplained_unknown[:20]

    summary_m = re.search(r"\[MODTEST\] SUMMARY total=(\d+) ok=(\d+) already=(\d+) init_err=(\d+) format_err=(\d+) other_err=(\d+)", log_text)
    summary = (
        {
            "total_attempted": int(summary_m.group(1)),
            "loaded_ok": int(summary_m.group(2)),
            "already_loaded": int(summary_m.group(3)),
            "hw_absent_init_err": int(summary_m.group(4)),
            "format_or_sig_err": int(summary_m.group(5)),
            "other_err": int(summary_m.group(6)),
        }
        if summary_m
        else {}
    )
    lsmod_m = re.search(r"\[LSMOD\] TOTAL_LINES=(\d+)", log_text)
    ok = (
        booted_banner and done_marker and not fatal_hits and not unexplained_unknown
        and summary.get("format_or_sig_err", -1) == 0
        and summary.get("other_err", -1) == 0
        and summary.get("loaded_ok", 0) > 0
    )
    return {
        "ok": ok,
        "container_image": QEMU_IMAGE,
        "log_file": str(log_path),
        "booted_expected_release": booted_banner,
        "completed_cleanly": done_marker,
        "proc_modules_loaded_count": int(lsmod_m.group(1)) if lsmod_m else 0,
        "summary": summary,
        "hw_absent_failed_modules": sorted(failed_init_mods),
        "cascaded_unknown_symbols_from_hw_absent_parents": len(cascaded_unknown),
        "unexplained_unknown_symbols": len(unexplained_unknown),
        "fatal_dmesg_hits": fatal_hits,
    }


def _default_rom_dir() -> Path:
    for cand in (PROJECT_ROOT / "yaap-houji/yaap", PROJECT_ROOT / "yaap", REPO_ROOT / "yaap"):
        if cand.is_dir():
            return cand
    return PROJECT_ROOT / "rom"


def _default_kp5_dir() -> Path:
    for cand in (REPO_ROOT / "kernel-6.12/kp5", REPO_ROOT / "kp5", PROJECT_ROOT / "kp5"):
        if cand.is_dir():
            return cand
    return REPO_ROOT / "kp5"


def _default_keys_dir() -> Path:
    for cand in (PROJECT_ROOT / "keys", REPO_ROOT / "keys"):
        if cand.is_dir():
            return cand
    return PROJECT_ROOT / "keys"


def _discover_target_files_dir(rom_dir: Path) -> Path:
    inter = rom_dir / "out/target/product/houji/obj/PACKAGING/target_files_intermediates"
    preferred = inter / "yaap_houji-target_files"
    if preferred.is_dir():
        return preferred
    if inter.is_dir():
        matches = sorted(p for p in inter.glob("*_houji-target_files") if p.is_dir())
        if matches:
            return matches[0]
    return preferred


def _detect_fingerprint(target_files_dir: Path) -> str:
    for rel in ("VENDOR/build.prop", "SYSTEM/build.prop"):
        prop = target_files_dir / rel
        if not prop.is_file():
            continue
        for key in ("ro.vendor.build.fingerprint", "ro.system.build.fingerprint", "ro.build.fingerprint"):
            prefix = f"{key}="
            for line in prop.read_text(errors="replace").splitlines():
                if line.startswith(prefix):
                    val = line[len(prefix):].strip()
                    if val:
                        return val
    return DEFAULT_FINGERPRINT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["auto", "staged", "dry-run"], default="staged")
    parser.add_argument("--skip-qemu", action="store_true", help="Skip live QEMU smoke test")
    parser.add_argument(
        "--rom-dir", "--yaap-dir", dest="yaap_dir", type=Path, default=_default_rom_dir(),
        help="Synced Android ROM build root (YAAP, LineageOS, AOSP, etc.)",
    )
    parser.add_argument("--kp5-dir", type=Path, default=_default_kp5_dir(), help="KP5 workspace root")
    parser.add_argument("--staged-dir", type=Path, default=REPO_ROOT / "out-6.12/staged-modules", help="Staged 6.12 artifacts dir")
    parser.add_argument("--dt-dir", type=Path, default=KERNEL_DIR / "devicetree-houji/out", help="Built DT output dir")
    parser.add_argument("--keys-dir", type=Path, default=_default_keys_dir(), help="Signing keys dir containing avb.pem")
    parser.add_argument("--target-files", type=Path, help="Extracted Android target_files intermediate dir")
    parser.add_argument("--template-dir", type=Path, help="Dir containing baseline vendor_boot/recovery/system_dlkm/vendor_dlkm images")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "out-6.12/images", help="Output images dir")
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "out-6.12/work", help="Scratch work dir")
    parser.add_argument("--fingerprint", default=None, help="Build fingerprint embedded in AVB properties (auto-detected from target_files)")
    args = parser.parse_args()

    yaap_dir = args.yaap_dir.resolve()
    kp5_dir = args.kp5_dir.resolve()
    staged_dir = args.staged_dir.resolve()
    dt_dir = args.dt_dir.resolve()
    keys_dir = args.keys_dir.resolve()
    target_files_dir = (
        args.target_files
        or _discover_target_files_dir(yaap_dir)
    ).resolve()
    args.fingerprint = args.fingerprint or _detect_fingerprint(target_files_dir)
    template_dir = (args.template_dir or (target_files_dir / "IMAGES")).resolve()
    out_images_dir = args.out_dir.resolve()
    work_dir = args.work_dir.resolve()

    host_bin = yaap_dir / "out/host/linux-x86/bin"
    host_lib64 = yaap_dir / "out/host/linux-x86/lib64"
    file_contexts_bin = target_files_dir / "META/file_contexts.bin"
    avb_key_pem = keys_dir / "avb.pem"
    kp5_dist_dir = kp5_dir / "kernel_platform/out/msm-kernel-pineapple-perf/dist"
    strip_bin = find_llvm_strip(kp5_dir)

    env = host_env(host_bin, host_lib64)
    out_images_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Extract AVB public key from user-supplied avb.pem
    pub_bin = work_dir / "avb_pubkey.bin"
    pub_pem = work_dir / "avb_pubkey.pem"
    run([host_bin / "avbtool", "extract_public_key", "--key", avb_key_pem, "--output", pub_bin], env=env)
    pub_sha1 = hashlib.sha1(pub_bin.read_bytes()).hexdigest()
    pub_pem.write_bytes(subprocess.run(["openssl", "rsa", "-in", str(avb_key_pem), "-pubout"], check=True, capture_output=True).stdout)

    # 2. Resolve inputs & baseline templates
    inp = resolve_inputs(args.mode, staged_dir, kp5_dist_dir, dt_dir, work_dir)
    release = inp["release"]
    print(f"=== Assembling 6.12 images [mode={inp['mode']}, release={release}] ===", flush=True)
    tmpl = extract_templates(template_dir, work_dir, host_bin, env)
    mod_map = json.loads(MODULE_MAP_PATH.read_text())

    cfg_text = inp["config"].read_text()
    pstore_ram_builtin = "CONFIG_PSTORE_RAM=y" in cfg_text
    pstore_ram_module = "CONFIG_PSTORE_RAM=m" in cfg_text
    cfi_strict = ("CONFIG_CFI_CLANG=y" in cfg_text) and ("# CONFIG_CFI_PERMISSIVE is not set" in cfg_text)

    # 3. Parse ELF module metadata & filter dead/non-production modules
    raw_sys_metas = [parse_ko_metadata(p) for p in inp["sys_rel_map"].values()]
    raw_vendor_metas = [parse_ko_metadata(p) for p in inp["vendor_kos"]]

    all_non_pruned_deps: set[str] = set()
    for m in raw_sys_metas:
        if m["norm"] not in SYSTEM_DLKM_PRUNE:
            all_non_pruned_deps.update(norm_mod(d) for d in m["depends"])
    for m in raw_vendor_metas:
        all_non_pruned_deps.update(norm_mod(d) for d in m["depends"])

    sys_pruned_rel_map: dict[str, Path] = {}
    pruned_sys_names: list[str] = []
    for rel, src_p in sorted(inp["sys_rel_map"].items()):
        nm = norm_mod(src_p.name)
        if nm in SYSTEM_DLKM_PRUNE and nm not in all_non_pruned_deps:
            pruned_sys_names.append(src_p.name)
        else:
            sys_pruned_rel_map[rel] = src_p

    sys_metas = [parse_ko_metadata(p) for p in sys_pruned_rel_map.values()]
    sys_names_set = {m["filename"] for m in sys_metas}
    if "tipc.ko" not in sys_names_set or "tipc_diag.ko" not in sys_names_set:
        raise ValueError("Missing required tipc/tipc_diag in system_dlkm!")

    pruned_vendor_norms = {norm_mod(k) for k, v in mod_map.get("vendor_modules", {}).items() if v.get("origin") == "prune_dead_bloat"}
    tmpl_block_norms = parse_blocklist(tmpl["vendor_blocklist"])

    vendor_metas_active, vendor_pruned_skipped, stub_panic_pruned = [], [], []
    for m in raw_vendor_metas:
        if b"is stubbed and should not be initialized" in m["path"].read_bytes():
            stub_panic_pruned.append(m["filename"])
        elif m["norm"] in pruned_vendor_norms or m["norm"] in NON_PRODUCTION_TEST_MODULES:
            vendor_pruned_skipped.append(m["filename"])
        else:
            vendor_metas_active.append(m)

    kmi_report = verify_kmi_and_vermagic(inp["symvers"], {"system_dlkm": sys_metas, "vendor_all": vendor_metas_active}, release)
    if not kmi_report["ok"]:
        raise RuntimeError(f"KMI/vermagic verification failed: {json.dumps(kmi_report, indent=2)}")

    # 4. Stage-1 & Recovery transitive closure
    all_pool_metas = {m["filename"]: m for m in vendor_metas_active}
    sys_meta_by_name = {m["filename"]: m for m in sys_metas}
    combined_pool = {**sys_meta_by_name, **all_pool_metas}
    norm_to_pool_fn = {norm_mod(fn): fn for fn in combined_pool}
    full_depmod = run_depmod_flat([m["path"] for m in combined_pool.values()], inp["symvers"], "/lib/modules", work_dir / "depmod_full_pool")

    def map_stock_list(stock_names: list[str]) -> list[str]:
        mapped = []
        for s in stock_names:
            for t in STOCK_6_1_TO_6_12_MAP.get(s, [s]):
                fn = norm_to_pool_fn.get(norm_mod(t))
                if fn and fn not in mapped:
                    mapped.append(fn)
        return mapped

    early_wdt_priority = [
        "qcom_hwspinlock.ko", "smem.ko", "qcom-scm.ko",
        "qcom-spmi-pmic.ko", "spmi-pmic-arb.ko", "nvmem_qcom-spmi-sdam.ko",
        "gh_ctrl.ko", "gh_msgq.ko", "gh_dbl.ko", "gh_arm_drv.ko", "gh_rm_drv.ko",
        "minidump.ko", "qcom_wdt_core.ko", "gh_virt_wdt.ko", "mi_md_kmsg.ko",
        "gh_panic_notifier.ko", "dmesg_dumper.ko",
    ]
    stage1_seeds: list[str] = []
    for fn in early_wdt_priority:
        pfn = norm_to_pool_fn.get(norm_mod(fn))
        if pfn:
            for dep in full_depmod["deps_graph"].get(pfn, []):
                if dep in combined_pool and dep not in stage1_seeds:
                    stage1_seeds.append(dep)
            if pfn not in stage1_seeds:
                stage1_seeds.append(pfn)
    for fn in map_stock_list(mod_map["load_lists"]["stage1_vendor_ramdisk"]):
        if fn not in stage1_seeds:
            stage1_seeds.append(fn)
    for fn in ESSENTIAL_STAGE1_6_12:
        pfn = norm_to_pool_fn.get(norm_mod(fn))
        if pfn and pfn not in stage1_seeds:
            stage1_seeds.append(pfn)
    if pstore_ram_module and norm_to_pool_fn.get("ramoops"):
        stage1_seeds.insert(0, norm_to_pool_fn["ramoops"])

    recovery_seeds = list(stage1_seeds)
    for fn in map_stock_list(mod_map["load_lists"]["stage1_vendor_ramdisk_recovery"]):
        if fn not in recovery_seeds:
            recovery_seeds.append(fn)
    for fn in ESSENTIAL_RECOVERY_6_12:
        pfn = norm_to_pool_fn.get(norm_mod(fn))
        if pfn and pfn not in recovery_seeds:
            recovery_seeds.append(pfn)

    def close_transitive(seeds: list[str]) -> set[str]:
        closed: set[str] = set()
        q: deque[str] = deque()
        for s in seeds:
            if s in combined_pool and norm_mod(s) not in tmpl_block_norms:
                closed.add(s)
                q.append(s)
        while q:
            cur = q.popleft()
            m = combined_pool[cur]
            neighbors = list(full_depmod["deps_graph"].get(cur, []))
            for d in m["depends"] + m["softdep_pre"] + m["softdep_post"]:
                dfn = norm_to_pool_fn.get(norm_mod(d))
                if dfn:
                    neighbors.append(dfn)
            for nxt in neighbors:
                if nxt in combined_pool and nxt not in closed and norm_mod(nxt) not in tmpl_block_norms:
                    closed.add(nxt)
                    q.append(nxt)
        return closed

    stage1_closed = close_transitive(stage1_seeds)
    recovery_closed = close_transitive(recovery_seeds)
    vendor_ramdisk_set = stage1_closed | recovery_closed

    vram_depmod = run_depmod_flat([combined_pool[fn]["path"] for fn in sorted(vendor_ramdisk_set)], inp["symvers"], "/lib/modules", work_dir / "depmod_vendor_ramdisk")
    vram_unresolved = []
    vram_norms = {norm_mod(fn) for fn in vendor_ramdisk_set}
    for fn in sorted(vendor_ramdisk_set):
        m = combined_pool[fn]
        for dep in m["depends"]:
            if norm_mod(dep) not in vram_norms:
                vram_unresolved.append({"module": fn, "kind": "hard_dep", "target": dep})
        for kind, lst in (("softdep_pre", m["softdep_pre"]), ("softdep_post", m["softdep_post"])):
            for sd in lst:
                sn = norm_mod(sd)
                if sn in norm_to_pool_fn and sn not in tmpl_block_norms and sn not in vram_norms:
                    vram_unresolved.append({"module": fn, "kind": kind, "target": sd})
    if vram_unresolved:
        raise RuntimeError(f"Unresolved stage-1 vendor_ramdisk deps: {vram_unresolved[:10]}")

    stage1_load_order = topo_sort_modules(stage1_closed, combined_pool, vram_depmod["deps_graph"], stage1_seeds)
    recovery_load_order = [fn for fn in topo_sort_modules(recovery_closed, combined_pool, vram_depmod["deps_graph"], recovery_seeds) if fn != "boot_stats.ko"]

    def assert_crm_before_consumers(order: list[str], label: str) -> None:
        if "crm.ko" not in order:
            return
        crm_idx = order.index("crm.ko")
        for consumer in ("camcc-pineapple.ko", "pci-msm-drv.ko"):
            if consumer in order and order.index(consumer) < crm_idx:
                raise RuntimeError(f"Ordering violation in {label}: {consumer} appears before crm.ko!")

    assert_crm_before_consumers(stage1_load_order, "vendor_ramdisk modules.load")
    assert_crm_before_consumers(recovery_load_order, "vendor_ramdisk modules.load.recovery")

    # 5. Build boot.img, dtbo.img, vendor_boot.img, recovery.img
    boot_out = out_images_dir / "boot.img"
    run([host_bin / "mkbootimg", "--header_version", "4", "--os_version", "17.0.0", "--os_patch_level", "2026-11", "--kernel", inp["image"], "--cmdline", "", "--output", boot_out], env=env)
    boot_orig_size = boot_out.stat().st_size
    run([
        host_bin / "avbtool", "add_hash_footer", "--image", boot_out, "--partition_size", str(BOOT_PARTITION_SIZE),
        "--partition_name", "boot", "--algorithm", "SHA256_RSA4096", "--key", avb_key_pem,
        "--hash_algorithm", "sha256", "--salt", BOOT_SALT, "--rollback_index", AVB_ROLLBACK_INDEX,
        "--prop", "com.android.build.boot.os_version:17",
        "--prop", f"com.android.build.boot.fingerprint:{args.fingerprint}",
        "--prop", "com.android.build.boot.security_patch:2024-02-29",
    ], env=env)

    dtbo_out = out_images_dir / "dtbo.img"
    shutil.copy2(inp["dtbo"], dtbo_out)
    dtbo_orig_size = dtbo_out.stat().st_size
    run([
        host_bin / "avbtool", "add_hash_footer", "--image", dtbo_out, "--partition_size", str(DTBO_PARTITION_SIZE),
        "--partition_name", "dtbo", "--algorithm", "SHA256_RSA4096", "--key", avb_key_pem,
        "--hash_algorithm", "sha256", "--salt", DEFAULT_SALT, "--rollback_index", AVB_ROLLBACK_INDEX,
        "--prop", f"com.android.build.dtbo.fingerprint:{args.fingerprint}",
    ], env=env)

    vram_stage = work_dir / "vendor_ramdisk_stage"
    if vram_stage.exists():
        shutil.rmtree(vram_stage)
    shutil.copytree(tmpl["vendor_ramdisk_root"], vram_stage)
    vram_mod_dir = vram_stage / "lib/modules"
    shutil.rmtree(vram_mod_dir)
    vram_mod_dir.mkdir(parents=True)
    for fn in sorted(vendor_ramdisk_set):
        copy_ko_for_vendor(combined_pool[fn]["path"], vram_mod_dir / fn, strip_bin)
    (vram_mod_dir / "modules.dep").write_text(vram_depmod["modules.dep"])
    (vram_mod_dir / "modules.alias").write_text(vram_depmod["modules.alias"])
    (vram_mod_dir / "modules.softdep").write_text(vram_depmod["modules.softdep"])
    if tmpl["vendor_blocklist"].is_file():
        shutil.copy2(tmpl["vendor_blocklist"], vram_mod_dir / "modules.blocklist")
    (vram_mod_dir / "modules.load").write_text("\n".join(stage1_load_order) + "\n")
    (vram_mod_dir / "modules.load.recovery").write_text("\n".join(recovery_load_order) + "\n")
    for p in vram_stage.rglob("*"):
        os.chmod(p, 0o755 if p.is_dir() else 0o644)

    vram_cpio, vram_lz4 = work_dir / "vendor_ramdisk00.cpio", work_dir / "vendor_ramdisk00.lz4"
    find_p = subprocess.Popen(["find", ".", "-print0"], cwd=vram_stage, stdout=subprocess.PIPE)
    sort_p = subprocess.Popen(["sort", "-z"], stdin=find_p.stdout, stdout=subprocess.PIPE, env={**os.environ, "LC_ALL": "C"})
    assert find_p.stdout is not None
    find_p.stdout.close()
    with vram_cpio.open("wb") as out_f:
        cpio_p = subprocess.Popen(["cpio", "--null", "-o", "-H", "newc", "--owner=0:0", "--quiet"], cwd=vram_stage, stdin=sort_p.stdout, stdout=out_f)
        assert sort_p.stdout is not None
        sort_p.stdout.close()
        cpio_p.wait()
        sort_p.wait()
        find_p.wait()
        if cpio_p.returncode != 0:
            raise RuntimeError(f"cpio failed with rc={cpio_p.returncode}")

    run([host_bin / "lz4", "-l", "-12", "--favor-decSpeed", "-f", vram_cpio, vram_lz4], env=env)
    if vram_lz4.read_bytes()[:4] != b"\x02\x21\x4c\x18":
        raise RuntimeError("vendor_ramdisk00.lz4 does not have LZ4 legacy magic 02 21 4c 18!")

    vendor_cmdline = " ".join(
        tok for tok in tmpl["vendor_cmdline"].split()
        if not tok.startswith(("swinfo.fingerprint=", "mtdoops.fingerprint=", "module_blacklist="))
    )
    for tok in EXTRA_VENDOR_CMDLINE.split():
        if tok not in vendor_cmdline.split():
            vendor_cmdline = f"{vendor_cmdline} {tok}"

    patched_bootconfig = work_dir / "bootconfig.patched"
    patched_bootconfig.write_text(
        tmpl["bootconfig"].read_text().replace("androidboot.load_modules_parallel=true", "androidboot.load_modules_parallel=false")
    )

    vendor_boot_out = out_images_dir / "vendor_boot.img"
    run([
        host_bin / "mkbootimg", "--header_version", "4", "--pagesize", "0x00001000",
        "--base", "0x00000000", "--kernel_offset", "0x00008000", "--ramdisk_offset", "0x01000000",
        "--tags_offset", "0x00000100", "--dtb_offset", "0x0000000001f00000", "--board", "",
        "--vendor_cmdline", vendor_cmdline, "--dtb", inp["dtb"], "--vendor_bootconfig", patched_bootconfig,
        "--ramdisk_type", "1", "--ramdisk_name", "", "--vendor_ramdisk_fragment", vram_lz4,
        "--vendor_boot", vendor_boot_out,
    ], env=env)
    vendor_boot_orig_size = vendor_boot_out.stat().st_size
    run([
        host_bin / "avbtool", "add_hash_footer", "--image", vendor_boot_out,
        "--partition_size", str(VENDOR_BOOT_PARTITION_SIZE), "--partition_name", "vendor_boot",
        "--algorithm", "SHA256_RSA4096", "--key", avb_key_pem, "--hash_algorithm", "sha256",
        "--salt", DEFAULT_SALT, "--rollback_index", AVB_ROLLBACK_INDEX,
        "--prop", f"com.android.build.vendor_boot.fingerprint:{args.fingerprint}",
    ], env=env)

    rec_work = work_dir / "recovery_build"
    if rec_work.exists():
        shutil.rmtree(rec_work)
    (rec_work / "unpacked").mkdir(parents=True)
    run([host_bin / "unpack_bootimg", "--boot_img", template_dir / "recovery.img", "--out", rec_work / "unpacked"], env=env)
    rec_src_cpio, rec_new_cpio, rec_new_lz4 = rec_work / "orig_ramdisk.cpio", rec_work / "new_ramdisk.cpio", rec_work / "new_ramdisk.lz4"
    run([host_bin / "lz4", "-d", "-f", rec_work / "unpacked/ramdisk", rec_src_cpio], env=env)
    rec_cpio_stats = rewrite_recovery_cpio(rec_src_cpio, rec_new_cpio, vram_mod_dir)
    run([host_bin / "lz4", "-l", "-12", "--favor-decSpeed", "-f", rec_new_cpio, rec_new_lz4], env=env)
    recovery_out = out_images_dir / "recovery.img"
    run([host_bin / "mkbootimg", "--header_version", "4", "--os_version", "17.0.0", "--os_patch_level", "2026-11", "--ramdisk", rec_new_lz4, "--cmdline", "", "--output", recovery_out], env=env)
    recovery_orig_size = recovery_out.stat().st_size
    run([
        host_bin / "avbtool", "add_hash_footer", "--image", recovery_out,
        "--partition_size", str(RECOVERY_PARTITION_SIZE), "--partition_name", "recovery",
        "--algorithm", "SHA256_RSA4096", "--key", avb_key_pem, "--hash_algorithm", "sha256",
        "--salt", DEFAULT_SALT, "--rollback_index", AVB_ROLLBACK_INDEX,
        "--prop", f"com.android.build.recovery.fingerprint:{args.fingerprint}",
    ], env=env)

    # 6. Build system_dlkm.img & vendor_dlkm.img
    sys_stage = work_dir / "system_dlkm_stage"
    if sys_stage.exists():
        shutil.rmtree(sys_stage)
    shutil.copytree(tmpl["system_dlkm_etc"], sys_stage / "etc")
    sys_mod_dir = sys_stage / "lib/modules" / release
    sys_mod_dir.mkdir(parents=True)
    for rel, src_p in sorted(sys_pruned_rel_map.items()):
        dst = sys_mod_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dst)
    proc = subprocess.run(["depmod", "-b", str(sys_stage), "-E", str(inp["symvers"]), release], text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"system_dlkm depmod failed: {proc.stderr}")

    sys_dep_graph = {
        Path(lhs.strip()).name: [Path(t).name for t in rhs.split() if t.strip()]
        for line in (sys_mod_dir / "modules.dep").read_text().splitlines()
        if ":" in line
        for lhs, rhs in [line.split(":", 1)]
    }
    fn_to_rel = {Path(rel).name: rel for rel in sys_pruned_rel_map}
    sys_sorted_fns = topo_sort_modules(set(fn_to_rel), sys_meta_by_name, sys_dep_graph, sorted(fn_to_rel))
    sys_order_rels = [fn_to_rel[fn] for fn in sys_sorted_fns]
    sys_load_rels = [fn_to_rel[fn] for fn in sys_sorted_fns if norm_mod(fn) not in SYSTEM_DLKM_HOTPLUG_ONLY]
    sys_hotplug_only = sorted(fn for fn in sys_sorted_fns if norm_mod(fn) in SYSTEM_DLKM_HOTPLUG_ONLY)
    (sys_mod_dir / "modules.load").write_text("\n".join(sys_load_rels) + "\n")
    (sys_mod_dir / "modules.order").write_text("\n".join(sys_order_rels) + "\n")
    for p in sys_stage.rglob("*"):
        os.chmod(p, 0o755 if p.is_dir() else 0o644)

    sys_dlkm_info = build_erofs_and_hashtree(
        sys_stage, "system_dlkm", out_images_dir / "system_dlkm.img", work_dir,
        ["com.android.build.system_dlkm.os_version:17", f"com.android.build.system_dlkm.fingerprint:{args.fingerprint}"],
        host_bin, file_contexts_bin, args.fingerprint, env,
    )

    vdlkm_stage = work_dir / "vendor_dlkm_stage"
    if vdlkm_stage.exists():
        shutil.rmtree(vdlkm_stage)
    shutil.copytree(tmpl["vendor_dlkm_etc"], vdlkm_stage / "etc")
    vdlkm_mod_dir = vdlkm_stage / "lib/modules"
    vdlkm_mod_dir.mkdir(parents=True)
    vdlkm_set = set(all_pool_metas)
    vdlkm_depmod = run_depmod_flat([combined_pool[fn]["path"] for fn in sorted(vdlkm_set)], inp["symvers"], "/vendor/lib/modules", work_dir / "depmod_vendor_dlkm")
    for fn in sorted(vdlkm_set):
        copy_ko_for_vendor(combined_pool[fn]["path"], vdlkm_mod_dir / fn, strip_bin)

    stage2_seeds = map_stock_list(mod_map["load_lists"]["stage2_vendor_dlkm"])
    for fn in ESSENTIAL_STAGE1_6_12 + [
        "xiaomi_touch.ko", "synaptics_tcm2.ko", "goodix_fod.ko", "nxp-nci.ko",
        "ir-spi.ko", "xiaomi_wifi_gpio.ko", "cs35l41_dlkm.ko", "swr_haptics_dlkm.ko",
        "btfmcodec.ko", "governor_gpubw_mon.ko", "governor_msm_adreno_tz.ko",
    ]:
        pfn = norm_to_pool_fn.get(norm_mod(fn))
        if pfn and pfn not in stage2_seeds:
            stage2_seeds.append(pfn)
    vdlkm_load_candidates = {fn for fn in (close_transitive(stage2_seeds) & vdlkm_set) if norm_mod(fn) not in tmpl_block_norms}
    vdlkm_load_order = topo_sort_modules(vdlkm_load_candidates, combined_pool, vdlkm_depmod["deps_graph"], stage2_seeds)
    assert_crm_before_consumers(vdlkm_load_order, "vendor_dlkm modules.load")

    (vdlkm_mod_dir / "modules.dep").write_text(vdlkm_depmod["modules.dep"])
    (vdlkm_mod_dir / "modules.alias").write_text(vdlkm_depmod["modules.alias"])
    (vdlkm_mod_dir / "modules.softdep").write_text(vdlkm_depmod["modules.softdep"])
    (vdlkm_mod_dir / "modules.load").write_text("\n".join(vdlkm_load_order) + "\n")
    if tmpl["vendor_blocklist"].is_file():
        shutil.copy2(tmpl["vendor_blocklist"], vdlkm_mod_dir / "modules.blocklist")

    sys_bl_lines = [ln.strip() for ln in tmpl["sys_blocklist"].read_text().splitlines() if ln.strip()] if tmpl["sys_blocklist"].is_file() else []
    sys_bl_existing = {ln.split()[1] for ln in sys_bl_lines if ln.startswith("blocklist ") and len(ln.split()) >= 2}
    for extra_bl in (
        "exynos_hvm", "9pnet", "9pnet_fd", "ptp_kvm", "vcpu_stall_detector",
        "virtio_balloon", "virtio_blk", "virtio_console", "virtio_pci",
        "virtio_pci_legacy_dev", "virtio_pci_modern_dev", "vmw_vsock_virtio_transport",
        "vmw_vsock_virtio_transport_common", "vsock", "vsock_diag",
        "xhci_pci_renesas", "snd_hda_codec_hdmi", "usbmon",
    ):
        if extra_bl not in sys_bl_existing:
            sys_bl_lines.append(f"blocklist {extra_bl}")
            sys_bl_existing.add(extra_bl)
    (vdlkm_mod_dir / "system_dlkm.modules.blocklist").write_text("\n".join(sys_bl_lines) + "\n")
    for p in vdlkm_stage.rglob("*"):
        os.chmod(p, 0o755 if p.is_dir() else 0o644)

    vendor_dlkm_info = build_erofs_and_hashtree(
        vdlkm_stage, "vendor_dlkm", out_images_dir / "vendor_dlkm.img", work_dir,
        ["com.android.build.vendor_dlkm.os_version:17", f"com.android.build.vendor_dlkm.fingerprint:{args.fingerprint}"],
        host_bin, file_contexts_bin, args.fingerprint, env,
    )

    # 7. Build vbmeta_system.img & vbmeta.img + Gate 1..4 verification
    tf_imgs = target_files_dir / "IMAGES"
    vbmeta_sys_out = out_images_dir / "vbmeta_system.img"
    run([
        host_bin / "avbtool", "make_vbmeta_image", "--algorithm", "SHA256_RSA4096",
        "--key", avb_key_pem, "--flags", "0", "--rollback_index", AVB_ROLLBACK_INDEX, "--padding_size", "4096",
        "--include_descriptors_from_image", tf_imgs / "product.img",
        "--include_descriptors_from_image", tf_imgs / "system.img",
        "--include_descriptors_from_image", out_images_dir / "system_dlkm.img",
        "--include_descriptors_from_image", tf_imgs / "system_ext.img",
        "--output", vbmeta_sys_out,
    ], env=env)

    vbmeta_out = out_images_dir / "vbmeta.img"
    run([
        host_bin / "avbtool", "make_vbmeta_image", "--algorithm", "SHA256_RSA4096",
        "--key", avb_key_pem, "--flags", "3", "--rollback_index", "0", "--padding_size", "4096",
        "--chain_partition", f"boot:4:{pub_bin}", "--chain_partition", f"dtbo:3:{pub_bin}",
        "--chain_partition", f"recovery:1:{pub_bin}", "--chain_partition", f"vbmeta_system:2:{pub_bin}",
        "--chain_partition", f"vendor_boot:5:{pub_bin}",
        "--include_descriptors_from_image", tf_imgs / "init_boot.img",
        "--include_descriptors_from_image", tf_imgs / "odm.img",
        "--include_descriptors_from_image", tf_imgs / "vendor.img",
        "--include_descriptors_from_image", out_images_dir / "vendor_dlkm.img",
        "--output", vbmeta_out,
    ], env=env)

    avb_verify_dir = work_dir / "avb_verify_staging"
    if avb_verify_dir.exists():
        shutil.rmtree(avb_verify_dir)
    avb_verify_dir.mkdir(parents=True)
    for name in ("boot", "dtbo", "vendor_boot", "recovery", "system_dlkm", "vendor_dlkm", "vbmeta_system", "vbmeta"):
        (avb_verify_dir / f"{name}.img").symlink_to(out_images_dir / f"{name}.img")
    for name in ("init_boot", "odm", "product", "system", "system_ext", "vendor"):
        (avb_verify_dir / f"{name}.img").symlink_to(tf_imgs / f"{name}.img")

    avb_vbmeta_out = run([host_bin / "avbtool", "verify_image", "--image", avb_verify_dir / "vbmeta.img", "--key", pub_pem, "--follow_chain_partitions"], env=env, capture=True).stdout
    avb_vbmeta_sys_out = run([host_bin / "avbtool", "verify_image", "--image", avb_verify_dir / "vbmeta_system.img", "--key", pub_pem, "--follow_chain_partitions"], env=env, capture=True).stdout

    vintf_dir = work_dir / "vintf_check"
    (vintf_dir / "apex").mkdir(parents=True, exist_ok=True)
    (vintf_dir / "apex/apex-info-list.xml").write_text('<?xml version="1.0" encoding="utf-8"?><apex-info-list></apex-info-list>\n')
    kver_file = vintf_dir / "kver.txt"
    kver_file.write_text(release + "\n")
    vintf_proc = subprocess.run([
        str(host_bin / "checkvintf"), "--check-compat",
        "--dirmap", f"/system:{target_files_dir / 'SYSTEM'}",
        "--dirmap", f"/vendor:{target_files_dir / 'VENDOR'}",
        "--dirmap", f"/odm:{target_files_dir / 'ODM'}",
        "--dirmap", f"/product:{target_files_dir / 'PRODUCT'}",
        "--dirmap", f"/system_ext:{target_files_dir / 'SYSTEM_EXT'}",
        "--dirmap", f"/apex:{vintf_dir / 'apex'}",
        "--kernel", f"{kver_file}:{inp['config']}",
        "--property", "ro.product.first_api_level=34",
        "--property", "ro.boot.product.vendor.sku=pineapple",
        "--property", "ro.boot.product.hardware.sku=houji",
    ], env=env, text=True, capture_output=True)
    vintf_ok = (vintf_proc.returncode == 0 and "COMPATIBLE" in vintf_proc.stdout)

    exported_by_mod = {norm_mod(m["filename"]): set(m["exported_crcs"]) for m in (sys_metas + vendor_metas_active)}
    qemu_report = {"ok": False, "skipped": True} if args.skip_qemu else run_qemu_kcfi_smoke_test(out_images_dir, work_dir, release, host_bin, env, exported_by_mod)
    overall_pass = kmi_report["ok"] and not vram_unresolved and vintf_ok and (qemu_report.get("ok", False) or args.skip_qemu)

    report = {
        "status": "PASS" if overall_pass else "FAIL",
        "mode": inp["mode"],
        "kernel_release": release,
        "strict_gki_2_0": {
            "config_pstore_ram_builtin": pstore_ram_builtin,
            "config_pstore_ram_module": pstore_ram_module,
            "config_cfi_clang_strict": cfi_strict,
        },
        "images": {
            "boot.img": {"path": str(boot_out), "original_bytes": boot_orig_size, "final_bytes": boot_out.stat().st_size, "sha256": sha256_file(boot_out)},
            "dtbo.img": {"path": str(dtbo_out), "original_bytes": dtbo_orig_size, "final_bytes": dtbo_out.stat().st_size, "sha256": sha256_file(dtbo_out)},
            "vendor_boot.img": {
                "path": str(vendor_boot_out), "original_bytes": vendor_boot_orig_size, "final_bytes": vendor_boot_out.stat().st_size,
                "sha256": sha256_file(vendor_boot_out), "vendor_cmdline": vendor_cmdline,
                "ramdisk_modules_count": len(vendor_ramdisk_set), "stage1_modules_load_count": len(stage1_load_order),
                "recovery_modules_load_count": len(recovery_load_order),
            },
            "recovery.img": {"path": str(recovery_out), "original_bytes": recovery_orig_size, "final_bytes": recovery_out.stat().st_size, "sha256": sha256_file(recovery_out), "cpio_rewrite_stats": rec_cpio_stats},
            "system_dlkm.img": {**sys_dlkm_info, "modules_kept_count": len(sys_pruned_rel_map), "modules_boot_load_count": len(sys_load_rels), "modules_hotplug_only": sys_hotplug_only, "modules_pruned_count": len(pruned_sys_names)},
            "vendor_dlkm.img": {**vendor_dlkm_info, "modules_count": len(vdlkm_set), "modules_load_count": len(vdlkm_load_order), "pruned_dead_bloat_skipped": sorted(vendor_pruned_skipped), "pruned_stub_panic_modules": sorted(stub_panic_pruned)},
            "vbmeta_system.img": {"path": str(vbmeta_sys_out), "final_bytes": vbmeta_sys_out.stat().st_size, "sha256": sha256_file(vbmeta_sys_out)},
            "vbmeta.img": {"path": str(vbmeta_out), "final_bytes": vbmeta_out.stat().st_size, "sha256": sha256_file(vbmeta_out), "avb_public_key_sha1": pub_sha1},
        },
        "gates": {
            "avb_chain_verification": {"ok": True, "avb_public_key_sha1": pub_sha1, "vbmeta_verify_summary": [ln for ln in avb_vbmeta_out.splitlines() if "Successfully verified" in ln], "vbmeta_system_verify_summary": [ln for ln in avb_vbmeta_sys_out.splitlines() if "Successfully verified" in ln]},
            "vintf_compatibility": {"ok": vintf_ok, "returncode": vintf_proc.returncode, "result": vintf_proc.stdout.strip().splitlines()[-1] if vintf_proc.stdout.strip() else ""},
            "stage1_ramdisk_closure": {"ok": not vram_unresolved, "unresolved_count": len(vram_unresolved), "vendor_ramdisk_total_ko": len(vendor_ramdisk_set), "stage1_load_count": len(stage1_load_order), "recovery_load_count": len(recovery_load_order)},
            "kmi_and_vermagic": kmi_report,
            "qemu_arm64_kcfi_smoke": qemu_report,
        },
    }
    (out_images_dir / "ASSEMBLY_VERIFICATION.json").write_text(json.dumps(report, indent=2) + "\n")
    sum_names = ["boot.img", "dtbo.img", "vendor_boot.img", "recovery.img", "system_dlkm.img", "vendor_dlkm.img", "vbmeta_system.img", "vbmeta.img"]
    (out_images_dir / "SHA256SUMS").write_text("\n".join(f"{sha256_file(out_images_dir / name)}  {name}" for name in sum_names) + "\n")

    print("\n================ ASSEMBLY SUMMARY ================", flush=True)
    print(json.dumps({
        "status": report["status"],
        "mode": report["mode"],
        "kernel_release": report["kernel_release"],
        "avb_ok": True,
        "vintf_ok": vintf_ok,
        "stage1_closure_ok": not vram_unresolved,
        "kmi_ok": kmi_report["ok"],
        "qemu_kcfi_ok": qemu_report.get("ok"),
        "qemu_summary": qemu_report.get("summary"),
    }, indent=2), flush=True)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
