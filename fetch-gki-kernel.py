#!/usr/bin/env python3
"""Fetch and KMI-verify the pinned official Google android14-6.1 GKI kernel + system_dlkm."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tarfile
import urllib.request

GKI_RELEASE = "6.1.176-android14-11-g6e799b788f49-ab16965211"
GKI_TAG = "android14-6.1-2026-09_r10"
GKI_BUILD_ID = "16965211"
GKI_BASE_URL = f"https://ci.android.com/builds/submitted/{GKI_BUILD_ID}/kernel_aarch64/latest/raw"

PINNED_ARTIFACTS = {
    "Image": "0b396f6d55136e6c3eba2b0e9daccf2fa711837e23afc2b476fbd44077abc5bc",
    "system_dlkm_staging_archive.tar.gz": "16b03dfadfe8482e1a243566d627feb80bcbd13c26a102005ebb316d93ba47a8",
    "vmlinux.symvers": "6860c3f330bd8d82f10fc58e4bc334eaee0a784633d24d64a075c63c63aba36c",
}


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def ensure_artifact(name: str, expected_sha256: str, cache_dir: Path) -> Path:
    dest = cache_dir / name
    if dest.is_file() and sha256_file(dest) == expected_sha256:
        return dest
    url = f"{GKI_BASE_URL}/{name}"
    print(f"Downloading {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "yaap-houji-gki-fetcher"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    actual = sha256_file(tmp)
    if actual != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{name}: SHA-256 mismatch: {actual} != {expected_sha256}")
    tmp.replace(dest)
    return dest


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("yaap_dir", type=Path, help="Path to the synced YAAP repo root")
    parser.add_argument("--cache-dir", type=Path, help="Optional directory to cache/reuse downloaded artifacts")
    args = parser.parse_args()

    yaap = args.yaap_dir.resolve()
    stock = yaap / "vendor/xiaomi/houji/kernel/stock"
    out_dir = yaap / "kernel/prebuilts/houji-gki"
    cache_dir = (args.cache_dir or (yaap / ".gki-cache")).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    downloaded = {
        name: ensure_artifact(name, expected, cache_dir)
        for name, expected in PINNED_ARTIFACTS.items()
    }

    stage = out_dir.with_name(".houji-gki-staging")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    shutil.copy2(downloaded["Image"], stage / "Image")
    shutil.copy2(downloaded["vmlinux.symvers"], stage / "vmlinux.symvers")
    os.chmod(stage / "Image", 0o644)
    os.chmod(stage / "vmlinux.symvers", 0o644)

    sys_dlkm_root = stage / "system_dlkm"
    expected_prefix = f"lib/modules/{GKI_RELEASE}/"
    with tarfile.open(downloaded["system_dlkm_staging_archive.tar.gz"], "r:gz") as tf:
        for member in tf.getmembers():
            norm = member.name.lstrip("./")
            if not (norm == expected_prefix.rstrip("/") or norm.startswith(expected_prefix)):
                continue
            target = sys_dlkm_root / norm
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, 0o755)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.chmod(target, 0o644)

    new_sys_kos = sorted((sys_dlkm_root / expected_prefix).rglob("*.ko"))
    if len(new_sys_kos) != 60:
        raise ValueError(f"Expected 60 system_dlkm modules, found {len(new_sys_kos)}")

    module_sets = {"system_dlkm": new_sys_kos}
    if (stock / "vendor_ramdisk").is_dir() and (stock / "vendor_dlkm").is_dir():
        module_sets["vendor_ramdisk"] = sorted((stock / "vendor_ramdisk").rglob("*.ko"))
        module_sets["vendor_dlkm"] = sorted((stock / "vendor_dlkm").rglob("*.ko"))
    kmi_summary = verify_kmi_crcs(stage / "vmlinux.symvers", module_sets)

    provenance = {
        "gki_release": GKI_RELEASE,
        "gki_tag": GKI_TAG,
        "gki_build_id": GKI_BUILD_ID,
        "source_base_url": GKI_BASE_URL,
        "artifact_sha256": PINNED_ARTIFACTS,
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
        f"Staged official Google GKI {GKI_RELEASE} ({GKI_TAG}) -> {out_dir}\n"
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
