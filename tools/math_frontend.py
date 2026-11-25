"""Restricted, human-readable source for RP2CC. Parse Python notation; never execute it.

    python tools/math_frontend.py examples/jump.rpm --out dist/generated/jump.json
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from rp2cc.model import Invalid, MASK, MAX_ITER, digest, identifier, primitive, require, validate, word


BIN = {ast.Add: 'add', ast.Sub: 'sub', ast.Mult: 'mul', ast.BitAnd: 'and', ast.BitOr: 'or',
       ast.BitXor: 'xor', ast.LShift: 'shl', ast.RShift: 'lshr'}
RESERVED = {'range', 'u32', 'lane_floor16'}
LIMIT_BYTES = 128 * 1024


def fail(node, message):
    raise Invalid(f"line {getattr(node, 'lineno', '?')}:{getattr(node, 'col_offset', 0)+1}: {message}")


def name(s, node):
    if not identifier(s) or s in RESERVED: fail(node, f"reserved or invalid name: {s}")
    return s


def target(stmt):
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
        fail(stmt, 'expected one assignment to a word variable')
    return name(stmt.targets[0].id, stmt.targets[0])


def atomic(node, constants):
    """Evaluate a compile-time constant without evaluating Python source or dynamic state."""
    if isinstance(node, ast.Constant) and word(node.value): return node.value
    if isinstance(node, ast.Name) and node.id in constants: return constants[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub, ast.Invert):
        v = atomic(node.operand, constants)
        return (v if isinstance(node.op, ast.UAdd) else -v if isinstance(node.op, ast.USub) else ~v) & MASK
    if isinstance(node, ast.BinOp) and type(node.op) in BIN:
        op = BIN[type(node.op)]
        a, b = atomic(node.left, constants), atomic(node.right, constants)
        if op in ('shl','lshr') and not 0 <= b < 32: fail(node, 'constant shift count must be 0..31')
        return primitive(op, (a,b))
    fail(node, 'expected a compile-time uint32 constant')


def linear(node, state, constants):
    """Structurally derive (a,b) with expr(z) = a*z+b modulo 2^32."""
    if isinstance(node, ast.Name) and node.id == state: return (1,0)
    if isinstance(node, ast.Name) or isinstance(node, ast.Constant):
        return (0, atomic(node, constants))
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
        a,b = linear(node.operand, state, constants)
        return (a,b) if isinstance(node.op, ast.UAdd) else (-a & MASK, -b & MASK)
    if isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult):
        a,b = linear(node.left, state, constants)
        c,d = linear(node.right, state, constants)
        if isinstance(node.op, ast.Add): return ((a+c) & MASK, (b+d) & MASK)
        if isinstance(node.op, ast.Sub): return ((a-c) & MASK, (b-d) & MASK)
        if a and c: fail(node, 'nonlinear product: both factors depend on the loop state')
        return ((a*d+b*c) & MASK, (b*d) & MASK)
    # Constant-only subexpressions can contain bit operators. Bit operations on a
    # dynamic state are deliberately not guessed to be affine over Z/(2^32).
    try: return (0, atomic(node, constants))
    except Invalid: fail(node, 'unrecognized recurrence: use constants and +, - or * with at most one state-dependent factor')


def expression(node, bindings, constants):
    if isinstance(node, ast.Constant) and word(node.value): return node.value
    if isinstance(node, ast.Name) and node.id in bindings: return bindings[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub, ast.Invert):
        v = expression(node.operand, bindings, constants)
        if isinstance(node.op, ast.UAdd): return v
        return ['sub',0,v] if isinstance(node.op, ast.USub) else ['not',v]
    if isinstance(node, ast.BinOp) and type(node.op) in BIN:
        op = BIN[type(node.op)]
        x = expression(node.left, bindings, constants)
        if op in ('shl','lshr'):
            k = atomic(node.right, constants)
            if k >= 32: fail(node, 'word shift count must be 0..31')
            return [op,x,k]
        return [op,x,expression(node.right, bindings, constants)]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'lane_floor16'
            and len(node.args) == 1 and not node.keywords):
        return ['floor2', expression(node.args[0], bindings, constants)]
    fail(node, 'unsupported word expression or unbound name (no arbitrary calls, memory, or effects)')


def translate_function(fn, constants, trace):
    name(fn.name,fn)
    args = fn.args
    if (fn.decorator_list or getattr(fn,'type_params',[]) or args.posonlyargs or args.vararg or args.kwarg
        or args.kwonlyargs or args.defaults or args.kw_defaults): fail(fn,'unsupported function signature')
    if not isinstance(fn.returns, ast.Name) or fn.returns.id != 'u32': fail(fn,'return must be annotated u32')
    params = []
    for p in args.args:
        name(p.arg,p)
        if p.arg in constants or p.arg in params: fail(p,'duplicate parameter or name shadows a global constant')
        if not isinstance(p.annotation, ast.Name) or p.annotation.id != 'u32': fail(p,'argument must be annotated u32')
        params.append(p.arg)
    bindings = {n:n for n in (*constants, *params)}
    static = dict(constants)
    body = None
    for i,s in enumerate(fn.body):
        if isinstance(s, ast.Return):
            if i != len(fn.body)-1 or s.value is None: fail(s,'exactly one final return expression is required')
            body = expression(s.value, bindings, static)
        elif isinstance(s, ast.Assign):
            v = target(s)
            if v in constants: fail(s,'cannot rebind a global constant')
            result = expression(s.value, bindings, static)
            try: val = atomic(s.value,static)
            except Invalid: static.pop(v,None)
            else: static[v] = val
            bindings[v] = result
        elif isinstance(s, ast.For):
            it = s.iter
            if (not isinstance(s.target, ast.Name) or s.target.id != '_' or s.orelse or len(s.body) != 1
                 or not isinstance(it, ast.Call) or not isinstance(it.func, ast.Name) or it.func.id != 'range'
                 or len(it.args) != 1 or it.keywords): fail(s,'loop must be for _ in range(K), with exactly one assignment and no else')
            n = atomic(it.args[0],static)
            if n > MAX_ITER: fail(s,'fixed recurrence count must be 0..65535')
            assignment = s.body[0]; state = target(assignment)
            if state not in bindings or state in constants: fail(assignment,'recurrence state must be an initialized, mutable local or argument')
            # The old static value is an initial value, never the varying recurrence state.
            loop_constants = {v:k for v,k in static.items() if v != state}
            a,b = linear(assignment.value, state, loop_constants)
            bindings[state] = ['affine_iter',bindings[state],n,a,b]
            static.pop(state,None)
            trace.append({'kernel':fn.name,'line':s.lineno,'state':state,'iterations':n,'multiplier':a,'increment':b})
        else: fail(s,'only local assignments, fixed affine recurrences and one final return are supported')
    if body is None: fail(fn,'missing final return')
    k = {'name':fn.name,'args':params,'body':body}
    if constants: k['known'] = dict(constants)
    return k


def translate(text):
    require(len(text.encode('utf-8')) <= LIMIT_BYTES, 'math source exceeds 128 KiB')
    try: tree = ast.parse(text, mode='exec')
    except (SyntaxError, RecursionError) as e: raise Invalid(f'invalid math syntax: {e}') from e
    require(sum(1 for _ in ast.walk(tree)) <= 4096, 'math source exceeds 4096 syntax nodes')
    constants, kernels, trace, declared = {}, [], [], set()
    for s in tree.body:
        if isinstance(s, ast.Assign):
            if kernels: fail(s,'declare constants before all kernels')
            key = target(s)
            if key in declared: fail(s,'duplicate declaration')
            constants[key] = atomic(s.value, constants); declared.add(key)
        elif isinstance(s, ast.FunctionDef):
            if s.name in declared: fail(s,'duplicate declaration')
            kernels.append(translate_function(s, constants, trace)); declared.add(s.name)
        else: fail(s,'only global constants and u32 kernels are supported')
    source = validate({'version':1,'kernels':kernels})
    return source, {'format':'rp2cc.math-front.v1','source_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    'ir_canonical_sha256':digest(source),'recurrences':trace}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Parse restricted u32 math notation into RP2CC v1 JSON (no eval).')
    ap.add_argument('source',type=Path); ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--trace',type=Path,help='optional parse/recognition receipt, not a Lean proof')
    ns = ap.parse_args(argv)
    try:
        source_path = ns.source.resolve()
        destinations = [p.resolve() for p in (ns.out,ns.trace) if p is not None]
        require(len(set(destinations)) == len(destinations) and source_path not in destinations, 'source/output paths must be distinct')
        with ns.source.open('rb') as f: raw = f.read(LIMIT_BYTES + 1)
        require(len(raw) <= LIMIT_BYTES,'math source exceeds 128 KiB')
        source,trace = translate(raw.decode('utf-8-sig'))
        # Source digest in the on-disk receipt identifies original bytes incl BOM/newlines.
        trace['source_sha256'] = hashlib.sha256(raw).hexdigest()
        for path,data in ((ns.out,source),(ns.trace,trace)):
            if path is not None:
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(data,ensure_ascii=True,indent=2)+'\n',encoding='utf-8')
        print(f"translated {len(source['kernels'])} kernels, recognized {len(trace['recurrences'])} fixed recurrence(s)")
        return 0
    except (ValueError, OSError, RecursionError) as e:
        print(f'math_frontend: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
