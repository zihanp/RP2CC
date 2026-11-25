import Std.Tactic.BVDecide
import Init.Data.BitVec.Bitblast
import Init.GrindInstances.Ring.BitVec

/-!
The semantic core of rp2cc, independent of the Python optimizer and the C compiler.
The word type is unsigned modulo 2^32; a signed lane is viewed using `toInt`.
There are no memory effects, register allocation, or time in this model.
-/
namespace RP2

abbrev Word := BitVec 32
abbrev Byte := BitVec 8
abbrev Env := String → Word

def low (x : Word) : BitVec 16 := x.extractLsb' 0 16
def high (x : Word) : BitVec 16 := x.extractLsb' 16 16

def floorSpec (x : Word) : Word := (high x).sshiftRight 4 ++ (low x).sshiftRight 4

def floorFast (x : Word) : Word :=
  ((((x >>> (4 : Nat)) &&& 0x0fff0fff) + 0x78007800) ^^^ 0x78007800)

private theorem floorSpec_bit (x : Word) (i : Nat) : (floorSpec x).getLsbD i =
    if i < 16 then ((low x).sshiftRight 4).getLsbD i
    else ((high x).sshiftRight 4).getLsbD (i-16) :=
  BitVec.getLsbD_append (x := (high x).sshiftRight 4) (y := (low x).sshiftRight 4)

/-- The optimizer's exact four-operation word formula implements both signed lanes. -/
theorem floor_correct (x : Word) : floorFast x = floorSpec x := by
  apply BitVec.eq_of_getLsbD_eq
  intro i hi
  rw [floorSpec_bit]
  simp only [floorFast, BitVec.getLsbD_xor, BitVec.getLsbD_add hi]
  simp only [high, low, BitVec.getLsbD_sshiftRight, BitVec.msb_eq_getLsbD_last,
    BitVec.getLsbD_extractLsb']
  have h : i=0 ∨ i=1 ∨ i=2 ∨ i=3 ∨ i=4 ∨ i=5 ∨ i=6 ∨ i=7 ∨
      i=8 ∨ i=9 ∨ i=10 ∨ i=11 ∨ i=12 ∨ i=13 ∨ i=14 ∨ i=15 ∨
      i=16 ∨ i=17 ∨ i=18 ∨ i=19 ∨ i=20 ∨ i=21 ∨ i=22 ∨ i=23 ∨
      i=24 ∨ i=25 ∨ i=26 ∨ i=27 ∨ i=28 ∨ i=29 ∨ i=30 ∨ i=31 := by omega
  rcases h with (rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|
     rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl) <;>
    simp +ground [BitVec.carry_succ]

/-- Integer division in Lean is Euclidean: for a positive divisor this is floor. -/
theorem lane_is_floor (s : BitVec 16) : (s.sshiftRight 4).toInt = s.toInt / 16 := by
  rw [BitVec.toInt_sshiftRight, Int.shiftRight_eq_div_pow]
  rfl

theorem decoded_floors (x : Word) :
    (low (floorFast x)).toInt = (low x).toInt / 16 ∧
    (high (floorFast x)).toInt = (high x).toInt / 16 := by
  have h₁ : low (floorFast x) = (low x).sshiftRight 4 := by
    simp only [floor_correct, floorSpec, low, high]; bv_normalize
  have h₂ : high (floorFast x) = (high x).sshiftRight 4 := by
    simp only [floor_correct, floorSpec, low, high]; bv_normalize
  rw [h₁, h₂, lane_is_floor, lane_is_floor]
  exact ⟨rfl, rfl⟩

def affineIter (a b : Word) : Nat → Word → Word
  | 0, x => x
  | n+1, x => affineIter a b n x * a + b

/-- Independent linear-time recurrence. Python proposes coefficients by squaring. -/
def coefficients (a b : Word) : Nat → Word × Word
  | 0 => (1, 0)
  | n+1 => let (A, B) := coefficients a b n; (A*a, B*a+b)

theorem affine_correct (a b x : Word) (n : Nat) :
    affineIter a b n x = x * (coefficients a b n).1 + (coefficients a b n).2 := by
  induction n with
  | zero => simp [affineIter, coefficients]
  | succ n ih =>
    simp only [affineIter, coefficients, ih, BitVec.add_mul, BitVec.mul_assoc,
      BitVec.add_assoc]

/-- Borrow a dirty word for two updates; cancelling restores it for any initial value.
    This is an algebraic experiment, not an enabled backend/register-allocation pass. -/
theorem dirty_restore (x y z k : Word) :
    let t := z + x
    let v := y + t*k
    let restored := t - x
    (v - restored*k, restored) = (y + x*k, z) := by
  dsimp
  simp only [BitVec.add_mul, BitVec.add_sub_cancel]
  congr 1
  grind

structure Object where
  data : List Byte
  alignment : Nat
  deriving DecidableEq

def sliceOK (pool : List Byte) (obj : Object) (off : Nat) : Bool := decide
  (0 < obj.alignment ∧ (obj.alignment &&& (obj.alignment - 1)) = 0 ∧
   off % obj.alignment = 0 ∧ off + obj.data.length ≤ pool.length ∧
   (pool.drop off).take obj.data.length = obj.data)

def poolRead (pool : List Byte) (off len i : Nat) : Byte :=
  if i < len then pool[off+i]?.getD 0 else 0

/-- Accepted offsets respect alignment and bounds; every byte read (including the
    documented zero outside an object's length) has the source value. -/
theorem slice_sound {pool : List Byte} {obj : Object} {off : Nat}
    (h : sliceOK pool obj off = true) :
    off % obj.alignment = 0 ∧ off + obj.data.length ≤ pool.length ∧
    ∀ i, poolRead pool off obj.data.length i = obj.data[i]?.getD 0 := by
  obtain ⟨_, _, halign, hbound, hbytes⟩ := of_decide_eq_true h
  refine ⟨halign, hbound, fun i => ?_⟩
  by_cases hi : i < obj.data.length
  · have hs := congrArg (fun xs : List Byte => xs[i]?) hbytes
    simp only [List.getElem?_take_of_lt hi, List.getElem?_drop] at hs
    simp [poolRead, hi, hs]
  · simp [poolRead, hi]

end RP2
