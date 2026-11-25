"""Compile the RP2350 SDK register-structure headers in isolation; no board, link or MMIO.

Uses genuine rp2lab firmware compile_commands.json files. Default: reproduce both
production compile lines, then repeat RISC-V with the SDK's Arm-header guard enabled.
Temporary ELF objects have to target the right ISA and allocate no target bytes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUBTREE = Path("src/rp2350/hardware_structs/include/hardware/structs")
GUARD = "PICO_FORBID_ARM_HEADERS_ON_RISCV"
DIAGNOSTIC = "Arm header included in a RISC-V build with " + GUARD + "=1"
STRUCT = re.compile(r"^[ \t]*typedef\s+struct\b[^;{]*\{.*?^}\s*(\w+)\s*;", re.M | re.S)
FORWARD = re.compile(r'^\s*#\s*include\s+"hardware/structs/([^"/]+\.h)"', re.M)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative(path):
    try:
        return Path(os.path.relpath(Path(path).resolve(), ROOT)).as_posix()
    except ValueError:  # different Windows volume: do not publish absolute host paths
        return Path(path).name


def split_command(command):
    if os.name != "nt":
        return shlex.split(command)
    # CMake writes MS CRT quoting (not POSIX shlex); preserve paths with spaces
    # and quoted -D... string macros without running any shell.
    import ctypes
    count = ctypes.c_int()
    split = ctypes.windll.shell32.CommandLineToArgvW
    ptrtype = ctypes.POINTER(ctypes.c_wchar_p)
    split.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    split.restype = ptrtype
    raw = split(command, ctypes.byref(count))
    if not raw:
        raise OSError("cannot parse CMake command")
    try:
        return list(raw[:count.value])
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(raw, ctypes.c_void_p))


def inventory(sdk):
    prefix = sdk / SUBTREE
    rows = []
    for path in sorted(prefix.rglob("*.h")):
        data = path.read_bytes()
        s = data.decode("utf-8")
        local_types = STRUCT.findall(s)
        forward = FORWARD.findall(s) if not local_types else []
        if not local_types and len(forward) != 1:
            raise ValueError("unclassified SDK header: " + relative(path))
        assertions = [line.strip() for line in s.splitlines() if re.match(r"\s*static_assert\s*\(", line)]
        rows.append(dict(header=path.relative_to(prefix).as_posix(), sha256=sha(data),
                         kind="structure" if local_types else "compatibility-forwarder",
                         direct_struct_typedefs=local_types, forwards_to=forward,
                         sdk_direct_static_assertions=assertions,
                         sdk_arm_only_guard=bool(re.search(r"^#if defined\(__riscv\) && " + GUARD + r"\s*$", s, re.M))))
    if not rows:
        raise ValueError("no SDK headers at " + relative(prefix))
    return rows


def configuration(arch, database, sdk):
    # Prefer the existing cross-linked SDK contract. On a newly built project
    # the ordinary firmware TU has the same SDK hardware_structs dependency.
    explicit = database is not None
    files = [database] if explicit else sorted((ROOT / ".build").glob(f"rp2354a-{arch}-*/compile_commands.json"))
    choice = None
    for db in files:
        entries = json.loads(db.read_text(encoding="utf-8"))
        for rank, name in enumerate(("sdk_contract.c", "rp2lab_io.c")):
            entry = next((e for e in entries if Path(e["file"]).name == name), None)
            if entry and (choice is None or rank < choice[0]):
                choice = rank, db.resolve(), entry
        if choice and choice[0] == 0:
            break
    if not choice:
        raise ValueError("missing " + arch + " firmware compile database; build the rp2354a profile first")
    _, db, row = choice
    cwd = Path(row["directory"]).resolve()
    args = row.get("arguments") or split_command(row["command"])
    cc = Path(args[0])
    flags, i = [], 1
    while i < len(args):
        arg = args[i]
        if arg in ("-o", "-MF", "-MT", "-MQ", "-MJ"):
            i += 2
        elif arg in ("-MD", "-MMD", "-MP", "-c") or arg == row["file"] or Path(arg).name == Path(row["file"]).name:
            i += 1
        else:
            flags.append(arg)
            i += 1
    if not cc.is_absolute():
        import shutil
        cc = Path(shutil.which(str(cc)) or (cwd / cc))
    cc = cc.resolve()
    if not cc.is_file() or not cwd.is_dir():
        raise ValueError("stale compilation database; rebuild firmware")
    if any(f in ("-E", "-S", "-fsyntax-only") or f.startswith("@") for f in flags):
        raise ValueError("unexpected non-object/response-file compilation command")
    version_header = cwd / "generated/pico_base/pico/version.h"
    version = re.search(r'#define\s+PICO_SDK_VERSION_STRING\s+"([^"]+)"', version_header.read_text())[1]
    if version != "2.3.1":
        raise ValueError("this inventory targets Pico SDK 2.3.1: got " + version)
    def normalized(flag):
        flag = flag.replace("\\", "/")
        for path, alias in ((cwd, "<build>"), (sdk, "<sdk>"), (ROOT, "<project>")):
            flag = flag.replace(path.as_posix(), alias)
        return flag
    normalized_flags = [normalized(f) for f in flags]
    info = dict(compile_database=relative(db), reference_translation_unit=relative(row["file"]),
                compiler=relative(cc), compiler_version=subprocess.check_output([cc, "--version"], text=True).splitlines()[0],
                target_triple=subprocess.check_output([cc, "-dumpmachine"], text=True).strip(),
                sdk_version=version, expected_elf_machine=40 if arch == "arm" else 243,
                cmake_flags=normalized_flags, cmake_flags_sha256=sha(json.dumps(normalized_flags).encode()))
    return cc, cwd, flags, info


def translation_unit(arch, row):
    target = "defined(__thumb__) && defined(__ARM_ARCH_8M_MAIN__)" if arch == "arm" else "defined(__riscv) && __riscv_xlen == 32"
    lines = [f"#include <hardware/structs/{row['header']}>", f"#if !({target})",
             '#error "wrong target architecture"', "#endif", '#if !defined(PICO_RP2350) || !PICO_RP2350',
             '#error "wrong SDK platform"', "#endif", '_Static_assert(sizeof(void *) == 4, "32-bit ABI");']
    for t in row["direct_struct_typedefs"]:
        lines.append(f'_Static_assert(sizeof({t}) > 0, "incomplete {t}");')
    return ("\n".join(lines) + "\n").encode("utf-8")


def object_check(data, expected):
    if (len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01" or
            struct.unpack_from("<HH", data, 16) != (1, expected)):
        raise ValueError("not a relocatable ELF32 object of the expected architecture")
    shoff, = struct.unpack_from("<I", data, 32)
    entsize, n = struct.unpack_from("<HH", data, 46)
    if not n or entsize != 40 or shoff + n * entsize > len(data):
        raise ValueError("invalid ELF32 section table")
    for i in range(n):
        _, _, flags, _, _, size, *_ = struct.unpack_from("<10I", data, shoff + i * entsize)
        if flags & 2 and size:
            raise ValueError("static-check object emitted allocated target bytes")


def probe(task):
    mode, row, cfg, sdk, tmp = task
    arch = "riscv" if mode.startswith("riscv") else "arm"
    cc, cwd, flags, info = cfg
    strict = mode == "riscv_strict"
    guard_expected = strict and row["sdk_arm_only_guard"]
    source = translation_unit(arch, row)
    dst = tmp / (mode + "-" + row["header"].replace("/", "_") + ".o")
    guard = ["-U" + GUARD, "-D" + GUARD + "=1"] if strict else []
    p = subprocess.run([cc, *flags, *guard, "-H", "-x", "c", "-c", "-", "-o", dst],
                       cwd=cwd, input=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log = p.stderr.decode("utf-8", errors="replace")
    # Verify the compiler really read the enumerated file, not another SDK/header
    # with the same name somewhere else in the build's include path.
    found = [Path(s[2:].strip()) for s in log.splitlines() if s.startswith(". ")]
    expected_header = (sdk / SUBTREE / row["header"]).resolve()
    if not any((p if p.is_absolute() else cwd / p).resolve() == expected_header for p in found):
        raise ValueError("wrong or missing direct include for " + mode + ":" + row["header"])
    result = {"unit_sha256": sha(source)}
    if guard_expected:
        errors = [s for s in log.splitlines() if re.search(r"\b(?:fatal )?error:", s)]
        if p.returncode == 0 or len(errors) != 1 or DIAGNOSTIC not in errors[0] or dst.exists():
            raise ValueError("did not see the precise SDK guard rejection: " + row["header"])
        result.update(status="rejected-by-sdk-arm-only-guard", diagnostic=DIAGNOSTIC)
    else:
        if p.returncode:
            errors = [s for s in log.splitlines() if "error:" in s]
            raise ValueError("compile failed for " + mode + ":" + row["header"] + "\n" + "\n".join(errors))
        raw = dst.read_bytes()
        object_check(raw, info["expected_elf_machine"])
        result.update(status="compiled", object_sha256=sha(raw))
    return mode, row["header"], result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, default=ROOT / ".deps/pico-sdk-master")
    parser.add_argument("--arm-db", type=Path)
    parser.add_argument("--riscv-db", type=Path)
    parser.add_argument("--out", type=Path, help="write a relative-path JSON report; otherwise print counts")
    parser.add_argument("-j", "--jobs", type=int, default=4, choices=range(1, 33), metavar="1..32")
    a = parser.parse_args()
    sdk = a.sdk.resolve()
    rows = inventory(sdk)
    cfg = {arch: configuration(arch, db, sdk) for arch, db in (("arm", a.arm_db), ("riscv", a.riscv_db))}
    headers = {r["header"]: r for r in rows}
    modes = ("arm", "riscv", "riscv_strict")
    with tempfile.TemporaryDirectory(prefix="rp2-reg-") as tmp, ThreadPoolExecutor(a.jobs) as pool:
        tasks = [(m, row, cfg["arm" if m == "arm" else "riscv"], sdk, Path(tmp)) for m in modes for row in rows]
        for m, name, result in pool.map(probe, tasks):
            headers[name].setdefault("results", {})[m] = result
    counts = dict(inventoried_headers=len(rows), structure_headers=sum(r["kind"] == "structure" for r in rows),
                  compatibility_forwarders=sum(r["kind"] == "compatibility-forwarder" for r in rows),
                  direct_struct_typedefs=sum(len(r["direct_struct_typedefs"]) for r in rows),
                  headers_with_sdk_direct_asserts=sum(bool(r["sdk_direct_static_assertions"]) for r in rows),
                  sdk_direct_assertions=sum(len(r["sdk_direct_static_assertions"]) for r in rows))
    for m in modes:
        counts[m] = {s: sum(r["results"][m]["status"] == s for r in rows)
                     for s in ("compiled", "rejected-by-sdk-arm-only-guard")}
    report = dict(schema="rp2lab.sdk-register-headers.v1", sdk_root=relative(sdk), sdk_inventory_subtree=SUBTREE.as_posix(),
                  sdk_version="2.3.1", counts=counts, configurations={k: c[3] for k, c in cfg.items()},
                  modes={"arm": "as configured", "riscv": "as configured", "riscv_strict": GUARD + "=1"},
                  evidence_scope="Individual includes, SDK static assertions and nonzero sizeof each directly declared structure. GCC direct header path and zero-allocated-byte target ELF32 objects checked. No link, MMIO or hardware execution in this audit.",
                  separate_existing_evidence={"link_source":"firmware/sdk_contract.c",
                       "linked_manifests":["dist/firmware/sdk-contract-arm/manifest.json","dist/firmware/sdk-contract-riscv/manifest.json"],
                       "flash_and_sram_image_checks":"dist/firmware/audit.json",
                       "board_runs":[f"dist/bench/{r}/results.json" for r in ("arm","riscv","riscv-tuned")]},
                  headers=rows)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as err:
        print("audit_registers:", err, file=sys.stderr)
        sys.exit(1)
