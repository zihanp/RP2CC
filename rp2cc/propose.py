"""Untrusted optimization heuristics. Every result must pass checker.py."""

import operator

from .model import BINARY, MASK, SHIFT, substitute

_FOLD = {"add": operator.add, "sub": operator.sub, "mul": operator.mul,
         "and": operator.and_, "or": operator.or_, "xor": operator.xor, "not": operator.invert,
         "shl": operator.lshift, "lshr": operator.rshift}


def affine_power(n, a, b):
    """Exponentiate functions (A,B): x -> A*x+B in Z/(2^32)."""
    A, B = 1, 0
    while n:
        if n & 1:
            A, B = a * A & MASK, (a * B + b) & MASK
        a, b = a * a & MASK, (a * b + b) & MASK
        n >>= 1
    return A, B


def _change(e):
    op, x, *rest = e
    y = rest[0] if rest else None
    if op == "floor2":
        return "swar.floor2.bias", ["xor", ["add", ["and", ["lshr", x, 4], 0x0fff0fff], 0x78007800], 0x78007800]
    if op == "affine_iter":
        A, B = affine_power(*rest)
        return "affine.jump", ["add", ["mul", x, A], B]
    if all(type(t) is int for t in e[1:]):
        return "fold.u32", _FOLD[op](*e[1:]) & MASK
    if op in BINARY and (type(x) is int or type(y) is int):
        for c, v, left in ((x, y, True), (y, x, False)):
            if type(c) is not int:
                continue
            if ((op in ("add", "or", "xor") and c == 0) or (op == "mul" and c == 1)
                    or (op == "and" and c == MASK) or (op == "sub" and not left and c == 0)):
                return "identity.u32", v
            if (op in ("mul", "and") and c == 0) or (op == "or" and c == MASK):
                return "annihilator.u32", c
    if op in SHIFT and y == 0:
        return "identity.u32", x
    if op in ("and", "or", "xor", "sub") and x == y:
        return "same.u32", x if op in ("and", "or") else 0
    if op == "not" and type(x) is list and x[0] == "not":
        return "involution.u32", x[1]
    if op in ("add", "sub") and type(x) is list and x[0] == {"add": "sub", "sub": "add"}[op] and x[2] == y:
        return "cancel.u32", x[1]
    return None


def propose_kernel(kernel):
    body = substitute(kernel["body"], kernel.get("known", {}))
    steps = []

    def visit(e, path):
        if type(e) is not list:
            return e
        e = [e[0], *(visit(child, path + (i,)) for i, child in enumerate(e[1:]))]
        proposal = _change(e)
        if proposal is None:
            return e
        rule, after = proposal
        steps.append({"path": list(path), "rule": rule, "after": after})
        # New arithmetic under a special-form replacement may itself simplify.
        return visit(after, path) if rule in ("swar.floor2.bias", "affine.jump") else after

    return {"name": kernel["name"], "args": kernel["args"], "body": visit(body, ()), "steps": steps}


def baseline_pool(blobs):
    pool, offsets = bytearray(), {}
    for name, data, align in blobs:
        if not data:
            offsets[name] = 0
            continue
        pool.extend(bytes(-len(pool) % align))
        offsets[name] = len(pool)
        pool.extend(data)
    return {"bytes": pool.hex(), "offsets": offsets}


def _overlap(pool, data, alignment):
    """KMP border lengths, restricted to trailing starts aligned in the pool."""
    if not pool or not data:
        return 0
    pi = [0] * len(data)
    k = 0
    for i in range(1, len(data)):
        while k and data[i] != data[k]:
            k = pi[k - 1]
        if data[i] == data[k]:
            k += 1
        pi[i] = k
    k = 0
    for val in pool[-len(data):]:
        while k and val != data[k]:
            k = pi[k - 1]
        if val == data[k]:
            k += 1
    while k and (len(pool) - k) % alignment:
        k = pi[k - 1]
    return k


def _greedy(blobs):
    pool, offsets = bytearray(), {}
    for name, data, align in blobs:
        if not data:
            offsets[name] = 0
            continue
        pos = pool.find(data)
        while pos >= 0 and pos % align:
            pos = pool.find(data, pos + align - pos % align)
        if pos < 0:
            common = _overlap(pool, data, align)
            if not common:
                pool.extend(bytes(-len(pool) % align))
            pos = len(pool) - common
            pool.extend(data[common:])
        offsets[name] = pos
    return {"bytes": pool.hex(), "offsets": offsets}


def propose_pool(blobs):
    best = baseline_pool(blobs)
    for order in (blobs, sorted(blobs, key=lambda b: (-len(b[1]), -b[2], b[1], b[0])),
                  sorted(blobs, key=lambda b: b[1])):
        candidate = _greedy(order)
        if len(candidate["bytes"]) < len(best["bytes"]):
            best = candidate
    return best
