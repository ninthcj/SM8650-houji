#!/usr/bin/env python3
"""
Deterministic Linux 6.18 Base DTB + Houji DTBO compiler & overlay verifier.

Builds:
  1. All 4 SM8650 (pineapple / pineapple-v2 / pineapplep / pineapplep-v2) 6.18 base DTBs
     with SoC-level techpack overlays compiled single-pass (preserving child node order
     and __symbols__), plus concatenated out/dtb.img matching factory stock.dtb ordering.
  2. All 5 Xiaomi 14 (houji) board-level overlays verified individually via ufdt_apply_overlay,
     merged into houji-sm8650-merged.dtbo via fdtoverlaymerge, and packed into out/dtbo.img.
  3. End-to-end verification applying houji-sm8650-merged.dtbo onto all 4 base DTBs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
KERNEL_DIR = HERE.parent
REPO_ROOT = KERNEL_DIR
PROJECT_ROOT = REPO_ROOT.parent
SRC_QCOM = HERE / "src" / "qcom"

# Base DTB order matches factory stock.dtb: [0] v2, [1] v1, [2] p-v2, [3] p-v1
BASE_DTBS = [
    ("pineapple-v2", "pineapple-v2.dts", True),
    ("pineapple", "pineapple.dts", False),
    ("pineapplep-v2", "pineapplep-v2.dts", True),
    ("pineapplep", "pineapplep.dts", False),
]


def soc_techpack_overlays(is_v2: bool) -> list[str]:
    return [
        "display/display/pineapple-sde.dts",
        "camera/pineapple-camera-v2.dts" if is_v2 else "camera/pineapple-camera.dts",
        "graphics/gpu/pineapple-v2-gpu.dts" if is_v2 else "graphics/gpu/pineapple-gpu.dts",
        "video/pineapple-vidc-v2.dts" if is_v2 else "video/pineapple-vidc.dts",
        "audio/pineapple-audio.dts",
        "eva/pineapple-eva-v2.dts" if is_v2 else "eva/pineapple-eva.dts",
        "mmrm/pineapple/pineapple-mmrm.dts",
        "mmrm/pineapple/pineapple-mmrm-test-v2.dts" if is_v2 else "mmrm/pineapple/pineapple-mmrm-test.dts",
        "mm/hw_fence/pineapple-hw-fence.dts",
        "mm-sys/ubwcp-devicetree/pineapple-ubwcp.dts",
        "data/pineapple-ipa.dts",
        "dsp/pineapple-dsp.dts",
        "bt/pineapple-kiwi-bt.dts",
        "synx/synx/pineapple-synx.dts",
    ]


HOUJI_BOARD_OVERLAYS = [
    ("houji-sm8650-overlay", "houji-sm8650-overlay.dts"),
    ("houji-sde-display-mtp-overlay", "display/display/houji-sde-display-mtp-overlay.dts"),
    ("houji-sm8650-camera-sensor", "camera/houji-sm8650-camera-sensor.dts"),
    ("houji-audio-mtp", "audio/houji-audio-mtp.dts"),
    ("pineapple-kiwi-cnss", "wlan/pineapple-kiwi-cnss.dts"),
]


def first_existing(candidates: list[Path | str | None], name: str) -> Path:
    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        if p.is_file():
            return p
        w = shutil.which(str(cand))
        if w:
            return Path(w)
    raise FileNotFoundError(f"Could not locate required tool '{name}'. Checked: {candidates}")


def _compile_dtc_helper(tool_name: str, kp6_dir: Path, rom_dir: Path, work_dir: Path) -> Path | None:
    for src_root in (
        kp6_dir / "external/qcom-dtc",
        kp6_dir / "external/dtc",
        rom_dir / "external/dtc",
    ):
        src_c = src_root / f"{tool_name}.c"
        util_c = src_root / "util.c"
        libfdt_dir = src_root / "libfdt"
        if src_c.is_file() and util_c.is_file() and libfdt_dir.is_dir():
            built = work_dir / tool_name
            built.parent.mkdir(parents=True, exist_ok=True)
            (work_dir / "version_gen.h").write_text('#define DTC_VERSION "DTC 1.7.0"\n')
            fdt_srcs = [s for s in sorted(libfdt_dir.glob("*.c")) if s.name != "acpi.c"]
            cmd = [
                "cc", "-O2", "-DNO_YAML",
                f"-I{work_dir}", f"-I{src_root}", f"-I{libfdt_dir}",
                str(src_c), str(util_c), *[str(s) for s in fdt_srcs],
                "-o", str(built),
            ]
            run(cmd, f"compile {tool_name} from {src_root}")
            return built
    return None


def ensure_dtc_helper(tool_name: str, explicit: Path | None, prebuilts: list[Path], kp6_dir: Path, rom_dir: Path, work_dir: Path) -> Path:
    if explicit and explicit.is_file():
        return explicit
    for prebuilt in prebuilts:
        if prebuilt.is_file():
            return prebuilt
    which_hit = shutil.which(tool_name)
    if which_hit:
        return Path(which_hit)
    built = _compile_dtc_helper(tool_name, kp6_dir, rom_dir, work_dir)
    if built:
        return built
    raise FileNotFoundError(
        f"Could not locate or compile {tool_name}. Pass --{tool_name} or sync KP6 external/qcom-dtc."
    )


def run(cmd: list[str], desc: str):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FAIL [{desc}]: {' '.join(map(str, cmd))}", file=sys.stderr)
        if res.stdout:
            print("STDOUT:\n" + res.stdout[-2000:], file=sys.stderr)
        if res.stderr:
            print("STDERR:\n" + res.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"Command failed: {desc}")
    return res


def compile_dts_path(
    src_path: Path,
    out_blob: Path,
    work_dir: Path,
    dtc_bin: Path,
    include_dirs: list[Path],
    extra_inc_dirs: list[Path] | None = None,
) -> Path:
    pre_path = work_dir / (out_blob.name + ".dts.pre")
    inc_flags: list[str] = []
    search_dirs = [src_path.parent] + (extra_inc_dirs or []) + include_dirs
    seen: set[Path] = set()
    for d in search_dirs:
        if d.exists() and d not in seen:
            seen.add(d)
            inc_flags.extend(["-I", str(d)])

    cpp_cmd = [
        "cpp", "-nostdinc", *inc_flags,
        "-undef", "-D__DTS__", "-x", "assembler-with-cpp",
        str(src_path), "-o", str(pre_path),
    ]
    run(cpp_cmd, f"cpp {src_path.name}")

    dtc_cmd = [
        str(dtc_bin), "-@", "-I", "dts", "-O", "dtb",
        "-Wno-unit_address_vs_reg",
        "-Wno-unit_address_format",
        "-Wno-avoid_unnecessary_addr_size",
        "-Wno-alias_paths",
        "-Wno-graph_child_address",
        "-Wno-graph_port",
        "-Wno-graph_endpoint",
        "-Wno-simple_bus_reg",
        "-Wno-unique_unit_address",
        "-Wno-pci_device_reg",
        "-Wno-reg_format",
        "-Wno-interrupts_property",
        "-i", str(src_path.parent),
        "-i", str(SRC_QCOM),
        "-o", str(out_blob),
        str(pre_path),
    ]
    run(dtc_cmd, f"dtc {src_path.name}")
    return out_blob


def build_soc_techpack_inc(rel_dts: str, inc_dir: Path) -> Path:
    """Extract #include directives from an SoC techpack overlay .dts so single-pass dtc
    compilation preserves child node order and __symbols__.
    """
    src_path = SRC_QCOM / rel_dts
    out_inc = inc_dir / f"{src_path.stem}.inc.dtsi"
    out_inc.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for raw_line in src_path.read_text().splitlines():
        s = raw_line.strip()
        if s.startswith("#include"):
            if '"' in s:
                parts = s.split('"')
                if len(parts) >= 3:
                    cand = src_path.parent / parts[1]
                    if cand.exists():
                        s = f'#include "{cand}"'
            lines.append(s)
    out_inc.write_text("\n".join(lines) + "\n")
    return out_inc


def _default_dir(*candidates: Path) -> Path:
    for cand in candidates:
        if cand.is_dir():
            return cand
    return candidates[0]


def main() -> int:
    default_kp6 = _default_dir(REPO_ROOT / "kernel-6.18/kp6", REPO_ROOT / "kp6", PROJECT_ROOT / "kp6")
    default_rom = _default_dir(PROJECT_ROOT / "yaap-houji/yaap", PROJECT_ROOT / "yaap", REPO_ROOT / "yaap")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kp6-dir", "--kp5-dir", dest="kp6_dir", type=Path, default=default_kp6, help="KP6 workspace root")
    parser.add_argument("--rom-dir", "--yaap-dir", dest="rom_dir", type=Path, default=default_rom, help="Android ROM root for host tools / stock DT reference")
    parser.add_argument("--out-dir", type=Path, default=HERE / "out", help="Output directory for dtb.img / dtbo.img")
    parser.add_argument("--dtc", type=Path, help="Path to dtc binary supporting -@")
    parser.add_argument("--ufdt", type=Path, help="Path to ufdt_apply_overlay binary")
    parser.add_argument("--fdtoverlay", type=Path, help="Path to fdtoverlay binary")
    parser.add_argument("--fdtoverlaymerge", type=Path, help="Path to fdtoverlaymerge binary")
    parser.add_argument("--mkdtboimg", type=Path, help="Path to mkdtboimg binary or mkdtboimg.py script")
    args = parser.parse_args()

    kp6_dir = args.kp6_dir.resolve()
    rom_dir = args.rom_dir.resolve()
    out_dir = args.out_dir.resolve()
    work_dir = out_dir / "work"

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    dtc_bin = first_existing([
        args.dtc,
        kp6_dir / "prebuilts/kernel-build-tools/linux_musl-x86/bin/dtc",
        kp6_dir / "prebuilts/kernel-build-tools/linux-x86/bin/dtc",
        kp6_dir / "out/msm-kernel-pineapple-perf/host/bin/dtc",
        rom_dir / "out/host/linux-x86/bin/dtc",
        rom_dir / "prebuilts/kernel-build-tools/linux_musl-x86/bin/dtc",
        rom_dir / "prebuilts/kernel-build-tools/linux-x86/bin/dtc",
        REPO_ROOT / ".kernel-build/out/scripts/dtc/dtc",
        "dtc",
    ], "dtc")
    ufdt_bin = first_existing([
        args.ufdt,
        kp6_dir / "prebuilts/kernel-build-tools/linux_musl-x86/bin/ufdt_apply_overlay",
        kp6_dir / "prebuilts/kernel-build-tools/linux-x86/bin/ufdt_apply_overlay",
        rom_dir / "out/host/linux-x86/bin/ufdt_apply_overlay",
        rom_dir / "prebuilts/kernel-build-tools/linux_musl-x86/bin/ufdt_apply_overlay",
        rom_dir / "prebuilts/kernel-build-tools/linux-x86/bin/ufdt_apply_overlay",
        "ufdt_apply_overlay",
    ], "ufdt_apply_overlay")
    fdtoverlay_bin = ensure_dtc_helper(
        "fdtoverlay",
        args.fdtoverlay,
        [
            kp6_dir / "prebuilts/kernel-build-tools/linux_musl-x86/bin/fdtoverlay",
            kp6_dir / "prebuilts/kernel-build-tools/linux-x86/bin/fdtoverlay",
            kp6_dir / "out/msm-kernel-pineapple-perf/host/bin/fdtoverlay",
            rom_dir / "out/host/linux-x86/bin/fdtoverlay",
            rom_dir / "prebuilts/kernel-build-tools/linux_musl-x86/bin/fdtoverlay",
            rom_dir / "prebuilts/kernel-build-tools/linux-x86/bin/fdtoverlay",
            REPO_ROOT / ".kernel-build/out/scripts/dtc/fdtoverlay",
        ],
        kp6_dir,
        rom_dir,
        work_dir,
    )
    fdtoverlaymerge_bin = ensure_dtc_helper(
        "fdtoverlaymerge",
        args.fdtoverlaymerge,
        [
            rom_dir / "out/host/linux-x86/bin/fdtoverlaymerge",
            kp6_dir / "out/msm-kernel-pineapple-perf/host/bin/fdtoverlaymerge",
            kp6_dir / "kernel_platform/out/msm-kernel-pineapple-perf/host/bin/fdtoverlaymerge",
            HERE / "tools/fdtoverlaymerge",
        ],
        kp6_dir,
        rom_dir,
        work_dir,
    )
    mkdtboimg_tool = first_existing([
        args.mkdtboimg,
        kp6_dir / "prebuilts/kernel-build-tools/linux_musl-x86/bin/mkdtboimg",
        kp6_dir / "tools/mkbootimg/mkdtboimg.py",
        rom_dir / "out/host/linux-x86/bin/mkdtboimg",
        rom_dir / "system/libufdt/utils/src/mkdtboimg.py",
        rom_dir / "external/libufdt/utils/src/mkdtboimg.py",
        "mkdtboimg",
    ], "mkdtboimg")

    soc_inc = kp6_dir / "soc-repo/include"
    if not soc_inc.is_dir():
        soc_inc = kp6_dir / "kernel_platform/soc-repo/include"
    common_inc = kp6_dir / "common/include"
    if not common_inc.is_dir():
        common_inc = kp6_dir / "kernel_platform/common/include"

    include_dirs = [
        SRC_QCOM,
        soc_inc,
        common_inc,
        KERNEL_DIR / "techpacks-mm/src/camera-kernel",
        KERNEL_DIR / "techpacks-mm/src/synx-kernel",
        KERNEL_DIR / "techpacks-conn/src/audio-kernel/include",
        SRC_QCOM / "audio",
        SRC_QCOM / "camera",
        SRC_QCOM / "display/display",
        SRC_QCOM / "graphics/gpu",
        SRC_QCOM / "video",
        SRC_QCOM / "eva/eva",
        SRC_QCOM / "mm",
        SRC_QCOM / "mmrm/pineapple",
        SRC_QCOM / "mm-sys/ubwcp-devicetree",
        SRC_QCOM / "data",
        SRC_QCOM / "dsp",
        SRC_QCOM / "bt",
        SRC_QCOM / "synx",
        SRC_QCOM / "synx/synx",
        SRC_QCOM / "wlan",
    ]

    report = {
        "base_dtbs": [],
        "soc_overlays": {},
        "houji_board_overlays": [],
        "verification": [],
    }

    print(f"[1/5] Compiling {len(set(soc_techpack_overlays(True) + soc_techpack_overlays(False)))} SoC-level techpack overlays...")
    for rel in sorted(set(soc_techpack_overlays(True) + soc_techpack_overlays(False))):
        out_p = work_dir / "soc_ov" / (Path(rel).stem + ".dtbo")
        out_p.parent.mkdir(parents=True, exist_ok=True)
        compile_dts_path(SRC_QCOM / rel, out_p, work_dir, dtc_bin, include_dirs)
        report["soc_overlays"][rel] = {"dtbo": str(out_p), "size": out_p.stat().st_size}
        print(f"  OK {rel} ({out_p.stat().st_size} B)")

    print("\n[2/5] Compiling 4 base 6.18 pineapple DTBs + SoC techpacks in single pass...")
    final_base_dtbs: list[tuple[str, Path]] = []
    for name, rel_dts, is_v2 in BASE_DTBS:
        ov_rels = soc_techpack_overlays(is_v2)
        soc_incs = [build_soc_techpack_inc(r, work_dir / "soc_inc" / name) for r in ov_rels]
        comb_dts = work_dir / "base_combined" / f"{name}.combined.dts"
        comb_dts.parent.mkdir(parents=True, exist_ok=True)
        inc_lines = [f'#include "{SRC_QCOM / rel_dts}"'] + [f'#include "{p}"' for p in soc_incs]
        comb_dts.write_text("\n".join(inc_lines) + "\n")
        extra_incs = [(SRC_QCOM / r).parent for r in ov_rels]
        final_p = out_dir / f"{name}.dtb"
        compile_dts_path(comb_dts, final_p, work_dir, dtc_bin, include_dirs, extra_inc_dirs=extra_incs)
        final_base_dtbs.append((name, final_p))
        report["base_dtbs"].append({
            "name": name,
            "path": str(final_p),
            "merged_size": final_p.stat().st_size,
            "soc_techpacks_included": len(ov_rels),
        })
        print(f"  OK {name}.dtb ({len(ov_rels)} SoC techpacks in source order -> {final_p.stat().st_size} B)")

    dtb_img = out_dir / "dtb.img"
    with open(dtb_img, "wb") as out_f:
        for _, p in final_base_dtbs:
            out_f.write(p.read_bytes())
    print(f"  PACKED {dtb_img} ({dtb_img.stat().st_size} B, {len(final_base_dtbs)} concatenated DTBs)")

    print("\n[3/5] Compiling & verifying all 5 houji board overlays...")
    v2_base = out_dir / "pineapple-v2.dtb"
    v2_plus_board_fdt = work_dir / "verify_individual/v2_plus_houji-sm8650-overlay.fdt.dtb"
    compiled_board_ovs: list[Path] = []
    for idx, (name, rel_dts) in enumerate(HOUJI_BOARD_OVERLAYS):
        out_p = out_dir / "overlays" / f"{name}.dtbo"
        out_p.parent.mkdir(parents=True, exist_ok=True)
        compile_dts_path(SRC_QCOM / rel_dts, out_p, work_dir, dtc_bin, include_dirs)
        target_base = v2_base if idx == 0 else v2_plus_board_fdt
        test_out = work_dir / "verify_individual" / f"verify_{name}.ufdt.dtb"
        test_out.parent.mkdir(parents=True, exist_ok=True)
        run([str(ufdt_bin), str(target_base), str(out_p), str(test_out)], f"ufdt_apply_overlay {name} on {target_base.name}")
        if idx == 0:
            run([str(fdtoverlay_bin), "-i", str(v2_base), "-o", str(v2_plus_board_fdt), str(out_p)], "fdtoverlay houji-sm8650-overlay")
        compiled_board_ovs.append(out_p)
        report["houji_board_overlays"].append({
            "name": name,
            "dts": rel_dts,
            "dtbo": str(out_p),
            "size": out_p.stat().st_size,
            "verified_against": target_base.name,
            "ufdt_apply_overlay": "PASS",
        })
        print(f"  OK {name}.dtbo ({out_p.stat().st_size} B) -> ufdt_apply_overlay({target_base.name}) PASS")

    print("\n[4/5] Building unified houji-sm8650-merged.dtbo & out/dtbo.img...")
    unified_dtbo = out_dir / "houji-sm8650-merged.dtbo"
    run([str(fdtoverlaymerge_bin), "-i", str(compiled_board_ovs[0]), "-o", str(unified_dtbo)] + [str(p) for p in compiled_board_ovs[1:]], "fdtoverlaymerge")

    dtbo_img = out_dir / "dtbo.img"
    mkdtbo_prefix = [sys.executable, str(mkdtboimg_tool)] if mkdtboimg_tool.suffix == ".py" else [str(mkdtboimg_tool)]
    run([
        *mkdtbo_prefix, "create", str(dtbo_img),
        "--page_size=4096", "--version=0",
        str(unified_dtbo), "--id=0", "--rev=0",
        "--custom0=0", "--custom1=0", "--custom2=0", "--custom3=0",
    ], "mkdtboimg create dtbo.img")
    print(f"  PACKED {dtbo_img} ({dtbo_img.stat().st_size} B)")

    print("\n[5/5] End-to-end ufdt_apply_overlay verification across all 4 base DTBs...")
    for name, base_p in final_base_dtbs:
        merged_p = out_dir / f"{name}-houji-merged.dtb"
        run([str(ufdt_bin), str(base_p), str(unified_dtbo), str(merged_p)], f"final merge {name} + houji dtbo")
        report["verification"].append({
            "base": name,
            "merged_dtb": str(merged_p),
            "size": merged_p.stat().st_size,
            "ufdt_apply_overlay": "PASS",
        })
        print(f"  PASS {name}.dtb + houji-sm8650-merged.dtbo -> {merged_p.name} ({merged_p.stat().st_size} B)")

    merged_v2_dts = out_dir / "pineapple-v2-houji-merged.dts"
    run([str(dtc_bin), "-I", "dtb", "-O", "dts", "-o", str(merged_v2_dts), str(out_dir / "pineapple-v2-houji-merged.dtb")], "decompile merged v2")

    merged_txt = merged_v2_dts.read_text()
    for label, first_tok, second_tok in (
        ("cam_smmu jpeg before cre", "msm_cam_smmu_jpeg {", "msm_cam_smmu_cre {"),
        ("msm-audio-ion before cma", "qcom,msm-audio-ion {", "qcom,msm-audio-ion-cma {"),
        ("fastrpc cb1 before cb17", "qcom,msm_fastrpc_compute_cb1 {", "qcom,msm_fastrpc_compute_cb17 {"),
    ):
        p1 = merged_txt.find(first_tok)
        p2 = merged_txt.find(second_tok)
        assert p1 != -1 and p2 != -1 and p1 < p2, (
            f"Child node order check failed [{label}]: {first_tok}={p1}, {second_tok}={p2}"
        )
        print(f"  ORDER OK [{label}]: {first_tok} (@{p1}) < {second_tok} (@{p2})")

    (out_dir / "build_manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print("\nALL DTBs & DTBO BUILT AND VERIFIED SUCCESSFULLY!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
