import argparse
import json
import sys

from . import __version__
from .driver import verify_artifacts, write_artifacts
from .model import Invalid, evaluate, load_json, require, validate, word


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m rp2cc", description="RP235x certificate-checked pure word compiler")
    ap.add_argument("--version", action="version", version=__version__)
    cmd = ap.add_subparsers(dest="cmd", required=True)
    em = cmd.add_parser("emit", help="generate C11 and a replayable certificate")
    em.add_argument("source"); em.add_argument("--out", required=True)
    em.add_argument("--mode", choices=("baseline", "optimized"), default="optimized")
    ve = cmd.add_parser("verify", help="independently replay source/candidate and compare generated C/H")
    ve.add_argument("source"); ve.add_argument("--cert", required=True)
    ev = cmd.add_parser("eval", help="run source semantics with dynamic arguments in declared order")
    ev.add_argument("source"); ev.add_argument("kernel"); ev.add_argument("values", nargs="*", type=lambda a: int(a, 0))
    ns = ap.parse_args(argv)
    try:
        if ns.cmd == "emit":
            print(json.dumps(write_artifacts(ns.source, ns.out, ns.mode), ensure_ascii=True, sort_keys=True))
        elif ns.cmd == "verify":
            c = verify_artifacts(ns.source, ns.cert)
            print(f"verified: {len(c['kernels'])} kernels, {len(c['pool']['offsets'])} blobs, source + certificate + generated C/H")
        else:
            src = validate(load_json(ns.source))
            k = next((x for x in src["kernels"] if x["name"] == ns.kernel), None)
            require(k is not None, "no such kernel")
            require(len(ns.values) == len(k["args"]) and all(word(v) for v in ns.values), "expected one uint32 per dynamic argument")
            result = evaluate(k["body"], {**k.get("known", {}), **dict(zip(k["args"], ns.values))})
            print(f"{result} (0x{result:08x})")
        return 0
    except (OSError, ValueError, RecursionError) as ex:
        print(f"rp2cc: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
