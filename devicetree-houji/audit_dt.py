#!/usr/bin/env python3
"""
Deterministic Device Tree Audit:
Compares stock ground-truth (stock.dtb[0] + stock dtbo.img[0] merged via ufdt_apply_overlay)
against the built Linux 6.12 DTBs (out/dtb.img, out/dtbo.img, out/pineapple-v2-houji-merged.dtb)
across bootloader matching, /reserved-memory, early-boot bindings, regulators, thermal zones,
and Xiaomi houji peripheral nodes.
"""

import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
PROJECT_ROOT = REPO_ROOT.parent

FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


def align4(n: int) -> int:
    return (n + 3) & ~3


def parse_fdt(blob: bytes) -> dict:
    magic, totalsize, off_struct, off_strings, off_rsvmap, version, last_comp, boot_cpuid, size_strings, size_struct = struct.unpack_from(">10I", blob, 0)
    if magic != 0xd00dfeed:
        raise ValueError(f"Bad FDT magic {hex(magic)}")

    def read_cstr(off: int) -> str:
        end = blob.index(b"\x00", off)
        return blob[off:end].decode("ascii", errors="replace")

    pos = off_struct
    stack = []
    root = None

    while pos < off_struct + size_struct:
        token = struct.unpack_from(">I", blob, pos)[0]
        pos += 4
        if token == FDT_BEGIN_NODE:
            end = blob.index(b"\x00", pos)
            name = blob[pos:end].decode("ascii", errors="replace")
            pos = align4(end + 1)
            node = {"_name": name, "_props": {}, "_children": {}}
            if stack:
                stack[-1]["_children"][name] = node
            else:
                root = node
            stack.append(node)
        elif token == FDT_END_NODE:
            stack.pop()
        elif token == FDT_PROP:
            plen, nameoff = struct.unpack_from(">2I", blob, pos)
            pos += 8
            pval = blob[pos:pos + plen]
            pos = align4(pos + plen)
            pname = read_cstr(off_strings + nameoff)
            stack[-1]["_props"][pname] = pval
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            raise ValueError(f"Unknown FDT token {token} at {pos - 4}")
    assert root is not None
    return root


def get_node(root: dict, path: str):
    if path == "/":
        return root
    cur = root
    for part in path.strip("/").split("/"):
        if part not in cur["_children"]:
            return None
        cur = cur["_children"][part]
    return cur


def walk_nodes(node: dict, prefix: str = ""):
    cur_path = prefix or "/"
    yield cur_path, node
    for cname, child in node["_children"].items():
        cpath = f"/{cname}" if cur_path == "/" else f"{cur_path}/{cname}"
        yield from walk_nodes(child, cpath)


def prop_u32_list(node: dict, name: str):
    val = node["_props"].get(name)
    if val is None:
        return None
    if len(val) % 4 != 0:
        return list(val)
    return list(struct.unpack(f">{len(val) // 4}I", val))


def prop_str_list(node: dict, name: str):
    val = node["_props"].get(name)
    if val is None:
        return None
    if val == b"":
        return []
    parts = val.split(b"\x00")
    if parts and parts[-1] == b"":
        parts = parts[:-1]
    return [p.decode("ascii", errors="replace") for p in parts]


def prop_str(node: dict, name: str):
    lst = prop_str_list(node, name)
    return lst[0] if lst else None


def split_concat_dtb(path: Path) -> list[bytes]:
    data = path.read_bytes()
    blobs = []
    pos = 0
    while pos < len(data):
        magic = struct.unpack_from(">I", data, pos)[0]
        if magic != 0xd00dfeed:
            nxt = data.find(b"\xd0\x0d\xfe\xed", pos + 1)
            if nxt == -1:
                break
            pos = nxt
            continue
        tsize = struct.unpack_from(">I", data, pos + 4)[0]
        blobs.append(data[pos:pos + tsize])
        pos += tsize
    return blobs


def parse_dtbo_img(path: Path) -> tuple[dict, list[bytes]]:
    data = path.read_bytes()
    magic, total_size, header_size, entry_size, entry_count, entries_off, page_size, version = struct.unpack_from(">8I", data, 0)
    entries = []
    blobs = []
    for i in range(entry_count):
        off = entries_off + i * entry_size
        dt_size, dt_offset, dt_id, dt_rev, c0, c1, c2, c3 = struct.unpack_from(">8I", data, off)
        blob = data[dt_offset:dt_offset + dt_size]
        blobs.append(blob)
        fdt = parse_fdt(blob)
        entries.append({
            "index": i,
            "size": dt_size,
            "offset": dt_offset,
            "id": hex(dt_id),
            "rev": hex(dt_rev),
            "custom": [hex(c0), hex(c1), hex(c2), hex(c3)],
            "model": prop_str(fdt, "model"),
            "compatible": prop_str_list(fdt, "compatible"),
            "qcom,msm-id": [hex(x) for x in (prop_u32_list(fdt, "qcom,msm-id") or [])],
            "qcom,board-id": [hex(x) for x in (prop_u32_list(fdt, "qcom,board-id") or [])],
            "xiaomi,miboard-id": [hex(x) for x in (prop_u32_list(fdt, "xiaomi,miboard-id") or [])],
        })
    return {
        "file": str(path),
        "size": len(data),
        "header": {
            "magic": hex(magic),
            "total_size": total_size,
            "header_size": header_size,
            "entry_size": entry_size,
            "entry_count": entry_count,
            "page_size": page_size,
            "version": version,
        },
        "entries": entries,
    }, blobs


