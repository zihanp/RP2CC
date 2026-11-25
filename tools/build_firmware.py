"""Compile an rp2cc translation unit into a complete Raspberry Pi SDK ELF + UF2.

No device discovery, flashing, reboot or external installation is performed.
Additional native C/C++/ASM and PIO files are explicit trusted application inputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / ".deps"
MIB = 1024 * 1024
PROFILES = {
    "rp2350": ("rp2lab_2350", 4*MIB),       # pin-neutral external-flash QFN60
    "rp2350b": ("rp2lab_2350b", 4*MIB),      # pin-neutral external-flash QFN80
    "hw973a": ("rp2lab_2350", 4*MIB),
    "hw973b": ("rp2lab_973b", 16*MIB),
    "rp2354a": ("rp2lab_2354a", 2*MIB),    # in-package flash
    "pico2": ("pico2", 4*MIB),              # official target, quad-SPI stage2
}
FAMILY = {"arm": 0xe48bff59, "riscv": 0xe48bff5a}


def inspect_uf2(raw: bytes, arch: str, ram: bool, flash_size: int):
    """Accept exactly the normal SDK image (and its optional E10 framing block)."""
    if not raw or len(raw) % 512: raise ValueError("UF2 is empty or truncated")
    normal, workaround = [], None
    for i in range(0, len(raw), 512):
        b = raw[i:i+512]
        m0,m1,flags,addr,n,num,total,fam = struct.unpack_from("<8I", b)
        if (m0, m1, struct.unpack_from("<I", b, 508)[0]) != (0x0a324655, 0x9e5d5157, 0x0ab16f30):
            raise ValueError("invalid UF2 magic")
        if fam == 0xe48bff57:
            if (ram or i or flags != 0xa000 or n != 256 or num != 0 or total != 2
                or addr != 0x10000000 + flash_size - 256 or b[32:288] != bytes([0xef])*256
                or struct.unpack_from("<I", b, 288)[0] != 0x9957e304 or any(b[292:508])):
                raise ValueError("unexpected E10 block")
            workaround = addr
            continue
        if fam != FAMILY[arch] or flags != 0x2000 or n != 256 or addr%256:
            raise ValueError("wrong UF2 platform, flags or page size")
        lo, hi = ((0x20000000, 0x20082000) if ram else (0x10000000, 0x10000000+flash_size-4096))
        if not lo <= addr <= hi-n: raise ValueError("image page outside profile or reserved E10 sector")
        if normal and normal[-1][1] >= addr: raise ValueError("reordered/overlapping load page")
        normal.append((num, addr, total))
    if not normal or any(i != k or n != len(normal) for k,(i,a,n) in enumerate(normal)):
        raise ValueError("inconsistent normal UF2 page numbering")
    if not ram and workaround is None: raise ValueError("missing SDK RP2350-A2 erratum block")
    if normal[0][1] != (0x20000000 if ram else 0x10000000):
        raise ValueError("unexpected image base")
    return {"architecture":arch, "kind":"sram-only" if ram else "flash",
            "family_id":hex(FAMILY[arch]), "uf2_bytes":len(raw), "payload_pages":len(normal),
            "first":hex(normal[0][1]), "end_exclusive":hex(normal[-1][1]+256),
            "e10_marker":None if workaround is None else hex(workaround)}


def compare_elf_uf2(elf: bytes, uf2: bytes, arch: str, ram: bool, flash_size: int):
    """Reconstruct every normal UF2 byte from ELF32 PT_LOAD, independent of picotool."""
    if len(elf)<52: raise ValueError("ELF header truncated")
    ident,typ,machine,version,entry,phoff,_,__,ehsize,phsize,nph,*rest = struct.unpack_from("<16sHHIIIIIHHHHHH",elf)
    if (ident[:7] != b"\x7fELF\x01\x01\x01" or typ!=2 or version!=1 or ehsize!=52 or
        machine!={"arm":40,"riscv":243}[arch] or phsize!=32 or not nph or phoff+nph*32>len(elf)):
        raise ValueError("invalid ELF32 executable or architecture")
    if entry & 1 != int(arch=="arm"): raise ValueError("unexpected ISA entry alignment")
    sram = (0x20000000,0x20082000)
    flash = (0x10000000,0x10000000+flash_size-4096)
    spans = [sram] if ram else [flash,sram]
    pages, occupied = {},[]
    contains_entry=False; filebytes=0
    for i in range(nph):
        kind,offset,vaddr,paddr,filesz,memsz,flags,align = struct.unpack_from("<8I",elf,phoff+32*i)
        if kind!=1: continue
        if filesz>memsz or (filesz and offset+filesz>len(elf)) or (
            memsz and not any(lo<=vaddr<=hi-memsz for lo,hi in spans)):
            raise ValueError("invalid ELF load segment; address or file range")
        if flags & 1 and vaddr <= (entry & ~1) < vaddr+filesz: contains_entry=True
        if not filesz: continue   # BSS/stack reserve runtime RAM but add no UF2 bytes
        lo,hi = sram if ram else flash
        if not lo<=paddr<=hi-filesz: raise ValueError("ELF load address out of profile")
        if any(paddr<end and start<paddr+filesz for start,end in occupied):
            raise ValueError("overlapping ELF load bytes")
        occupied.append((paddr,paddr+filesz))
        j=0
        while j<filesz:
            addr=paddr+j; base=addr&~255
            if base not in pages: pages[base]=bytearray(256)
            start=addr-base; n=min(256-start,filesz-j)
            pages[base][start:start+n]=elf[offset+j:offset+j+n]
            j+=n
        filebytes+=filesz
    if not pages or not contains_entry: raise ValueError("ELF entry not in executable file-backed memory")
    if not ram:
        # picotool fills holes in touched Flash erase sectors up to the final page.
        last=max(pages)
        for sec in {base&~4095 for base in pages}:
            for page in range(sec,min(sec+4096,last),256):
                pages.setdefault(page,bytearray(256))
    actual = {}
    for pos in range(0,len(uf2),512):
        addr,=struct.unpack_from("<I",uf2,pos+12)
        fam,=struct.unpack_from("<I",uf2,pos+28)
        if fam==FAMILY[arch]: actual[addr]=uf2[pos+32:pos+288]
    if actual!=pages: raise ValueError("UF2 payload differs from linked ELF bytes or SDK page padding")
    return {"elf_machine":machine,"elf_entry":hex(entry),"elf_load_bytes_compared":filebytes,
            "verified_uf2_payload_bytes":len(pages)*256}


def cmd_log(cmd, log, env=None, cwd=None):
    cmd = list(map(str, cmd))
    print(">", subprocess.list2cmdline(cmd), flush=True)
    with log.open("a", encoding="utf-8") as f:
        f.write("> " + subprocess.list2cmdline(cmd) + "\n"); f.flush()
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env, cwd=cwd)
    if p.returncode:
        print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-32:]), file=sys.stderr)
        raise RuntimeError(f"command failed ({p.returncode}); see {log}")


def cache_path(explicit, var, choices):
    if explicit: return Path(explicit).resolve()
    if os.environ.get(var): return Path(os.environ[var]).resolve()
    return next((x.resolve() for x in choices if x.exists()), None)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--generated", required=True, type=Path, help="directory with emitted kernels.c and kernels.h")
    p.add_argument("--out", required=True, type=Path, help="local output directory; never the BOOTSEL volume")
    p.add_argument("--board", choices=PROFILES, default="rp2350")
    p.add_argument("--arch", choices=FAMILY, default="arm")
    p.add_argument("--ram", action="store_true", help="no_flash SDK linker script, all UF2 payload addresses checked")
    p.add_argument("--app", type=Path, default=ROOT/"firmware/main.c", help="trusted main C/C++/ASM file")
    p.add_argument("--extra-source", type=Path, default=[], action="append")
    p.add_argument("--pio", type=Path, default=[], action="append", help="additional .pio assembled into .pio.h")
    p.add_argument("--sdk-lib", default=[], action="append", help="additional trusted SDK CMake library target")
    p.add_argument("--capability-smoke", action="store_true", help="force-retain typed SDK references, never call them")
    p.add_argument("--usb-stdio", action="store_true", help="TinyUSB CDC; main must call stdio_init_all(); no automatic reset commands")
    p.add_argument("--sdk", type=Path, help="installed Raspberry Pi pico-sdk directory")
    p.add_argument("--toolchain", type=Path, help="GCC bin directory or root (auto: .deps, PATH or PICO_TOOLCHAIN_PATH)")
    p.add_argument("--picotool-dir", type=Path, help="directory holding picotoolConfig.cmake")
    p.add_argument("--pioasm-dir", type=Path, help="directory holding pioasmConfig.cmake")
    p.add_argument("--build-dir", type=Path)
    p.add_argument("--opt", choices=["O2","O3","Os"], default="O3", help="SDK release flags used for the complete image")
    p.add_argument("-j","--jobs", type=int, default=2)
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    gen,out,app = a.generated.resolve(),a.out.resolve(),a.app.resolve()
    if (Path(out.anchor)/"INFO_UF2.TXT").is_file():
        raise ValueError("refusing to use a mounted BOOTSEL drive as a build output")
    sources = [p.resolve() for p in a.extra_source]
    pios = [p.resolve() for p in a.pio]
    if any(not x.is_file() for x in [app, gen/"kernels.c", gen/"kernels.h", *sources, *pios]):
        raise ValueError("missing generated unit, app, extra source or PIO file")
    import re
    if any(not re.fullmatch(r"[A-Za-z][a-zA-Z0-9_]*", n) for n in a.sdk_lib):
        raise ValueError("invalid native SDK library target")
    if any(";" in str(p) for p in [app, gen, *sources, *pios]):
        raise ValueError("CMake list separator not allowed in input file paths")
    if not 1 <= a.jobs <= 64: raise ValueError("jobs must be within 1..64")
    sdk = cache_path(a.sdk, "PICO_SDK_PATH", [DEPS/"pico-sdk-master", DEPS/"pico-sdk"])
    if not sdk or not (sdk/"pico_sdk_init.cmake").is_file(): raise ValueError("missing Pico SDK: pass --sdk")
    pico = cache_path(a.picotool_dir,"picotool_DIR",[DEPS/"picotool-host/picotool"])
    pio = cache_path(a.pioasm_dir,"pioasm_DIR",[DEPS/"pioasm-host/lib/cmake/pioasm"])
    tc_choices = ([*sorted(DEPS.glob("arm-gnu-*/bin")), DEPS/"bin"] if a.arch == "arm" else
                  [DEPS/"riscv/bin", *sorted(DEPS.glob("riscv/*/bin"))])
    tc = cache_path(a.toolchain, "PICO_TOOLCHAIN_PATH", tc_choices)
    if tc and (tc/"bin").is_dir(): tc = tc/"bin"
    cc_names = ["arm-none-eabi-gcc"] if a.arch=="arm" else ["riscv32-pico-elf-gcc","riscv32-unknown-elf-gcc",
                                                         "riscv32-corev-elf-gcc","riscv-none-elf-gcc","riscv64-unknown-elf-gcc"]
    search_path = str(tc) if tc else os.environ.get("PATH","")
    cc = next((shutil.which(c,path=search_path) for c in cc_names if shutil.which(c,path=search_path)), None)
    if not cc: raise ValueError("no suitable bare-metal GCC located; use --toolchain")
    cc = Path(cc).resolve()
    cmake = shutil.which("cmake")
    if not cmake or not shutil.which("ninja"): raise ValueError("cmake and ninja are required")
    sdk_board,capacity = PROFILES[a.board]
    platform = "rp2350-arm-s" if a.arch=="arm" else "rp2350-riscv"
    cfg = [Path(cmake).as_posix(), "-G", "Ninja", "-S", (ROOT/"firmware").as_posix(),
           f"-DPICO_BOARD={sdk_board}", f"-DPICO_PLATFORM={platform}",
           f"-DPICO_SDK_PATH={sdk.as_posix()}", f"-DPICO_TOOLCHAIN_PATH={cc.parent.as_posix()}",
           f"-DRP2_GENERATED={gen.as_posix()}",f"-DRP2_APP={app.as_posix()}",
           "-DRP2_EXTRA_SOURCES="+";".join(p.as_posix() for p in sources),
           "-DRP2_EXTRA_PIO="+";".join(p.as_posix() for p in pios),"-DRP2_EXTRA_LIBS="+";".join(a.sdk_lib),
           f"-DRP2_RAM={int(a.ram)}", f"-DRP2_CAPS={int(a.capability_smoke)}",
           f"-DRP2_USB_STDIO={int(a.usb_stdio)}",
           f"-DRP2_E10_ADDR={hex(0x10000000+capacity-256)}", "-DCMAKE_BUILD_TYPE=Release",
           f"-DCMAKE_C_FLAGS_RELEASE=-{a.opt} -DNDEBUG",f"-DCMAKE_CXX_FLAGS_RELEASE=-{a.opt} -DNDEBUG",
           "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
    if a.arch=="riscv":
        # SDK 2.3.1's own compiler probe flattens the compiler path into an
        # unquoted command string (breaks workspace paths containing spaces).
        # Test the same assembly-sensitive SDK specimens with proper argv.
        testdir=sdk/"cmake/preload/toolchains/util"
        candidates=[
            (["-mcpu=hazard3-rp2350","-mstrict-align"],"riscv_zcmp_test.c"),
            (["-march=rv32ima_zicsr_zifencei_zba_zbb_zbs_zbkb_zca_zcb_zcmp","-mabi=ilp32","-mstrict-align"],"riscv_zcmp_test.c"),
            (["-march=rv32imac_zicsr_zifencei_zba_zbb_zbs_zbkb","-mabi=ilp32","-mstrict-align"],"empty.c")]
        selected=next((flags for flags,fname in candidates if not subprocess.run(
            [cc,*flags,"-x","c","-c",testdir/fname,"-o",os.devnull],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode),None)
        if not selected: raise ValueError("GCC cannot assemble the SDK Hazard3 probes")
        cfg.extend(["-DPICO_GCC_TRIPLE="+cc.stem.removesuffix("-gcc"),"-DPICO_COMMON_LANG_FLAGS="+" ".join(selected),
                    "-DCMAKE_TRY_COMPILE_PLATFORM_VARIABLES=PICO_COMMON_LANG_FLAGS"])
    if pico: cfg.append("-Dpicotool_DIR="+pico.as_posix())
    if pio: cfg.append("-Dpioasm_DIR="+pio.as_posix())
    ident = hashlib.sha256(repr(cfg).encode()).hexdigest()[:12]
    build = (a.build_dir or ROOT/".build"/f"{a.board}-{a.arch}-{ident}").resolve()
    if (Path(build.anchor)/"INFO_UF2.TXT").is_file():
        raise ValueError("refusing to use a mounted BOOTSEL drive to build")
    build.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
    log = build/"build.log"
    with log.open("a", encoding="utf-8") as f: f.write("\n--- build invocation ---\n")
    cmd_log([*cfg,"-B",build],log)
    cmd_log([cmake,"--build",build,"--target","rp_lab","--parallel",str(a.jobs)],log)
    uf2=(build/"rp_lab.uf2").read_bytes()
    info = inspect_uf2(uf2,a.arch,a.ram,capacity)
    info.update(compare_elf_uf2((build/"rp_lab.elf").read_bytes(),uf2,a.arch,a.ram,capacity))
    delivered = {}
    for suffix in ["elf","uf2","bin","elf.map","dis"]:
        f = build / ("rp_lab."+suffix)
        if f.is_file():
            d = out/f.name; shutil.copyfile(f,d)
            delivered[f.name] = {"bytes":d.stat().st_size,"sha256":hashlib.sha256(d.read_bytes()).hexdigest()}
    info.update(board=a.board,sdk_board=sdk_board,flash_capacity_bytes=capacity,option=a.opt,
        sdk_version=re.search(r'#define\s+PICO_SDK_VERSION_STRING\s+"([^"]+)"',
            (build/"generated/pico_base/pico/version.h").read_text(encoding="utf-8"))[1],
        gcc=subprocess.check_output([cc,"--version"],text=True).splitlines()[0],
        capability_smoke=a.capability_smoke, usb_stdio=a.usb_stdio,
        generated_c_sha256=hashlib.sha256((gen/"kernels.c").read_bytes()).hexdigest(),
        files=delivered)
    (out/"manifest.json").write_text(json.dumps(info,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    shutil.copyfile(log,out/"build.log")
    print(json.dumps(info,indent=2))
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as e:
        print("build_firmware:",e,file=sys.stderr); sys.exit(2)
