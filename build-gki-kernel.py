#!/usr/bin/env python3
"""Build, patch, KMI-verify, and stage the pinned android14-6.1 (6.1.182) GKI kernel + system_dlkm."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request

GKI_BRANCH = "android14-6.1"
GKI_KMI_GENERATION = "11"
GKI_REPO_URL = "https://android.googlesource.com/kernel/common"
GKI_UPSTREAM_COMMIT = "7bae370c0f35c4188943c4d8a4d5a357d157767f"
CLANG_VERSION = "r487747c"
CLANG_URL = (
    "https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86/"
    f"+archive/refs/heads/android14-release/clang-{CLANG_VERSION}.tar.gz"
)
DEFAULT_PATCH = Path(__file__).resolve().parent / "patches/gki_common/0001-Revert-drm-Add-valid-clones-check.patch"
DEFAULT_CONTAINER = "localhost/houji-aosp-builder:ubuntu24.04"
# Unused system_dlkm modules on Xiaomi 14 (houji): MediaTek hypervisor, runtime header archive,
# and unused socket families (AF_CAN, AF_IEEE802154/6LoWPAN) that expand Ring-0 attack surface.
# Note: AF_TIPC (tipc + diag) is required by Qualcomm SM8650 nicmd/tlpd IPC for DSI/IMS/data.
SYSTEM_DLKM_PRUNE = frozenset({
    "gzvm", "kheaders",
    "can", "can_raw", "can_bcm", "can_gw", "can_dev", "vcan", "slcan",
    "ieee802154", "ieee802154_socket", "ieee802154_6lowpan", "mac802154",
    "6lowpan", "nhc_dest", "nhc_fragment", "nhc_hop", "nhc_ipv6", "nhc_mobility", "nhc_routing", "nhc_udp",
})


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def parse_elf(data: bytes):
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("Not a little-endian ELF64 object")
    e_shoff = struct.unpack_from("<Q", data, 40)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 58)
    shstr_off, shstr_sz = struct.unpack_from("<QQ", data, e_shoff + e_shstrndx * e_shentsize + 24)
    shstr = data[shstr_off : shstr_off + shstr_sz]
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


def extract_exported_crcs(ko_path: Path) -> dict[str, int]:
    data = ko_path.read_bytes()
    by_idx, by_name = parse_elf(data)
    if ".symtab" not in by_name or ".strtab" not in by_name:
        return {}
    _, sym_off, sym_sz, entsz = by_name[".symtab"]
    _, str_off, str_sz, _ = by_name[".strtab"]
    strtab = data[str_off : str_off + str_sz]
    out: dict[str, int] = {}
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
            out[name[6:]] = crc
    return out


def extract_required_versions(ko_path: Path) -> list[tuple[str, int]]:
    data = ko_path.read_bytes()
    _, by_name = parse_elf(data)
    if "__versions" not in by_name:
        return []
    _, off, sz, _ = by_name["__versions"]
    reqs: list[tuple[str, int]] = []
    for pos in range(off, off + sz, 64):
        crc = struct.unpack_from("<Q", data, pos)[0] & 0xFFFFFFFF
        name = data[pos + 8 : pos + 64].split(b"\x00", 1)[0].decode("utf-8", "ignore")
        if name:
            reqs.append((name, crc))
    return reqs


def verify_kmi_crcs(symvers_path: Path, module_sets: dict[str, list[Path]]) -> dict:
    vmlinux_crcs: dict[str, int] = {}
    for line in symvers_path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            vmlinux_crcs[parts[1]] = int(parts[0], 16)
    mod_crcs: dict[str, int] = {}
    for paths in module_sets.values():
        for p in paths:
            for sym, crc in extract_exported_crcs(p).items():
                mod_crcs.setdefault(sym, crc)
    mismatches = []
    missing = []
    counts = {}
    total_refs = 0
    for label, paths in module_sets.items():
        refs = 0
        for p in paths:
            for sym, req_crc in extract_required_versions(p):
                refs += 1
                if sym in vmlinux_crcs:
                    if vmlinux_crcs[sym] != req_crc:
                        mismatches.append((label, p.name, sym, hex(req_crc), hex(vmlinux_crcs[sym])))
                elif sym in mod_crcs:
                    if mod_crcs[sym] != req_crc:
                        mismatches.append((label, p.name, sym, hex(req_crc), hex(mod_crcs[sym])))
                else:
                    missing.append((label, p.name, sym, hex(req_crc)))
        counts[label] = {"modules": len(paths), "symbol_refs": refs}
        total_refs += refs
    if mismatches or missing:
        raise ValueError(
            f"KMI CRC check failed: {len(mismatches)} mismatches ({mismatches[:5]}), "
            f"{len(missing)} missing ({missing[:5]})"
        )
    return {"total_symbol_refs": total_refs, "trees": counts}


def ensure_clang(toolchain_dir: Path) -> None:
    if (toolchain_dir / "bin/clang").is_file():
        return
    toolchain_dir.mkdir(parents=True, exist_ok=True)
    archive = toolchain_dir.parent / f"clang-{CLANG_VERSION}.tar.gz"
    print(f"Downloading {CLANG_URL} ...", flush=True)
    req = urllib.request.Request(CLANG_URL, headers={"User-Agent": "yaap-houji-gki-builder"})
    with urllib.request.urlopen(req, timeout=300) as resp, archive.open("wb") as out:
        shutil.copyfileobj(resp, out)
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(toolchain_dir, filter="tar")
    archive.unlink(missing_ok=True)


def ensure_patched_source(common_dir: Path, patch_path: Path) -> None:
    if not (common_dir / ".git").is_dir():
        common_dir.mkdir(parents=True, exist_ok=True)
        run(["git", "init"], cwd=common_dir)
        run(["git", "remote", "add", "origin", GKI_REPO_URL], cwd=common_dir)
    run(["git", "fetch", "--depth=1", "origin", GKI_UPSTREAM_COMMIT], cwd=common_dir)
    run(["git", "checkout", "--force", "FETCH_HEAD"], cwd=common_dir)
    run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=common_dir)
    run(["git", "clean", "-fdx"], cwd=common_dir)
    run(
        [
            "git",
            "-c", "user.name=yaap-houji",
            "-c", "user.email=build@yaap-houji.local",
            "am", "--3way", str(patch_path),
        ],
        cwd=common_dir,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("yaap_dir", type=Path, help="Path to the synced YAAP repo root")
    parser.add_argument("--work-dir", type=Path, help="Kernel build/cache workspace (default: <yaap_dir>/../.kernel-build)")
    parser.add_argument("--patch", type=Path, default=DEFAULT_PATCH, help="DRM clone-check revert patch path")
    parser.add_argument("--container", default=DEFAULT_CONTAINER, help="Podman builder image (pass '' for host build)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 8, help="Parallel make jobs")
    parser.add_argument("--restage-only", action="store_true", help="Restage from existing .kernel-build outputs without rebuilding")
    args = parser.parse_args()

    yaap = args.yaap_dir.resolve()
    patch_path = args.patch.resolve()
    if not patch_path.is_file():
        raise FileNotFoundError(f"Patch not found: {patch_path}")
    kbt_dir = yaap / "prebuilts/kernel-build-tools/linux-x86"
    if not (kbt_dir / "bin/pahole").is_file():
        raise FileNotFoundError(f"Missing kernel-build-tools pahole under {kbt_dir}")

    work_dir = (args.work_dir or (yaap.parent / ".kernel-build")).resolve()
    common_dir = work_dir / "common"
    toolchain_dir = work_dir / "toolchain"
    out_build = work_dir / "out"
    mod_install = work_dir / "mod_install"

    if not args.restage_only:
        ensure_patched_source(common_dir, patch_path)
        ensure_clang(toolchain_dir)

        build_script = f"""