def extract_resmem(root: dict) -> dict:
    rm = get_node(root, "/reserved-memory")
    out = {}
    if not rm:
        return out
    for name, node in rm["_children"].items():
        reg = prop_u32_list(node, "reg")
        size = prop_u32_list(node, "size")
        entry = {
            "compatible": prop_str_list(node, "compatible"),
            "no-map": "no-map" in node["_props"],
            "reusable": "reusable" in node["_props"],
        }
        if reg and len(reg) == 4:
            base = (reg[0] << 32) | reg[1]
            length = (reg[2] << 32) | reg[3]
            entry["reg_base"] = hex(base)
            entry["reg_size"] = hex(length)
            entry["reg_end"] = hex(base + length)
        if size and len(size) == 2:
            entry["size"] = hex((size[0] << 32) | size[1])
        for extra in ("console-size", "pmsg-size", "record-size", "ftrace-size", "mem-type"):
            v = prop_u32_list(node, extra)
            if v is not None:
                entry[extra] = [hex(x) for x in v]
        out[name] = entry
    return out


def check_resmem_overlaps(rm_dict: dict):
    intervals = []
    for name, info in rm_dict.items():
        if "reg_base" in info and int(info["reg_size"], 16) > 0:
            b = int(info["reg_base"], 16)
            s = int(info["reg_size"], 16)
            intervals.append((b, b + s, name))
    intervals.sort()
    overlaps = []
    for i in range(len(intervals) - 1):
        b1, e1, n1 = intervals[i]
        b2, e2, n2 = intervals[i + 1]
        if e1 > b2:
            overlaps.append({
                "node_a": n1, "range_a": [hex(b1), hex(e1)],
                "node_b": n2, "range_b": [hex(b2), hex(e2)],
            })
    return overlaps


def collect_compatibles(root: dict):
    cmap = {}
    for path, node in walk_nodes(root):
        cs = prop_str_list(node, "compatible")
        if cs:
            status = prop_str(node, "status") or "okay"
            for c in cs:
                cmap.setdefault(c, []).append({"path": path, "status": status})
    return cmap


def find_ufdt(explicit: Path | None, yaap_dir: Path, kp5_dir: Path) -> Path:
    for cand in (
        explicit,
        yaap_dir / "prebuilts/kernel-build-tools/linux-x86/bin/ufdt_apply_overlay",
        kp5_dir / "kernel_platform/prebuilts/kernel-build-tools/linux-x86/bin/ufdt_apply_overlay",
        shutil.which("ufdt_apply_overlay"),
    ):
        if cand and Path(cand).is_file():
            return Path(cand)
    raise FileNotFoundError("Could not locate ufdt_apply_overlay (pass --ufdt or sync yaap/kp5)")


def _default_dir(*candidates: Path) -> Path:
    for cand in candidates:
        if cand.is_dir():
            return cand
    return candidates[0]


