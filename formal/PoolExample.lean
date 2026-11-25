import Proofs
import Init.Data.List.Perm

/-! Exact minimum for the six byte objects in examples/bench.json only. Not a
claim about optimal ELF layout, arbitrary pool instances or filesystem sizes. -/
namespace RP2

private theorem slice_mem_prefix {pool : List Byte} {o : Object} {off endPos : Nat}
    (h : sliceOK pool o off = true) (he : off + o.data.length ≤ endPos) :
    o.data ⊆ pool.take endPos := by
  obtain ⟨_, _, _, _, hbytes⟩ := of_decide_eq_true h
  intro v hv
  obtain ⟨i, hi, eq⟩ := List.getElem_of_mem hv
  have hp := congrArg (fun xs : List Byte => xs[i]?) hbytes
  simp only [List.getElem?_take_of_lt hi, List.getElem?_drop] at hp
  apply List.mem_of_getElem? (i := off+i)
  rw [List.getElem?_take_of_lt (by omega), hp, List.getElem?_eq_getElem hi, eq]

def byteSeq (start count : Nat) : List Byte :=
  (List.range count).map fun n => BitVec.ofNat 8 (start + n)

def ramp : Object := ⟨byteSeq 0 64, 16⟩
def sweep : Object := ⟨byteSeq 48 32, 16⟩
def syncBytes : Object := ⟨[0xa5,0x00,0xff,0x5a], 4⟩

/-- Three of the six objects alone already require 84 bytes in every permitted pool.
    There are 83 distinct values; any final occupied end position is a multiple of 4. -/
theorem demo_lower84 (pool : List Byte) (r s t : Nat)
    (hr : sliceOK pool ramp r = true) (hs : sliceOK pool sweep s = true)
    (ht : sliceOK pool syncBytes t = true) : 84 ≤ pool.length := by
  let e := max (r+64) (max (s+32) (t+4))
  let witness : List Byte := byteSeq 0 80 ++ [0xa5, 0xff, 0x5a]
  have nr : r+64 ≤ e := by dsimp [e]; omega
  have ns : s+32 ≤ e := by dsimp [e]; omega
  have nt : t+4 ≤ e := by dsimp [e]; omega
  have gr := slice_sound hr
  have gs := slice_sound hs
  have gt := slice_sound ht
  obtain ⟨ar, br, _⟩ := gr
  obtain ⟨aₛ, bs, _⟩ := gs
  obtain ⟨at', bt, _⟩ := gt
  change r % 16 = 0 at ar; change s % 16 = 0 at aₛ; change t % 4 = 0 at at'
  change r + 64 ≤ pool.length at br; change s + 32 ≤ pool.length at bs
  change t + 4 ≤ pool.length at bt
  have wlen : witness.length = 83 := by decide
  have wnodup : witness.Nodup := by decide
  have wcover : witness.all (fun v => ramp.data.contains v || sweep.data.contains v ||
      syncBytes.data.contains v) = true := by decide
  have wsub : witness ⊆ pool.take e := by
    intro v hv
    have hor := List.all_eq_true.mp wcover v hv
    simp only [Bool.or_eq_true, List.contains_iff_mem] at hor
    rcases hor with (hr' | hs') | ht'
    · exact slice_mem_prefix hr nr hr'
    · exact slice_mem_prefix hs ns hs'
    · exact slice_mem_prefix ht nt ht'
  have lenBound := wnodup.length_le_of_subset wsub
  have short := List.length_take_le e pool
  rw [wlen] at lenBound
  dsimp [e] at lenBound short
  omega

/-- A concrete feasible construction, including all alignments and empty object. -/
theorem demo_attains84 :
    let p := byteSeq 0 80 ++ syncBytes.data
    p.length = 84 ∧ sliceOK p ramp 0 = true ∧
    sliceOK p ⟨byteSeq 16 32, 8⟩ 16 = true ∧ sliceOK p ⟨byteSeq 48 16,16⟩ 48 = true ∧
    sliceOK p sweep 48 = true ∧ sliceOK p syncBytes 80 = true ∧
    sliceOK p ⟨[],1⟩ 0 = true := by decide

end RP2
