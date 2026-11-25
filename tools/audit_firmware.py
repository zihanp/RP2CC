"""Re-audit the shipped images and print a compact, machine-readable inventory (no devices)."""
import argparse
import hashlib
import json
from pathlib import Path
from build_firmware import ROOT, PROFILES, compare_elf_uf2, inspect_uf2

def audit(folder):
    manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
    arch=manifest["architecture"]; ram=manifest["kind"]=="sram-only"
    if manifest["kind"] not in ("sram-only","flash"): raise ValueError("unknown image kind")
    sku,capacity=PROFILES[manifest["board"]]
    if sku!=manifest["sdk_board"] or capacity!=manifest["flash_capacity_bytes"]: raise ValueError("wrong board profile")
    for name,record in manifest["files"].items():
        if name not in ("rp_lab.elf","rp_lab.bin","rp_lab.uf2","rp_lab.elf.map","rp_lab.dis"):
            raise ValueError("unexpected manifest path")
        raw=(folder/name).read_bytes()
        if len(raw)!=record["bytes"] or hashlib.sha256(raw).hexdigest()!=record["sha256"]:
            raise ValueError("manifest bytes/hash mismatch: "+str(folder/name))
    e=(folder/"rp_lab.elf").read_bytes(); u=(folder/"rp_lab.uf2").read_bytes()
    info=inspect_uf2(u,arch,ram,capacity)
    info.update(compare_elf_uf2(e,u,arch,ram,capacity))
    if any(k in manifest and manifest[k]!=v for k,v in info.items()): raise ValueError("manifest facts mismatch")
    return {"target":folder.name,"board":manifest["board"],"sdk_version":manifest["sdk_version"],
        "gcc":manifest["gcc"],"kind":manifest["kind"],"arch":arch,"flash_capacity":capacity,
        "image_bytes":manifest["files"]["rp_lab.bin"]["bytes"],"uf2_bytes":len(u),
        "application_uf2_pages":info["payload_pages"],"elf_load_bytes_compared":info["elf_load_bytes_compared"],
        "family":info["family_id"],"e10_marker":info["e10_marker"],
        "sha256_uf2":hashlib.sha256(u).hexdigest()}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--root",type=Path,default=ROOT/"dist/firmware")
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    files=sorted(a.root.glob("*/manifest.json"))
    if not files: p.error("no firmware manifests to audit")
    text=json.dumps({"schema":"rp2lab.firmware.audit.v1","images":[audit(f.parent) for f in files]},indent=2)+"\n"
    if a.out: a.out.write_text(text,encoding="utf-8")
    else: print(text,end="")

if __name__=="__main__": main()
