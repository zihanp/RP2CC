"""Serialize accepted IR/data to closed Lean terms for optional per-artifact kernel checking.

Used only *after* independent direct JSON replay in Lean. No untrusted string is
inserted as Lean syntax: variable names are checked ASCII identifiers and quoted;
all numbers are bounded decimal integers; all constructors are fixed strings.
"""
import json
import re


def nat(n, limit=4294967295):
    if type(n) is not int or not 0 <= n <= limit:
        raise ValueError("not an admitted bounded natural number")
    return str(n)


def sym(s):
    if type(s) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", s, re.ASCII):
        raise ValueError("unrecognized identifier")
    return json.dumps(s, ensure_ascii=True)


def term(e):
    if type(e) is int:
        return f"(Expr.lit {nat(e)})"
    if type(e) is str:
        return f"(Expr.var {sym(e)})"
    op, *a = e
    bins = {"add":"add", "sub":"sub", "mul":"mul", "or":"bor", "and":"band", "xor":"bxor"}
    if op in bins:
        return f"(Expr.bin .{bins[op]} {term(a[0])} {term(a[1])})"
    if op in ("not", "floor2"):
        return f"(Expr.{'compl' if op=='not' else 'floor2'} {term(a[0])})"
    if op in ("lshr", "shl"):
        return f"(Expr.{op} {term(a[0])} {nat(a[1],31)})"
    if op == "affine_iter":
        return f"(Expr.affine {term(a[0])} {nat(a[1],65535)} {nat(a[2])} {nat(a[3])})"
    raise ValueError("unrecognized operation")


def octets(h):
    return "["+",".join(map(str,bytes.fromhex(h)))+"]"


def emit(source, candidate):
    src = json.loads(source.read_text(encoding="utf-8-sig"))
    cert = json.loads(candidate.read_text(encoding="utf-8-sig"))
    lines = ["import Checker", "open RP2", "namespace RP2.Artifact", "set_option maxRecDepth 16000",
             "set_option maxHeartbeats 1000000"]
    count = 0
    for i, (sk,dk) in enumerate(zip(src["kernels"],cert["kernels"],strict=True)):
        bindings = ",".join(f"({sym(k)}, {nat(v)})" for k,v in sk.get("known",{}).items())
        lines += [f"def k{i} (s : String) : Option Word := ([{bindings}] : List (String × Word)).lookup s",
                  f"def s{i} := {term(sk['body'])}", f"def d{i} := {term(dk['body'])}",
                  f"theorem kernel{i} (ρ : Env) : d{i}.eval ρ = s{i}.eval (fun v => (k{i} v).getD (ρ v)) :=",
                  f"  accepts_sound (known := k{i}) (src := s{i}) (dst := d{i}) (by decide) ρ"]
        count += 1
    lines.append(f"def pool : List Byte := {octets(cert['pool']['bytes'])}")
    for i,b in enumerate(src.get("blobs",[])):
        ofs = nat(cert['pool']['offsets'][b['name']])
        lines += [f"def b{i} : Object := ⟨{octets(b['hex'])}, {nat(b['align'],256)}⟩",
                  f"theorem bytes{i} (j : Nat) : poolRead pool {ofs} b{i}.data.length j = b{i}.data[j]?.getD 0 :=",
                  f"  (slice_sound (pool := pool) (obj := b{i}) (off := {ofs}) (by decide)).2.2 j"]
        count += 1
    return "\n".join(lines+["end RP2.Artifact", ""]), count
