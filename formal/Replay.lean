import Checker
import PoolExample
import Lean.Data.Json.Parser

/-! Directly read the original source and final certificate JSON. No generated proof
source, Python AST translation or optimizer rule trace is used by this executable.
Checks semantic terms, known bindings, signatures, and the final private pool.
Use rp2cc's separate `verify` command for the trace, file hashes, and emitted C bytes. -/
namespace RP2.Replay
open Lean (Json)

abbrev Result := Except String

def ensure (ok : Bool) (err : String) : Result Unit := if ok then pure () else throw err

def fields (j : Json) (required optional : List String) : Result Unit := do
  let keys := (← j.getObj?).toList.map Prod.fst
  ensure (required.all keys.contains && keys.all (required ++ optional).contains)
    s!"unexpected or missing fields: {keys}"

def fallback (j : Json) (k : String) (d : Json) : Result Json := do
  return ((← j.getObj?).get? k).getD d

def bounded (j : Json) (n : Nat) : Result Nat := do
  let k ← j.getNat?
  ensure (k ≤ n) s!"integer {k} exceeds {n}"
  return k

def word (j : Json) : Result Word := return BitVec.ofNat 32 (← bounded j 4294967295)

def op? : String → Result Op
  | "add" => pure .add | "sub" => pure .sub | "mul" => pure .mul
  | "and" => pure .band | "or" => pure .bor | "xor" => pure .bxor
  | s => throw s!"unsupported binary operation: {s}"

def expr : Nat → List String → Json → Result Expr
  | 0, _, _ => throw "term exceeds replay depth limit"
  | fuel+1, vars, j => do
    match j with
    | .num _ => return .lit (← word j)
    | .str s => ensure (vars.contains s) s!"unbound variable: {s}"; return .var s
    | .arr a =>
      match a.toList with
      | [.str "not", e] => return .compl (← expr fuel vars e)
      | [.str "floor2", e] => return .floor2 (← expr fuel vars e)
      | [.str "shl", e, j] => return .shl (← expr fuel vars e) (← bounded j 31)
      | [.str "lshr", e, j] => return .lshr (← expr fuel vars e) (← bounded j 31)
      | [.str "affine_iter", e, n, a, b] =>
        return .affine (← expr fuel vars e) (← bounded n 65535) (← word a) (← word b)
      | [.str op, l, r] => return .bin (← op? op) (← expr fuel vars l) (← expr fuel vars r)
      | _ => throw "unknown term or bad arity"
    | _ => throw "expected a term, identifier or uint32"

def digit (c : Char) : Result Nat := do
  let n := c.toNat
  if 48 ≤ n && n ≤ 57 then return n-48
  if 65 ≤ n && n ≤ 70 then return n-55
  if 97 ≤ n && n ≤ 102 then return n-87
  throw "invalid hex digit"

def readBytes (j : Json) : Result (List Byte) := do
  let s ← j.getStr?
  ensure (s.utf8ByteSize ≤ 4*1024*1024) "hex string exceeds 4 MiB"
  let rec loop : List Char → List Byte → Result (List Byte)
    | [], acc => return acc.reverse
    | [_], _ => throw "odd number of hex digits"
    | a::b::cs, acc => do
      let byte := BitVec.ofNat 8 ((← digit a)*16 + (← digit b))
      loop cs (byte::acc)
  loop s.toList []

def identifier (s : String) : Bool :=
  match s.toList with
  | [] => false
  | first::tail => first.isAlpha && tail.all (fun c => c.isAlphanum || c == '_') &&
                   s.length ≤ 48 && !(s.contains "__")

def stringList (j : Json) : Result (List String) := do
  (← j.getArr?).toList.mapM Json.getStr?

