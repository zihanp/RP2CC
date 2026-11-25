"""The accepted input language, not the optimizer. No untyped C escape hatch."""

import hashlib
import json
import re
from pathlib import Path

MASK = (1 << 32) - 1
BINARY = frozenset(("add", "sub", "mul", "and", "or", "xor"))
SHIFT = frozenset(("shl", "lshr"))
OPS = BINARY | SHIFT | {"not", "floor2", "affine_iter"}
MAX_ITER = 65535
MAX_NODES = 8192
MAX_DEPTH = 80
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}\Z", re.ASCII)


class Invalid(ValueError):
    """A source program, certificate or requested operation violates its contract."""


def require(condition, message):
    if not condition:
        raise Invalid(message)


def fields(obj, required, optional=()):
    require(type(obj) is dict, "expected a JSON object")
    r = set(required)
    require(r <= set(obj) <= r | set(optional),
            f"keys: expected {sorted(r)}, optional {sorted(optional)}; got {sorted(obj)}")


def load_json(path, limit=4 * 1024 * 1024):
    with Path(path).open("rb") as f:
        data = f.read(limit + 1)
    require(len(data) <= limit, f"input exceeds {limit} bytes")

    def unique(pairs):
        obj = {}
        for k, v in pairs:
            require(k not in obj, f"duplicate JSON key: {k}")
            obj[k] = v
        return obj

    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique,
                          parse_constant=lambda s: (_ for _ in ()).throw(Invalid(f"invalid JSON: {s}")))
    except (ValueError, RecursionError, UnicodeError) as e:
        raise Invalid(f"invalid JSON ({path}): {e}") from e


def digest(source):
    payload = json.dumps(source, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def word(x):
    return type(x) is int and 0 <= x <= MASK


def identifier(s):
    return type(s) is str and IDENT.fullmatch(s) is not None and "__" not in s


def check_expr(e, names, budget, depth=0, expanded=False):
    budget[0] -= 1
    require(budget[0] >= 0 and depth <= (MAX_DEPTH * 5 if expanded else MAX_DEPTH),
            "expression exceeds depth/node limit")
    if word(e) or (type(e) is str and e in names):
        return
    require(type(e) is list and e and type(e[0]) is str and e[0] in OPS,
            "invalid expression/unknown variable or operation; effects belong in a separate SDK translation unit")
    op = e[0]
    arity = 4 if op == "affine_iter" else 2 if op in BINARY | SHIFT else 1
    require(len(e) == arity + 1, f"{op}: expected {arity} operands")
    if op in SHIFT:
        require(word(e[2]) and e[2] < 32, "shift amount must be a literal in 0..31")
    if op == "affine_iter":
        require(word(e[2]) and e[2] <= MAX_ITER and word(e[3]) and word(e[4]),
                "affine_iter expects literal count 0..65535 and literal uint32 coefficients")
    for v in e[1:]:
        check_expr(v, names, budget, depth + 1, expanded)


def validate(source):
    fields(source, ("version", "kernels"), ("blobs",))
    require(type(source["version"]) is int and source["version"] == 1, "expected IR version 1")
    ks, bs = source["kernels"], source.get("blobs", [])
    require(type(ks) is list and 0 < len(ks) <= 128, "expected 1..128 kernels")
    require(type(bs) is list and len(bs) <= 256, "expected at most 256 blobs")
    used = set()
    budget = [MAX_NODES]
    for k in ks:
        fields(k, ("name", "args", "body"), ("known",))
        name, args, known = k["name"], k["args"], k.get("known", {})
        require(identifier(name) and not name.startswith("rom_") and name not in used,
                "invalid, reserved, or duplicate kernel name")
        used.add(name)
        require(type(args) is list and len(args) <= 16 and all(identifier(a) for a in args)
                and len(set(args)) == len(args), "invalid or repeated dynamic parameter")
        require(type(known) is dict and len(known) <= 128
                and all(identifier(a) and word(v) for a, v in known.items())
                and set(args).isdisjoint(known), "invalid or shadowed known parameter")
        check_expr(k["body"], set(args) | set(known), budget)
    used.clear()
    byte_count = 0
    for b in bs:
        fields(b, ("name", "hex", "align"))
        n, h, a = b["name"], b["hex"], b["align"]
        require(identifier(n) and n not in used, "invalid/duplicate blob name")
        used.add(n)
        require(type(h) is str and len(h) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]*", h, re.ASCII) is not None,
                f"{n}: invalid hex (use two digits per byte, no spaces)")
        require(type(a) is int and a in (1, 2, 4, 8, 16, 32, 64, 128, 256),
                f"{n}: alignment must be a power of two from 1 to 256")
        byte_count += len(h) // 2
    require(byte_count <= 1024 * 1024, "at most 1 MiB of source blob data")
    return source


def substitute(e, known):
    if type(e) is list:
        return [e[0], *(substitute(x, known) for x in e[1:])]
    return known.get(e, e) if type(e) is str else e


def primitive(op, a):
    x = a[0]
    if op == "not":
        return ~x & MASK
    y = a[1]
    if op == "add": return (x + y) & MASK
    if op == "sub": return (x - y) & MASK
    if op == "mul": return (x * y) & MASK
    if op == "and": return x & y
    if op == "or": return x | y
    if op == "xor": return x ^ y
    if op == "shl": return (x << y) & MASK
    if op == "lshr": return x >> y
    raise Invalid("unknown primitive")


def evaluate(e, env):
    """Source interpreter. Signed floor uses Python mathematical // deliberately."""
    if type(e) is int:
        return e
    if type(e) is str:
        return env[e]
    op, *args = e
    v = [evaluate(x, env) for x in args]
    if op == "floor2":
        def lane(t):
            t &= 0xffff
            return ((t - (0x10000 if t >= 0x8000 else 0)) // 16) & 0xffff
        return lane(v[0]) | (lane(v[0] >> 16) << 16)
    if op == "affine_iter":
        x, n, a, b = v
        for _ in range(n):
            x = (x * a + b) & MASK
        return x
    return primitive(op, v)


def blobs_of(source):
    return [(b["name"], bytes.fromhex(b["hex"]), b["align"]) for b in source.get("blobs", [])]
