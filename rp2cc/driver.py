"""Compile with checked fallback; verify supplied certificates without fallback."""

import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from . import checker, codegen, propose
from .model import Invalid, blobs_of, digest, load_json, require, substitute, validate

CERT_LIMIT = 64 * 1024 * 1024


def _baseline(k):
    return {"name": k["name"], "args": k["args"], "body": substitute(k["body"], k.get("known", {})), "steps": []}


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("ascii")


def compile_source(source, mode="optimized"):
    validate(source)
    require(mode in ("baseline", "optimized"), "mode must be baseline or optimized")
    blobs = blobs_of(source)
    plain_pool = propose.baseline_pool(blobs)
    kernels, fallback, pool = [], [], plain_pool
    for k in source["kernels"]:
        checked = _baseline(k)
        if mode == "optimized":
            try:
                option = propose.propose_kernel(k)
                checker.check_kernel(k, option, mode)
                checked = option
            except (Invalid, RecursionError) as exc:
                fallback.append({"kernel": k["name"], "reason": str(exc)})
        kernels.append(checked)
    if mode == "optimized":
        try:
            option = propose.propose_pool(blobs)
            checker.check_pool(source, option)
            pool = option
        except (ValueError, RecursionError) as exc:
            fallback.append({"pool": True, "reason": str(exc)})
    cert = {"format": checker.FORMAT, "mode": mode, "source_sha256": digest(source),
            "kernels": kernels, "pool": pool, "files": {"kernels.c": "", "kernels.h": ""}}
    # The complete verifier is also used on the fallback before any C is emitted.
    checker.check_cert(source, cert)
    outputs = codegen.render(source, cert)
    cert["files"] = codegen.hashes(outputs)
    stats = {"mode": mode, "rules": dict(Counter(st["rule"] for k in kernels for st in k["steps"])),
             "fallback": fallback, "kernels": len(kernels), "pool_bytes": len(pool["bytes"]) // 2,
             "baseline_pool_bytes": len(plain_pool["bytes"]) // 2,
             **checker.check_pool(source, pool, mode)}
    require(len(_json_bytes(cert)) <= CERT_LIMIT, "certificate exceeds 64 MiB; split your source module")
    return cert, outputs, stats


def _atomic_write(path, data):
    # One atomic replace per named file; write the certificate last as the commit record.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".rp2cc-", delete=False) as tmp:
        tempname = tmp.name
        try:
            tmp.write(data)
        except BaseException:
            tmp.close()
            os.unlink(tempname)
            raise
    try:
        os.replace(tempname, path)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)


def write_artifacts(source_path, out, mode):
    p, d = Path(source_path), Path(out)
    cert, outputs, stats = compile_source(load_json(p), mode)
    records = {**outputs, "stats.json": _json_bytes(stats), "certificate.json": _json_bytes(cert)}
    require(all((d / k).resolve() != p.resolve() for k in records), "output would overwrite source")
    d.mkdir(parents=True, exist_ok=True)
    for name, data in records.items():
        _atomic_write(d / name, data)
    return stats


def verify_artifacts(source_path, cert_path):
    p = Path(cert_path)
    source, cert = load_json(source_path), load_json(p, CERT_LIMIT)
    checker.check_cert(source, cert)
    expected = codegen.render(source, cert)
    require(cert["files"] == codegen.hashes(expected), "generated file hashes differ from deterministic replay")
    for name, data in expected.items():
        with (p.parent / name).open("rb") as f:
            require(f.read(len(data) + 1) == data, f"generated file differs from checked lowering: {name}")
    return cert