def verify (s c : Json) : Result String := do
  fields s ["version", "kernels"] ["blobs"]
  fields c ["format", "mode", "source_sha256", "kernels", "pool", "files"] []
  ensure ((← (← s.getObjVal? "version").getNat?) == 1) "expected source version 1"
  ensure ((← (← c.getObjVal? "format").getStr?) == "rp2cc.cert.v1") "bad certificate format"
  let mode ← (← c.getObjVal? "mode").getStr?
  ensure (mode == "baseline" || mode == "optimized") "invalid certificate mode"
  let ks := (← (← s.getObjVal? "kernels").getArr?).toList
  let ds := (← (← c.getObjVal? "kernels").getArr?).toList
  ensure (ks.length == ds.length && ks.length > 0 && ks.length ≤ 128) "kernel count mismatch"
  let mut kernelNames : List String := []
  for (sk, dk) in ks.zip ds do
    fields sk ["name", "args", "body"] ["known"]
    fields dk ["name", "args", "body", "steps"] []
    let name ← (← sk.getObjVal? "name").getStr?
    ensure (identifier name && !(name.startsWith "rom_") && !(kernelNames.contains name))
      s!"bad kernel name: {name}"
    kernelNames := name :: kernelNames
    ensure (name == (← (← dk.getObjVal? "name").getStr?)) s!"kernel name mismatch: {name}"
    let args ← stringList (← sk.getObjVal? "args")
    let args' ← stringList (← dk.getObjVal? "args")
    ensure (args == args' && args.length ≤ 16 && args.Nodup && args.all identifier)
      s!"kernel {name}: invalid args or signature mismatch"
    let bindings ← (← (← fallback sk "known" (Json.mkObj [])).getObj?).toList.mapM fun (sym,j) => do
      ensure (identifier sym && !(args.contains sym)) s!"invalid fixed parameter: {sym}"
      return (sym, ← word j)
    ensure (bindings.length ≤ 128) "known parameter count too large"
    let src ← expr 81 (args ++ bindings.map Prod.fst) (← sk.getObjVal? "body")
    let dst ← expr 401 args (← dk.getObjVal? "body")
    ensure (accepts (fun key => bindings.lookup key) src dst) s!"kernel {name}: equality not certified"
  let bs := (← (← fallback s "blobs" (.arr #[])).getArr?).toList
  let p ← c.getObjVal? "pool"
  fields p ["bytes", "offsets"] []
  let data ← readBytes (← p.getObjVal? "bytes")
  let offs ← (← p.getObjVal? "offsets").getObj?
  ensure (bs.length == offs.toList.length && bs.length ≤ 256) "blob count mismatch"
  let mut names : List String := []
  let mut sawRamp := false
  let mut sawSweep := false
  let mut sawSync := false
  for b in bs do
    fields b ["name", "align", "hex"] []
    let name ← (← b.getObjVal? "name").getStr?
    ensure (identifier name && !(names.contains name)) s!"bad blob name: {name}"
    names := name :: names
    let some offJ := offs.get? name | throw s!"missing offset: {name}"
    let obj : Object := ⟨← readBytes (← b.getObjVal? "hex"), ← bounded (← b.getObjVal? "align") 256⟩
    ensure (sliceOK data obj (← offJ.getNat?)) s!"blob {name}: invalid slice or alignment"
    sawRamp := sawRamp || decide (obj = ramp)
    sawSweep := sawSweep || decide (obj = sweep)
    sawSync := sawSync || decide (obj = syncBytes)
  -- Compare full bytes *and* alignments, never just names or hashes, before
  -- applying the specialized lower bound from PoolExample.lean to actual inputs.
  let optimum := if sawRamp && sawSweep && sawSync && data.length == 84 then
                    "; attains the proved 84-byte lower bound" else ""
  return s!"PASS {mode}: {ks.length} universal kernel equalities; {bs.length} checked slices; {data.length} pool bytes{optimum}"

end RP2.Replay

def main (args : List String) : IO UInt32 := do
  let [sourcePath, certPath] := args | IO.eprintln "usage: Replay.lean SOURCE.json CERTIFICATE.json"; return 2
  let source ← IO.FS.readFile sourcePath
  let cert ← IO.FS.readFile certPath
  if source.utf8ByteSize > 4*1024*1024 || cert.utf8ByteSize > 12*1024*1024 then
    IO.eprintln "file exceeds replay limit"; return 2
  let result := do RP2.Replay.verify (← Lean.Json.parse (source.dropPrefix "\uFEFF").toString)
                                     (← Lean.Json.parse (cert.dropPrefix "\uFEFF").toString)
  match result with
  | .ok msg => IO.println msg; return 0
  | .error msg => IO.eprintln s!"REJECT: {msg}"; return 1