def main() -> int:
    default_rom = _default_dir(PROJECT_ROOT / "yaap-houji/yaap", PROJECT_ROOT / "yaap", REPO_ROOT / "yaap")
    default_kp5 = _default_dir(REPO_ROOT / "kernel-6.12/kp5", REPO_ROOT / "kp5", PROJECT_ROOT / "kp5")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom-dir", "--yaap-dir", dest="yaap_dir", type=Path, default=default_rom, help="Android ROM repo root")
    parser.add_argument("--kp5-dir", type=Path, default=default_kp5, help="KP5 workspace root")
    parser.add_argument("--stock-dir", type=Path, help="Stock kernel dir (defaults to <yaap>/vendor/xiaomi/houji/kernel/stock)")
    parser.add_argument("--dt-out", type=Path, default=HERE / "out", help="Built DT output dir")
    parser.add_argument("--ufdt", type=Path, help="Path to ufdt_apply_overlay")
    parser.add_argument("--output", type=Path, help="Output JSON report path (default: <dt-out>/DT_AUDIT.json)")
    args = parser.parse_args()

    yaap_dir = args.yaap_dir.resolve()
    kp5_dir = args.kp5_dir.resolve()
    stock_dir = (args.stock_dir or (yaap_dir / "vendor/xiaomi/houji/kernel/stock")).resolve()
    out_dir = args.dt_out.resolve()
    ufdt_bin = find_ufdt(args.ufdt, yaap_dir, kp5_dir)
    out_json = (args.output or (out_dir / "DT_AUDIT.json")).resolve()

    issues: list[str] = []
    warnings: list[str] = []

    stock_dtbs = split_concat_dtb(stock_dir / "dtb/stock.dtb")
    new_dtbs = split_concat_dtb(out_dir / "dtb.img")

    def summarize_base_dtbs(blobs):
        res = []
        for idx, b in enumerate(blobs):
            f = parse_fdt(b)
            res.append({
                "index": idx,
                "size": len(b),
                "model": prop_str(f, "model"),
                "compatible": prop_str_list(f, "compatible"),
                "qcom,msm-id": [hex(x) for x in (prop_u32_list(f, "qcom,msm-id") or [])],
                "qcom,board-id": [hex(x) for x in (prop_u32_list(f, "qcom,board-id") or [])],
            })
        return res

    stock_dtb_summary = summarize_base_dtbs(stock_dtbs)
    new_dtb_summary = summarize_base_dtbs(new_dtbs)
    stock_dtbo_info, stock_dtbo_blobs = parse_dtbo_img(stock_dir / "dtbo.img")
    new_dtbo_info, _ = parse_dtbo_img(out_dir / "dtbo.img")

    if len(stock_dtb_summary) != len(new_dtb_summary):
        issues.append(f"Base DTB count mismatch: stock={len(stock_dtb_summary)} vs new={len(new_dtb_summary)}")
    for s, n in zip(stock_dtb_summary, new_dtb_summary):
        for k in ("compatible", "qcom,msm-id", "qcom,board-id"):
            if s[k] != n[k]:
                issues.append(f"Base DTB[{s['index']}] {k} mismatch: stock={s[k]} vs new={n[k]}")

    se0 = stock_dtbo_info["entries"][0]
    ne0 = new_dtbo_info["entries"][0]
    for k in ("id", "rev", "custom", "compatible", "qcom,board-id", "xiaomi,miboard-id"):
        if se0[k] != ne0[k]:
            issues.append(f"DTBO[0] {k} mismatch: stock={se0[k]} vs new={ne0[k]}")
    s_pairs = set(zip(se0["qcom,msm-id"][0::2], se0["qcom,msm-id"][1::2]))
    n_pairs = set(zip(ne0["qcom,msm-id"][0::2], ne0["qcom,msm-id"][1::2]))
    if s_pairs != n_pairs:
        issues.append(f"DTBO[0] qcom,msm-id pair set mismatch: stock={sorted(s_pairs)} vs new={sorted(n_pairs)}")

    with tempfile.TemporaryDirectory() as td_str:
        td = Path(td_str)
        s_base = td / "stock_0.dtb"
        s_ov = td / "stock_0.dtbo"
        s_merged = td / "stock_merged_0.dtb"
        s_base.write_bytes(stock_dtbs[0])
        s_ov.write_bytes(stock_dtbo_blobs[0])
        subprocess.run([str(ufdt_bin), str(s_base), str(s_ov), str(s_merged)], check=True, capture_output=True)
        stock_root = parse_fdt(s_merged.read_bytes())

    new_root = parse_fdt((out_dir / "pineapple-v2-houji-merged.dtb").read_bytes())

    # 3. /reserved-memory audit
    stock_rm = extract_resmem(stock_root)
    new_rm = extract_resmem(new_root)
    new_rm_overlaps = check_resmem_overlaps(new_rm)
    if new_rm_overlaps:
        issues.append(f"6.12 /reserved-memory has address overlaps: {new_rm_overlaps}")

    rm_diffs = {}
    for k in sorted(set(stock_rm) | set(new_rm)):
        if k not in new_rm:
            rm_diffs[k] = {"status": "MISSING_IN_6_12", "stock": stock_rm[k]}
            issues.append(f"/reserved-memory/{k} missing in 6.12")
        elif k not in stock_rm:
            rm_diffs[k] = {"status": "ADDED_IN_6_12", "new": new_rm[k]}
        else:
            s_info, n_info = stock_rm[k], new_rm[k]
            diff_fields = {}
            compare_fields = (
                ("reg_base", "reg_size", "size", "no-map", "reusable", "pmsg-size")
                if k == "ramoops_region"
                else ("reg_base", "reg_size", "size", "no-map", "reusable", "console-size", "pmsg-size")
            )
            for fld in compare_fields:
                if s_info.get(fld) != n_info.get(fld):
                    diff_fields[fld] = {"stock": s_info.get(fld), "new": n_info.get(fld)}
            if k == "ramoops_region":
                if n_info.get("record-size") != ["0x40000"]:
                    diff_fields["record-size"] = {"expected": ["0x40000"], "new": n_info.get("record-size")}
                if n_info.get("console-size") != ["0x100000"]:
                    diff_fields["console-size"] = {"expected": ["0x100000"], "new": n_info.get("console-size")}
                if n_info.get("mem-type") != ["0x2"]:
                    diff_fields["mem-type"] = {"expected": ["0x2"], "new": n_info.get("mem-type")}
            if diff_fields:
                rm_diffs[k] = {"status": "DIFF", "fields": diff_fields}
                issues.append(f"/reserved-memory/{k} mismatch: {diff_fields}")

    critical_rm_checks = {}
    for k in (
        "ramoops_region", "dump_display_region@816e0000", "mpss_region@8bc00000",
        "adspslpi_region@9df00000", "cdsp_region@9ca00000", "camera_region@9b300000",
        "video_region@9bb00000", "cvp_region@9c300000", "hwfence-shmem",
        "splash_region", "qseecom_region", "qseecom_ta_region", "linux,cma",
        "oem_vm_region@f7c00000", "cnss_wlan_region",
    ):
        critical_rm_checks[k] = {
            "stock": stock_rm.get(k),
            "new": new_rm.get(k),
            "exact_match": (stock_rm.get(k) == new_rm.get(k)) if k != "ramoops_region" else (
                stock_rm.get(k, {}).get("reg_base") == new_rm.get(k, {}).get("reg_base")
                and stock_rm.get(k, {}).get("reg_size") == new_rm.get(k, {}).get("reg_size")
                and new_rm.get(k, {}).get("record-size") == ["0x40000"]
                and new_rm.get(k, {}).get("console-size") == ["0x100000"]
                and new_rm.get(k, {}).get("pmsg-size") == ["0x200000"]
                and new_rm.get(k, {}).get("mem-type") == ["0x2"]
            ),
        }

    # 4. Early-boot & 6.12 bindings audit
    early_boot: dict = {}

    s_tlmm = get_node(stock_root, "/soc/pinctrl@f000000")
    n_tlmm = get_node(new_root, "/soc/pinctrl@f000000")
    s_gres = prop_u32_list(s_tlmm, "qcom,gpios-reserved") if s_tlmm else None
    n_gres = prop_u32_list(n_tlmm, "qcom,gpios-reserved") if n_tlmm else None
    early_boot["tlmm_gpios_reserved"] = {"stock": s_gres, "new": n_gres, "match": s_gres == n_gres}
    if s_gres != n_gres:
        issues.append(f"tlmm qcom,gpios-reserved mismatch: stock={s_gres} vs new={n_gres}")

    n_pg = get_node(new_root, "/soc/qcom,pmic_glink")
    n_pgl = get_node(new_root, "/soc/qcom,pmic_glink_log")
    early_boot["pmic_glink"] = {
        "pmic_glink_compatible_6_12": prop_str_list(n_pg, "compatible") if n_pg else None,
        "pmic_glink_log_compatible_6_12": prop_str_list(n_pgl, "compatible") if n_pgl else None,
        "battery_charger_present": bool(get_node(new_root, "/soc/qcom,pmic_glink/qcom,battery_charger")),
        "battery_charger_shutdown_voltage": prop_u32_list(get_node(new_root, "/soc/qcom,pmic_glink/qcom,battery_charger"), "qcom,shutdown-voltage"),
        "charger_partition_present": bool(get_node(new_root, "/soc/qcom,pmic_glink/charger_partition")),
        "ucsi_connector_present": bool(get_node(new_root, "/soc/qcom,pmic_glink/qcom,ucsi/connector")),
    }
    if early_boot["pmic_glink"]["pmic_glink_compatible_6_12"] != ["qcom,qti-pmic-glink"]:
        issues.append("pmic_glink does not use 6.12 binding qcom,qti-pmic-glink")

    s_spmi = get_node(stock_root, "/soc/qcom,spmi@c42d000")
    n_spmi = get_node(new_root, "/soc/qcom,spmi@c42d000")
    s_pmics = sorted(c for c, nd in (s_spmi["_children"].items() if s_spmi else []) if (prop_str(nd, "status") or "ok") != "disabled")
    n_pmics = sorted(c for c, nd in (n_spmi["_children"].items() if n_spmi else []) if (prop_str(nd, "status") or "ok") != "disabled")
    early_boot["spmi_enabled_pmics"] = {"stock": s_pmics, "new": n_pmics, "match": s_pmics == n_pmics}
    if s_pmics != n_pmics:
        issues.append(f"Enabled SPMI PMICs mismatch: stock={s_pmics} vs new={n_pmics}")

    n_ufshc = get_node(new_root, "/soc/ufshc@1d84000")
    n_ufsphy = get_node(new_root, "/soc/ufsphy_mem@1d80000")
    n_sdhc2 = get_node(new_root, "/soc/sdhci@8804000")
    ufshc_reg = [hex(x) for x in (prop_u32_list(n_ufshc, "reg") or [])] if n_ufshc else []
    ufshc_reg_names = prop_str_list(n_ufshc, "reg-names") if n_ufshc else None
    ufshc_use_hwkm = bool(n_ufshc and "qcom,ice-use-hwkm" in n_ufshc["_props"])
    ufshc_handle_clks = bool(n_ufshc and "qcom,ice-handle-clks" in n_ufshc["_props"])
    ufshc_esi_aff = prop_u32_list(n_ufshc, "qcom,esi-affinity-mask") if n_ufshc else []
    ufshc_prime_mask = prop_u32_list(n_ufshc, "qcom,prime-mask") if n_ufshc else []
    ufshc_silver_mask = prop_u32_list(n_ufshc, "qcom,silver-mask") if n_ufshc else []
    ufshc_msi_parent = prop_u32_list(n_ufshc, "msi-parent") if n_ufshc else []
    ufshc_broken_ahit = bool(n_ufshc and "qcom,broken-ahit-wa" in n_ufshc["_props"])
    early_boot["ufs_and_storage"] = {
        "ufshc_status": prop_str(n_ufshc, "status") if n_ufshc else None,
        "ufshc_reg_6_12": ufshc_reg,
        "ufshc_reg_names_6_12": ufshc_reg_names,
        "ufshc_ice_use_hwkm": ufshc_use_hwkm,
        "ufshc_ice_handle_clks": ufshc_handle_clks,
        "ufshc_prime_mask": [hex(x) for x in (ufshc_prime_mask or [])],
        "ufshc_silver_mask": [hex(x) for x in (ufshc_silver_mask or [])],
        "ufshc_esi_affinity_mask": ufshc_esi_aff,
        "ufshc_msi_parent_cells": len(ufshc_msi_parent or []),
        "ufshc_msi_parent_id": hex(ufshc_msi_parent[1]) if ufshc_msi_parent and len(ufshc_msi_parent) == 2 else None,
        "ufshc_broken_ahit_wa": ufshc_broken_ahit,
        "ufsphy_status": prop_str(n_ufsphy, "status") if n_ufsphy else None,
        "ufsphy_vdda_min_uv": prop_u32_list(n_ufsphy, "vdda-phy-min-microvolt") if n_ufsphy else None,
        "sdhc2_status": prop_str(n_sdhc2, "status") if n_sdhc2 else None,
    }
    if early_boot["ufs_and_storage"]["ufshc_status"] != "ok" or early_boot["ufs_and_storage"]["ufsphy_status"] != "ok":
        issues.append("UFS controller or PHY not enabled")
    expected_ufs_reg = ["0x1d84000", "0x3000", "0x1d88000", "0x18000", "0x1da5000", "0x2000", "0x1da4000", "0x10"]
    expected_ufs_reg_names = ["ufs_mem", "ice", "mcq_sqd", "mcq_vs"]
    if ufshc_reg != expected_ufs_reg or ufshc_reg_names != expected_ufs_reg_names or not ufshc_use_hwkm:
        issues.append(f"ufshc@1d84000 ICE/HWKM/MCQ mismatch: reg={ufshc_reg} reg-names={ufshc_reg_names} use_hwkm={ufshc_use_hwkm}")
    if not ufshc_esi_aff or any(c >= 8 for c in ufshc_esi_aff):
        issues.append(f"ufshc@1d84000 qcom,esi-affinity-mask invalid/OOB CPU index (>=8): {ufshc_esi_aff}")
    if ufshc_silver_mask != [0x03] or ufshc_prime_mask != [0x80]:
        issues.append(f"ufshc@1d84000 CPU cluster masks mismatch: prime={ufshc_prime_mask} silver={ufshc_silver_mask}")
    if not (ufshc_msi_parent and len(ufshc_msi_parent) == 2 and ufshc_msi_parent[1] == 0x60):
        issues.append(f"ufshc@1d84000 missing msi-parent=<&gic_its 0x60>: {ufshc_msi_parent}")
    if not ufshc_broken_ahit:
        issues.append("ufshc@1d84000 missing qcom,broken-ahit-wa")
    if early_boot["ufs_and_storage"]["sdhc2_status"] != "disabled":
        issues.append("sdhc_2 should be disabled on houji")

    n_pcie0 = get_node(new_root, "/soc/qcom,pcie@1c00000")
    n_pcie1 = get_node(new_root, "/soc/qcom,pcie@1c08000")
    pcie1_status = prop_str(n_pcie1, "status") if n_pcie1 else None
    early_boot["pcie_selinux_paths"] = {
        "pcie0_qcom_node_present": n_pcie0 is not None,
        "pcie1_qcom_node_present": n_pcie1 is not None,
        "pcie1_status": pcie1_status,
        "pcie0_old_node_absent": get_node(new_root, "/soc/pcie@1c00000") is None,
        "pcie1_old_node_absent": get_node(new_root, "/soc/pcie@1c08000") is None,
    }
    if not n_pcie0 or not n_pcie1:
        issues.append("PCIe nodes must be named qcom,pcie@1c00000 and qcom,pcie@1c08000 for enforcing SELinux genfscon")
    if pcie1_status != "disabled":
        issues.append(f"pcie1 must be disabled on houji (avoids GPIO 99 conflict with cs35l41@42): status={pcie1_status}")

    n_usb0 = get_node(new_root, "/soc/ssusb@a600000")
    n_dwc3 = get_node(new_root, "/soc/ssusb@a600000/dwc3@a600000")
    n_eusb = get_node(new_root, "/soc/hsphy@88e3000")
    n_qmp = get_node(new_root, "/soc/ssphy@88e8000")
    n_rep = get_node(new_root, "/soc/qcom,spmi@c42d000/qcom,pm8550b@7/qcom,eusb2-repeater@fd00")
    s_rep = get_node(stock_root, "/soc/qcom,spmi@c42d000/qcom,pm8550b@7/qcom,eusb2-repeater@fd00")
    n_eud = get_node(new_root, "/soc/qcom,msm-eud@88e0000")
    usb2_lpm_dis = bool(n_dwc3 and "snps,usb2-lpm-disable" in n_dwc3["_props"] and "snps,usb2-gadget-lpm-disable" in n_dwc3["_props"])
    qmp_vdd = prop_u32_list(n_qmp, "qcom,vdd-voltage-level") if n_qmp else None
    rep_match = all(
        prop_u32_list(n_rep, p) == prop_u32_list(s_rep, p)
        for p in ("qcom,param-override-seq", "qcom,param-override-seq-host", "qcom,param-override-seq-factory")
    ) if (n_rep and s_rep) else False
    early_boot["usb"] = {
        "dwc3_role_switch": "usb-role-switch" in (n_dwc3["_props"] if n_dwc3 else {}),
        "usb0_wcd_usbss": "qcom,wcd_usbss" in (n_usb0["_props"] if n_usb0 else {}),
        "usb0_extcon_absent": "extcon" not in (n_usb0["_props"] if n_usb0 else {}),
        "eud_status": prop_str(n_eud, "status") if n_eud else None,
        "usb0_dummy_supply": "dummy-supply" in (n_usb0["_props"] if n_usb0 else {}),
        "usb0_pm_qos_latency": prop_u32_list(n_usb0, "qcom,pm-qos-latency") if n_usb0 else None,
        "dwc3_max_speed": prop_str(n_dwc3, "maximum-speed") if n_dwc3 else None,
        "dwc3_usb2_lpm_disabled": usb2_lpm_dis,
        "eusb2_repeater_bound": "usb-repeater" in (n_eusb["_props"] if n_eusb else {}),
        "eusb2_repeater_seqs_match_stock": rep_match,
        "qmp_dp_phy_vdd_voltage_level": qmp_vdd,
        "qmp_dp_phy_init_seq_len": len(prop_u32_list(n_qmp, "qcom,qmp-phy-init-seq") or []) if n_qmp else 0,
    }
    if not (
        early_boot["usb"]["usb0_extcon_absent"]
        and early_boot["usb"]["eud_status"] == "disabled"
        and early_boot["usb"]["dwc3_role_switch"]
        and early_boot["usb"]["usb0_dummy_supply"]
        and early_boot["usb"]["usb0_pm_qos_latency"] == [2]
    ):
        issues.append(f"ssusb@a600000 / eud / dummy-supply / pm-qos-latency check failed: {early_boot['usb']}")
    if not usb2_lpm_dis or qmp_vdd != [0, 880000, 880000] or not rep_match:
        issues.append(f"USB DWC3/QMP/eUSB2 repeater mismatch vs stock: lpm_dis={usb2_lpm_dis} qmp_vdd={qmp_vdd} rep_match={rep_match}")

    n_smmu = get_node(new_root, "/soc/apps-smmu@15000000")
    n_kgsl_smmu = get_node(new_root, "/soc/kgsl-smmu@3da0000")
    early_boot["smmu_6_12"] = {
        "apps_smmu_reg_cells": len(prop_u32_list(n_smmu, "reg") or []) if n_smmu else 0,
        "kgsl_smmu_reg_cells": len(prop_u32_list(n_kgsl_smmu, "reg") or []) if n_kgsl_smmu else 0,
    }

    # 5. Regulators audit
    reg_paths = {
        "L2G (Display 1.8V VDDIO)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldog2/regulator-pm-v6g-l2",
        "L8B (3.3V rail)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldob8/regulator-pm-humu-l8",
        "L9B (touch AVDD)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldob9/regulator-pm-humu-l9",
        "L16B (Goodix FOD VDD)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldob16/regulator-pm-humu-l16",
        "L2M (camera)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldom2/regulator-pm8010m-l2",
        "L6M (camera)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldom6/regulator-pm8010m-l6",
        "L7M (camera)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldom7/regulator-pm8010m-l7",
        "L2C (camera)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldoc2/regulator-pm-v6c-l2",
        "BOB2 (camera)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-bobb2/regulator-pm-humu-bob2",
        "L1E (camera added)": "/soc/rsc@17a00000/drv@2/rpmh-regulator-ldoe1/regulator-pm-v6e-l1",
        "display_panel_vddd": "/soc/display_panel_vddd",
        "cam_cc_camss_top_gdsc": "/soc/qcom,gdsc@add5004",
    }
    regulators_audit = {}
    for label, path in reg_paths.items():
        sn = get_node(stock_root, path)
        nn = get_node(new_root, path)
        s_v = {k: prop_u32_list(sn, k) for k in ("regulator-min-microvolt", "regulator-max-microvolt", "qcom,init-voltage")} if sn else None
        n_v = {k: prop_u32_list(nn, k) for k in ("regulator-min-microvolt", "regulator-max-microvolt", "qcom,init-voltage")} if nn else None
        match = (s_v == n_v) and (sn is not None) and (nn is not None)
        regulators_audit[label] = {"path": path, "stock": s_v, "new": n_v, "match": match}
        if not match:
            issues.append(f"Regulator {label} ({path}) mismatch: stock={s_v} vs new={n_v}")

    # 6. Thermal & VADC
    vadc = get_node(new_root, "/soc/qcom,spmi@c42d000/qcom,pmk8550@0/vadc@9000")
    vadc_reg_map = {}
    vadc_dups = []
    if vadc:
        for cname, cnode in vadc["_children"].items():
            r = prop_u32_list(cnode, "reg")
            if r:
                key = tuple(r)
                if key in vadc_reg_map:
                    vadc_dups.append({"reg": [hex(x) for x in key], "nodes": [vadc_reg_map[key], cname]})
                else:
                    vadc_reg_map[key] = cname
    if vadc_dups:
        issues.append(f"Duplicate VADC channel regs under pmk8550_vadc: {vadc_dups}")

    n_tz = get_node(new_root, "/soc/thermal-zones")
    xiaomi_therms = [
        "cpu_therm", "flash_therm", "wifi_therm", "pa_therm0", "wireless_therm",
        "conn_therm", "charger_therm0", "quiet_therm", "display_therm", "pa_therm1",
    ]
    missing_therms = [t for t in xiaomi_therms if not (n_tz and t in n_tz["_children"])]
    if missing_therms:
        issues.append(f"Missing Xiaomi board thermal zones: {missing_therms}")

    thermal_audit = {
        "total_thermal_zones_6_12": len(n_tz["_children"]) if n_tz else 0,
        "total_vadc_channels_6_12": len(vadc["_children"]) if vadc else 0,
        "vadc_duplicate_reg_channels": vadc_dups,
        "xiaomi_board_thermistors_present": [t for t in xiaomi_therms if n_tz and t in n_tz["_children"]],
        "xiaomi_board_thermistors_missing": missing_therms,
        "thermal_message_board_sensor": prop_str(get_node(new_root, "/soc/thermal-message"), "board-sensor") if get_node(new_root, "/soc/thermal-message") else None,
    }

    # 7. Peripherals audit
    stock_compats = collect_compatibles(stock_root)
    new_compats = collect_compatibles(new_root)
    required_houji_compats = [
        "ramoops", "xiaomi,mtdoops_pmsg", "xiaomi,dump_display", "xiaomi,charger_partition",
        "xiaomi,xiaomi-wifi", "xiaomi-touch", "synaptics,tcm-spi", "goodix,fingerprint",
        "qcom,sn-nci", "ir-spi", "cirrus,cs35l41", "qcom,wcd939x-codec",
        "qcom,wcd939x-i2c", "qcom,wsa884x", "qcom,pm8550b-swr-haptics", "qcom,cnss-kiwi",
        "qcom,sde-kms", "qcom,dsi-display", "qcom,adreno-gpu-gen7-9-1", "qcom,kgsl-3d0",
        "qcom,sm8650-vidc-v2", "qcom,cam-sensor", "qcom,cam-i2c-sensor", "qcom,actuator",
        "qcom,ois", "qcom,eeprom", "qcom,camera-flash", "qcom,csiphy-v2.2.0-n3",
        "qcom,ipa", "qcom,qti-pmic-glink", "qcom,battery-charger", "qcom,ucsi-glink",
        "qcom,qseecom", "qcom,msm-fastrpc-rpmsg", "qcom,kiwi", "qcom,ipclite",
        "qcom,hdcp", "qcom,va-minidump",
    ]
    peripherals_audit = {}
    for c in required_houji_compats:
        s_nodes = stock_compats.get(c, [])
        n_nodes = new_compats.get(c, [])
        ok = len(n_nodes) > 0
        peripherals_audit[c] = {
            "stock_count": len(s_nodes),
            "new_count": len(n_nodes),
            "present_in_6_12": ok,
            "paths_6_12": [x["path"] for x in n_nodes[:6]],
        }
        if not ok:
            issues.append(f"Required houji peripheral compatible '{c}' missing in 6.12 merged DTB")

    n3_panel = get_node(new_root, "/soc/qcom,mdss_mdp@ae00000/qcom,mdss_dsi_n3_42_0d_0a_dsc_cmd")
    peripherals_audit["dsi_panel_n3_42_0d_0a"] = {
        "present_in_6_12": n3_panel is not None,
        "panel_name": prop_str(n3_panel, "qcom,mdss-dsi-panel-name") if n3_panel else None,
        "bpp": prop_u32_list(n3_panel, "qcom,mdss-dsi-bpp") if n3_panel else None,
    }
    if n3_panel is None:
        issues.append("Xiaomi 14 TCL CSOT N3 panel node (qcom,mdss_dsi_n3_42_0d_0a_dsc_cmd) missing!")

    se4_spi = get_node(new_root, "/soc/qcom,qupv3_1_geni_se@ac0000/spi@a90000")
    se4_children = sorted(se4_spi["_children"].keys()) if se4_spi else []
    peripherals_audit["qupv3_se4_spi_touch_children"] = se4_children
    if "goodix-berlin@0" in se4_children or "synaptics_tcm@0" not in se4_children:
        issues.append(f"qupv3_se4_spi touch node collision or missing synaptics_tcm@0: {se4_children}")

    only_in_stock = sorted(set(stock_compats) - set(new_compats))
    only_in_6_12 = sorted(set(new_compats) - set(stock_compats))

    # 8. Hardware parity assertions
    n_chosen = get_node(new_root, "/chosen")
    n_bootargs = prop_str(n_chosen, "bootargs") if n_chosen else ""
    n_rpmh_stats = get_node(new_root, "/soc/sram@c3f0000")

    n_hyp_wdt = get_node(new_root, "/hypervisor/qcom,gh-watchdog")
    n_gh_panic = get_node(new_root, "/soc/qcom,gunyah-panic-notifier")
    n_dmesg_dump = get_node(new_root, "/soc/dmesg-dump")
    n_qrtr_tui = get_node(new_root, "/soc/qcom,qrtr-gunyah-tuivm")
    n_qrtr_oem = get_node(new_root, "/soc/qcom,qrtr-gunyah-oemvm")
    n_qrtr_cnss = get_node(new_root, "/soc/qcom,qrtr-mhi-cnss")
    qrtr_cnss_net_id = prop_u32_list(n_qrtr_cnss, "qcom,net-id") if n_qrtr_cnss else None
    if not all([n_hyp_wdt, n_gh_panic, n_dmesg_dump, n_qrtr_tui, n_qrtr_oem]):
        issues.append("Missing Gunyah hypervisor/watchdog or primary-VM peer nodes")
    if qrtr_cnss_net_id != [1]:
        issues.append(f"qcom,qrtr-mhi-cnss qcom,net-id must be <1> (FastConnect 7800), got {qrtr_cnss_net_id}")

    n_wcd_usbss = get_node(new_root, "/soc/qcom,qupv3_1_geni_se@ac0000/i2c@a8c000/wcd939x_i2c@e")
    n_wcd_rst = prop_u32_list(n_wcd_usbss, "reset-gpios") if n_wcd_usbss else None
    n_wcd_pnames = prop_str_list(n_wcd_usbss, "pinctrl-names") if n_wcd_usbss else None
    n_wcd_codec = get_node(new_root, "/soc/spf_core_platform/lpass-cdc/wcd939x-codec")
    s_wcd_codec = get_node(stock_root, "/soc/spf_core_platform/lpass-cdc/wcd939x-codec")
    wcd_mic_bias = prop_u32_list(n_wcd_codec, "qcom,cdc-vdd-mic-bias-voltage") if n_wcd_codec else None
    usbcss_props = [
        "qcom,usbcss-hs-xtalk-config", "qcom,usbcss-hs-rdson-3p6v", "qcom,usbcss-hs-rdson-6v",
        "qcom,usbcss-hs-r1-l", "qcom,usbcss-hs-r1-r", "qcom,usbcss-hs-r3-l", "qcom,usbcss-hs-r3-r",
        "qcom,usbcss-hs-r4-sbu1", "qcom,usbcss-hs-r4-sbu2", "qcom,usbcss-hs-r6-sbu1", "qcom,usbcss-hs-r6-sbu2",
        "qcom,usbcss-hs-r7-sbu1", "qcom,usbcss-hs-r7-sbu2", "qcom,usbcss-hs-rcom-margin",
        "qcom,usbcss-hs-diff-slope", "qcom,usbcss-hs-lin-k-aud",
    ]
    wcd_usbcss_match = all(prop_u32_list(n_wcd_codec, p) == prop_u32_list(s_wcd_codec, p) for p in usbcss_props) if (n_wcd_codec and s_wcd_codec) else False
    n_cir0 = get_node(new_root, "/soc/pinctrl@f000000/cirrus_reset_default_0/config")
    n_cir1 = get_node(new_root, "/soc/pinctrl@f000000/cirrus_reset_default_1/config")
    cirrus_ds_ok = (prop_u32_list(n_cir0, "drive-strength") == [16] and prop_u32_list(n_cir1, "drive-strength") == [16]) if (n_cir0 and n_cir1) else False
    if not (n_wcd_rst and len(n_wcd_rst) == 3 and n_wcd_rst[1] == 152 and n_wcd_pnames == ["usbss_active", "usbss_sleep"]):
        issues.append(f"wcd_usbss GPIO 152 reset/pinctrl mismatch: reset-gpios={n_wcd_rst} pinctrl-names={n_wcd_pnames}")
    if wcd_mic_bias != [3296000, 3296000] or not wcd_usbcss_match or not cirrus_ds_ok:
        issues.append(f"Audio calibration mismatch: mic_bias={wcd_mic_bias} usbcss_match={wcd_usbcss_match} cirrus_ds_16={cirrus_ds_ok}")

    n_hap = get_node(new_root, "/soc/qcom,spmi@c42d000/qcom,pm8550b@7/qcom,hv-haptics@f000")
    if not (n_hap and prop_str(n_hap, "status") == "ok" and prop_str(n_hap, "nvmem-names") == "hap_cfg_sdam" and "nvmem" in n_hap["_props"]):
        issues.append("pm8550b_haptics missing status='ok' or nvmem='hap_cfg_sdam'")

    n_kiwi = get_node(new_root, "/soc/qcom,cnss-kiwi@b0000000")
    kiwi_clks = prop_u32_list(n_kiwi, "clocks") if n_kiwi else None
    kiwi_cnames = prop_str_list(n_kiwi, "clock-names") if n_kiwi else None
    if not (kiwi_cnames == ["rf_clk"] and kiwi_clks and len(kiwi_clks) == 2 and kiwi_clks[1] == 6):
        issues.append(f"cnss-kiwi RF clock mismatch: clock-names={kiwi_cnames} clocks={kiwi_clks}")

    n3_panel_id = prop_u32_list(n3_panel, "mi,panel-id") if n3_panel else None
    n3_esd_int = prop_u32_list(n3_panel, "mi,mdss-dsi-panel-status-check-interval") if n3_panel else None
    if n3_panel_id != [0x4e33, 0x420d0a] or n3_esd_int != [5000]:
        issues.append(f"N3 display panel-id/ESD interval mismatch: panel-id={n3_panel_id} interval={n3_esd_int}")

    if "qcom_scm.download_mode=0" not in n_bootargs:
        issues.append("/chosen/bootargs missing qcom_scm.download_mode=0")
    if "ufs_qcom.crash_on_ber=n" not in n_bootargs:
        issues.append("/chosen/bootargs should set ufs_qcom.crash_on_ber=n matching factory stock")
    if ",/odm/firmware" not in n_bootargs:
        issues.append("/chosen/bootargs firmware_class.path missing ,/odm/firmware")
    if not n_rpmh_stats or prop_str_list(n_rpmh_stats, "compatible") != ["qcom,rpmh-stats-v4"]:
        issues.append("/soc/sram@c3f0000 should use qcom,rpmh-stats-v4")

    audit = {
        "summary": {
            "status": "PASS" if not issues else "FAIL",
            "issue_count": len(issues),
            "warning_count": len(warnings),
            "issues": issues,
            "warnings": warnings,
        },
        "bootloader_matching": {
            "stock_base_dtbs": stock_dtb_summary,
            "new_6_12_base_dtbs": new_dtb_summary,
            "stock_dtbo_img": stock_dtbo_info,
            "new_6_12_dtbo_img": new_dtbo_info,
        },
        "reserved_memory": {
            "stock_region_count": len(stock_rm),
            "new_6_12_region_count": len(new_rm),
            "overlaps_in_6_12": new_rm_overlaps,
            "critical_regions": critical_rm_checks,
            "diffs_vs_stock": rm_diffs,
        },
        "early_boot_and_6_12_bindings": early_boot,
        "regulators": regulators_audit,
        "thermal_and_vadc": thermal_audit,
        "houji_peripherals": peripherals_audit,
        "compatible_delta": {
            "stock_unique_count": len(stock_compats),
            "new_6_12_unique_count": len(new_compats),
            "only_in_stock_6_1": only_in_stock,
            "only_in_new_6_12": only_in_6_12,
        },
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(audit, indent=2) + "\n")

    print(f"=== DT_AUDIT SUMMARY: {audit['summary']['status']} ===")
    print(f"Issues ({len(issues)}):")
    for x in issues:
        print(f"  [ISSUE] {x}")
    print(f"Warnings ({len(warnings)}):")
    for x in warnings:
        print(f"  [WARN]  {x}")
    print(f"\nCompatible counts: stock={len(stock_compats)}, 6.12={len(new_compats)}")
    print(f"Only in stock (6.1 -> 6.12 removed/renamed, {len(only_in_stock)}): {only_in_stock}")
    print(f"Only in 6.12 (new 6.12 bindings, {len(only_in_6_12)}): {only_in_6_12}")
    print(f"\nWrote {out_json}")
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
