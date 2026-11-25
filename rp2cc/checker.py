"""Independent rule and layout replay. Does not import or run the proposer."""

from math import gcd

from .model import (BINARY, MASK, MAX_NODES, SHIFT, blobs_of, check_expr, digest,
                    evaluate, fields, require, substitute, validate, word)

FORMAT = "rp2cc.cert.v1"


def _replay_rule(e, rule, result):
    if type(e) is not list:
        return False
    op, *a = e
    x, y = a[0], a[1] if len(a) > 1 else None
    if rule == "swar.floor2.bias":
        # Derived from independent 12-bit sign extension in 16-bit lanes.
        # This is the exact expression statement proved in formal/.
        return op == "floor2" and result == ["xor", ["add", ["and", ["lshr", x, 4], 268374015], 2013296640], 2013296640]
    if rule == "affine.jump":
        if op != "affine_iter":
            return False
        # Check directly from the source recurrence, independently of squaring.
        a0, b0 = 1, 0
        for _ in range(a[1]):
            a0, b0 = a[2] * a0 & MASK, (a[2] * b0 + a[3]) & MASK
        return result == ["add", ["mul", x, a0], b0]
    if rule == "fold.u32":
        return op in BINARY | SHIFT | {"not"} and all(word(v) for v in a) and result == evaluate(e, {})
    if rule == "identity.u32":
        if op in ("add", "xor", "or", "mul", "and"):
            n = {"add": 0, "xor": 0, "or": 0, "mul": 1, "and": MASK}[op]
            return (x == n and result == y) or (y == n and result == x)
        return op in ("sub", "shl", "lshr") and y == 0 and result == x
    if rule == "annihilator.u32":
        if op not in ("mul", "and", "or"):
            return False
        n = MASK if op == "or" else 0
        return (x == n or y == n) and result == n
    if rule == "same.u32":
        return x == y and ((op in ("and", "or") and result == x) or (op in ("xor", "sub") and result == 0))
    if rule == "involution.u32":
        return op == "not" and type(x) is list and x[0] == "not" and result == x[1]
    if rule == "cancel.u32":
        return type(x) is list and (op, x[0]) in (("add", "sub"), ("sub", "add")) and x[2] == y and result == x[1]
    return False


def check_kernel(k, cert, mode):
    fields(cert, ("name", "args", "body", "steps"))
    require(k["name"] == cert["name"] and k["args"] == cert["args"], "kernel ABI mismatch")
    names = set(k["args"])
    current = substitute(k["body"], k.get("known", {}))
    steps = cert["steps"]
    require(type(steps) is list and len(steps) <= MAX_NODES * 4, "invalid step count")
    require(mode != "baseline" or not steps, "baseline cannot contain rewrite steps")
    for i, step in enumerate(steps):
        fields(step, ("rule", "path", "after"))
        rule, path, after = step["rule"], step["path"], step["after"]
        require(type(rule) is str and type(path) is list and len(path) <= 400, "invalid proof step")
        check_expr(after, names, [MAX_NODES * 6], expanded=True)
        node = current
        parents = []
        for pos in path:
            require(type(pos) is int and type(node) is list and 0 <= pos < len(node) - 1, "invalid certificate path")
            parents.append((node, pos + 1))
            node = node[pos + 1]
        require(_replay_rule(node, rule, after), f"{k['name']}: rejected step {i} ({rule})")
        node = after
        for parent, j in reversed(parents):
            node = parent[:j] + [node] + parent[j+1:]
        current = node
    check_expr(cert["body"], names, [MAX_NODES * 6], expanded=True)
    require(cert["body"] == current, f"{k['name']}: final expression does not match replay")


def pool_lower_bound(source):
    """A universal lower bound for this *byte pool*, not for the linked image.

    Let E be the largest occupied end. Both every nonempty start and every
    object length are multiples of g, hence E is a multiple of g. All distinct
    source bytes and every individual object fit inside [0, E). Optional
    unobserved trailing padding cannot decrease the required pool length.
    """
    g, longest, octets = 0, 0, set()
    for _, data, alignment in blobs_of(source):
        if data:
            g = gcd(gcd(g, alignment), len(data))
            longest = max(longest, len(data))
            octets.update(data)
    g = g or 1  # the empty problem has bound zero; define its reporting unit as one byte
    count = len(octets)
    bound = (max(count, longest) + g - 1) // g * g
    return {"pool_lower_bound_bytes": bound, "pool_distinct_octets": count,
            "pool_alignment_length_gcd_bytes": g, "pool_longest_blob_bytes": longest}


def check_pool(source, candidate, mode="optimized"):
    fields(candidate, ("bytes", "offsets"))
    h, loc = candidate["bytes"], candidate["offsets"]
    require(type(h) is str and len(h) % 2 == 0 and len(h) <= 2 * (1024 * 1024 + 256 * 255), "bad pool size")
    try:
        pool = bytes.fromhex(h)
    except ValueError as exc:
        raise ValueError("invalid pool hex") from exc
    require(pool.hex() == h and type(loc) is dict, "invalid pool encoding/offsets")
    blobs = blobs_of(source)
    require(set(loc) == {n for n, _, _ in blobs}, "pool symbol mismatch")
    baseline = bytearray()
    baseline_offsets = {}
    for n, data, a in blobs:
        off = loc[n]
        require(type(off) is int and 0 <= off <= len(pool) and off % a == 0
                and len(data) <= len(pool) - off and pool[off:off + len(data)] == data,
                f"bad slice, alignment or bytes: {n}")
        expected = 0
        if data:
            baseline.extend(bytes(-len(baseline) % a))
            expected = len(baseline)
            baseline.extend(data)
        baseline_offsets[n] = expected
    require(len(pool) <= len(baseline), "pool candidate larger than baseline")
    if mode == "baseline" or len(pool) == len(baseline):
        require(bytes(baseline) == pool and baseline_offsets == loc, "non-improving layout must keep serial layout")
    bound = pool_lower_bound(source)
    require(len(pool) >= bound["pool_lower_bound_bytes"], "candidate violates independent pool size lower bound")
    return {**bound, "pool_optimal": len(pool) == bound["pool_lower_bound_bytes"]}


def check_cert(source, cert):
    validate(source)
    fields(cert, ("format", "mode", "source_sha256", "kernels", "pool", "files"))
    require(cert["format"] == FORMAT and cert["mode"] in ("baseline", "optimized"), "unknown certificate/mode")
    require(cert["source_sha256"] == digest(source), "source digest mismatch")
    require(type(cert["kernels"]) is list and len(cert["kernels"]) == len(source["kernels"]), "kernel count mismatch")
    for k, candidate in zip(source["kernels"], cert["kernels"]):
        check_kernel(k, candidate, cert["mode"])
    check_pool(source, cert["pool"], cert["mode"])
    fields(cert["files"], ("kernels.c", "kernels.h"))