set -euo pipefail
rm -rf "$KOUT" "$MOD_INSTALL"
mkdir -p "$KOUT"
MAKE_ARGS=(
  O="$KOUT"
  ARCH=arm64
  LLVM=1
  LLVM_IAS=1
  BRANCH="{GKI_BRANCH}"
  KMI_GENERATION="{GKI_KMI_GENERATION}"
  KCFLAGS="-D__ANDROID_COMMON_KERNEL__"
  DEPMOD=depmod
  DTC=dtc
)
make "${{MAKE_ARGS[@]}}" gki_defconfig
make -j{args.jobs} "${{MAKE_ARGS[@]}}" Image modules
make -j{args.jobs} "${{MAKE_ARGS[@]}}" INSTALL_MOD_PATH="$MOD_INSTALL" modules_install
"""

        if args.container:
            cmd = [
                "podman", "run", "--rm", "--userns=keep-id",
                "-v", f"{work_dir}:/kwork:Z",
                "-v", f"{kbt_dir}:/kbt:ro,Z",
                "-w", "/kwork/common",
                "-e", "PATH=/kwork/toolchain/bin:/kbt/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "-e", "LD_LIBRARY_PATH=/kbt/lib64",
                "-e", "KBUILD_BUILD_USER=build-user",
                "-e", "KBUILD_BUILD_HOST=build-host",
                "-e", "KOUT=/kwork/out",
                "-e", "MOD_INSTALL=/kwork/mod_install",
                args.container, "bash", "-lc", build_script,
            ]
            run(cmd)
        else:
            env = os.environ.copy()
            env["PATH"] = f"{toolchain_dir / 'bin'}:{kbt_dir / 'bin'}:{env.get('PATH', '')}"
            env["LD_LIBRARY_PATH"] = f"{kbt_dir / 'lib64'}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
            env["KBUILD_BUILD_USER"] = "build-user"
            env["KBUILD_BUILD_HOST"] = "build-host"
            env["KOUT"] = str(out_build)
            env["MOD_INSTALL"] = str(mod_install)
            subprocess.run(["bash", "-lc", build_script], cwd=common_dir, env=env, check=True)

    release = (out_build / "include/config/kernel.release").read_text().strip()
    image_src = out_build / "arch/arm64/boot/Image"
    symvers_src = out_build / "vmlinux.symvers"
    if b"failed valid clone check" in image_src.read_bytes():
        raise ValueError("Built Image still contains drm_atomic_check_valid_clones")

    mod_src = mod_install / "lib/modules" / release
    out_dir = yaap / "kernel/prebuilts/houji-gki"
    stage = out_dir.with_name(".houji-gki-staging")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    shutil.copy2(image_src, stage / "Image")
    shutil.copy2(symvers_src, stage / "vmlinux.symvers")
    os.chmod(stage / "Image", 0o644)
    os.chmod(stage / "vmlinux.symvers", 0o644)

    dlkm_dst = stage / "system_dlkm/lib/modules" / release
    dlkm_dst.mkdir(parents=True)
    for item in mod_src.iterdir():
        if item.name in ("build", "source"):
            continue
        dst = dlkm_dst / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
    kept_load = [
        line for line in (dlkm_dst / "modules.order").read_text().splitlines()
        if line.strip() and Path(line.strip()).stem.replace("-", "_") not in SYSTEM_DLKM_PRUNE
    ]
    (dlkm_dst / "modules.load").write_text("\n".join(kept_load) + "\n")
    for p in dlkm_dst.rglob("*"):
        os.chmod(p, 0o755 if p.is_dir() else 0o644)

    new_sys_kos = sorted(dlkm_dst.rglob("*.ko"))
    if len(new_sys_kos) != 60:
        raise ValueError(f"Expected 60 system_dlkm modules, found {len(new_sys_kos)}")

    stock = yaap / "vendor/xiaomi/houji/kernel/stock"
    module_sets = {"system_dlkm": new_sys_kos}
    if (stock / "vendor_ramdisk").is_dir() and (stock / "vendor_dlkm").is_dir():
        module_sets["vendor_ramdisk"] = sorted((stock / "vendor_ramdisk").rglob("*.ko"))
        module_sets["vendor_dlkm"] = sorted((stock / "vendor_dlkm").rglob("*.ko"))
    kmi_summary = verify_kmi_crcs(stage / "vmlinux.symvers", module_sets)

    provenance = {
        "gki_release": release,
        "gki_branch": GKI_BRANCH,
        "gki_upstream_commit": GKI_UPSTREAM_COMMIT,
        "gki_build_mode": f"source-built-{GKI_BRANCH}-clang-{CLANG_VERSION}",
        "patches": [
            {
                "file": patch_path.name,
                "sha256": sha256_file(patch_path),
                "summary": (
                    "Revert upstream Linux ffb55ddf2685 (drm: Add valid clones check) "
                    "for Qualcomm 6.1.68 msm_drm.ko CWB clone-mode compatibility"
                ),
            }
        ],
        "artifact_sha256": {
            "Image": sha256_file(stage / "Image"),
            "vmlinux.symvers": sha256_file(stage / "vmlinux.symvers"),
        },
        "system_dlkm_module_count": len(new_sys_kos),
        "kmi_verification": kmi_summary,
    }
    (stage / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")

    sums = []
    for p in sorted(stage.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS":
            sums.append(f"{sha256_file(p)}  {p.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    stage.rename(out_dir)

    print(
        f"Staged source-built GKI {release} -> {out_dir}\n"
        f"KMI verified: {kmi_summary['total_symbol_refs']} symbol refs across "
        f"{sum(t['modules'] for t in kmi_summary['trees'].values())} modules (0 mismatches, 0 missing)."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
