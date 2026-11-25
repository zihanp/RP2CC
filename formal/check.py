"""Clean Lean build, transitive axiom audit, and replay of real compiler artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from instances import emit as emit_instances


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = ("Proofs.lean", "Checker.lean", "PoolExample.lean", "Hazard3.lean", "Replay.lean", "Audit.lean")
AUDITED = {
    "floor_correct", "lane_is_floor", "decoded_floors", "affine_correct", "dirty_restore",
    "slice_sound", "subst_sound", "norm_sound", "accepts_sound", "demo_lower84", "demo_attains84",
    "hazard3_correct",
}
ALLOWED = {"propext", "Quot.sound", "Classical.choice"}


def find_lean(explicit):
    if explicit:
        return str(Path(explicit).resolve())
    if os.environ.get("RP2CC_LEAN"):
        return str(Path(os.environ["RP2CC_LEAN"]).resolve())
    for exe in (ROOT.parent / "elan/toolchains/leanprover--lean4---v4.33.1/bin/lean.exe",
                ROOT.parent / "elan/toolchains/leanprover--lean4---v4.33.1/bin/lean"):
        if exe.is_file():
            return str(exe)
    if found := shutil.which("lean"):
        return found
    raise ValueError("Lean not found: install Lean 4.33.1 or pass --lean PATH")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def named(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()


def unique_json(path, limit):
    """Reject ambiguous duplicate keys before feeding original bytes independently to Lean."""
    def unique(items):
        d = {}
        for k,v in items:
            if k in d:
                raise ValueError(f"{path}: repeated JSON key {k!r}")
            d[k] = v
        return d
    def nonfinite(t):
        raise ValueError(f"{path}: invalid JSON number {t}")
    if path.stat().st_size > limit:
        raise ValueError(f"{path}: exceeds {limit} bytes")
    json.loads(path.read_bytes().decode("utf-8-sig"), object_pairs_hook=unique, parse_constant=nonfinite)


def run(lean, env, *args, expected=0, reason=None):
    proc = subprocess.run([lean, *map(str, args)], cwd=HERE, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=180)
    output = proc.stdout.strip()
    if proc.returncode != expected or (reason and reason not in output) or "warning:" in output:
        raise RuntimeError(f"Lean exited {proc.returncode} (expected {expected}) while running "
                           f"{' '.join(map(str, args))}:\n{output}")
    return output


def run_all(source, certs, lean, self_test=False, kernel_instances=False):
    source = Path(source).resolve(strict=True)
    certs = [Path(c).resolve(strict=True) for c in certs]
    unique_json(source, 4*1024*1024)
    for c in certs:
        unique_json(c, 12*1024*1024)
    # Exclude stale or user-supplied olean files. Check exactly the local sources in order.
    with tempfile.TemporaryDirectory(prefix="rp2cc-lean-") as tmp:
        work = Path(tmp)
        env = {**os.environ, "LEAN_PATH": str(work)}
        # Lake/Elan do not participate: only this executable and its sysroot are needed.
        env.pop("LEAN_SYSROOT", None)
        version = run(lean, env, "--version")
        if "version 4.33.1," not in version:
            raise ValueError(f"expected pinned Lean 4.33.1, found {version}")
        for item in SOURCES[:-2]:
            run(lean, env, "-j1", "-o", work/(Path(item).stem+".olean"), item)
        audit = run(lean, env, "-j1", "Audit.lean")
        axioms = {key: [a.strip() for a in value.split(",") if a.strip()] for key, value in
                  re.findall(r"'RP2\.(\w+)' depends on axioms:\s*\[([^\]]*)\]", audit, re.S)}
        axioms.update((k, []) for k in re.findall(r"'RP2\.(\w+)' does not depend on any axioms", audit))
        if axioms.keys() != AUDITED or any(set(ax)-ALLOWED for ax in axioms.values()):
            raise ValueError(f"missing theorem or non-standard axiom:\n{audit}")
        def replay(s, c, expected=0, reason="PASS "):
            return run(lean, env, "-j1", "--run", "Replay.lean", s, c,
                       expected=expected, reason=reason)
        results = [{"certificate": named(c), "sha256": digest(c), "result": replay(source, c)}
                   for c in certs]
        if kernel_instances:
            for n, (cert, result) in enumerate(zip(certs, results, strict=True)):
                lean_text, count = emit_instances(source, cert)
                fixture_lean = work/f"Artifact{n}.lean"
                fixture_lean.write_text(lean_text, encoding="utf-8")
                run(lean, env, "-j1", fixture_lean)
                result["kernel_reduced_instance_theorems"] = count
                result["instance_lean_sha256"] = digest(fixture_lean)
        tests = {}
        if self_test:
            # Both positive and negative tests use the same Lean CLI as the user.
            # The synthetic certificate is for semantic replay; its unused hash/trace
            # fields do not purport to be a valid Python file-identity certificate.
            s = {"version": 1, "kernels":[{"name":"large", "args":["x"],
                        "body":["affine_iter", "x", 65535, 1, 1]}],
                 "blobs":[{"name":"obj", "hex":"aa00", "align":2}]}
            k = {"name":"large", "args":["x"], "steps":[], "body":["add","x",65535]}
            c = {"format":"rp2cc.cert.v1", "mode":"optimized", "source_sha256":"", "files":{},
                 "kernels":[k], "pool":{"bytes":"aa00", "offsets":{"obj":0}}}
            sp, cp = work/"fixture-source.json", work/"fixture-certificate.json"
            sp.write_text(json.dumps(s), encoding="utf-8")
            def fixture(key, expect=0, reason="PASS "):
                cp.write_text(json.dumps(c), encoding="utf-8")
                tests[key] = replay(sp, cp, expect, reason)
            fixture("max_65535_iterations")
            k["body"][-1] = 65534
            fixture("wrong_affine_constant", 1, "equality not certified")
            k["body"][-1] = 65535
            c["pool"]["bytes"] = "ab00"
            fixture("wrong_pool_byte", 1, "invalid slice or alignment")
            c["pool"] = {"bytes":"00aa00", "offsets":{"obj":1}}
            fixture("correct_bytes_wrong_alignment", 1, "invalid slice or alignment")
        return {"toolchain": version, "lean_build": "fresh temporary directory; -j1",
                "lean_source_sha256": {item:digest(HERE/item) for item in SOURCES},
                "validation_driver_sha256": {item:digest(HERE/item) for item in ("check.py", "instances.py")},
                "audited_theorems": axioms, "source": named(source), "source_file_sha256":digest(source),
                "replayed":results, "self_test":tests}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lean", help="direct path to pinned Lean 4.33.1")
    ap.add_argument("--source", type=Path, default=ROOT/"examples/bench.json")
    ap.add_argument("--cert", action="append", type=Path, help="certificate.json (can repeat)")
    ap.add_argument("--report", type=Path, help="write a JSON evidence record")
    ap.add_argument("--kernel-instances", action="store_true",
                    help="also elaborate concrete theorems for each kernel and blob (best for small programs)")
    ap.add_argument("--self-test", action="store_true", help="also exercise the maximum loop count and rejection cases")
    args = ap.parse_args()
    certs = args.cert or [ROOT/f"dist/generated/{mode}/certificate.json" for mode in ("baseline", "optimized")]
    try:
        result = run_all(args.source, certs, find_lean(args.lean), args.self_test, args.kernel_instances)
        print(result["toolchain"])
        print(f"PASS {len(result['audited_theorems'])} transitive axiom audits; allowed: {', '.join(sorted(ALLOWED))}")
        for row in result["replayed"]:
            print(row["result"])
            if "kernel_reduced_instance_theorems" in row:
                print(f"  {row['kernel_reduced_instance_theorems']} concrete kernel-reduced theorems")
        for name, row in result["self_test"].items():
            print(f"{name}: {row}")
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    except (OSError, ValueError, RuntimeError, RecursionError, subprocess.SubprocessError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
