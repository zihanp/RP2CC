import Proofs

/-! RV32 instruction *value* semantics for the one exact kernel emitted by the
Hazard3 lowering. SEXT.H requires Zbb and PACK requires Zbkb. The two SRAI
immediates fit five bits, so the model cannot express a reserved immediate.
Register aliasing follows the measured operands: a temporary distinct from the
incoming/result word must hold the low lane until PACK. Ret and encoding are
not part of these four value operations. -/
namespace RP2

def rvSextH (rs : Word) : Word := (low rs).signExtend 32
def rvSrai (rs : Word) (shamt : Fin 32) : Word := rs.sshiftRight shamt.val
/-- In RV32, bits [15:0] come from rs1 and bits [31:16] come from rs2. -/
def rvPack (rs1 rs2 : Word) : Word := low rs2 ++ low rs1

def hazard3Floor (word : Word) : Word :=
  let tmp := rvSextH word
  let tmp := rvSrai tmp 4
  let word := rvSrai word 20
  rvPack tmp word

private theorem rvLow (x : Word) : low (rvSrai (rvSextH x) 4) = (low x).sshiftRight 4 := by
  apply BitVec.eq_of_getLsbD_eq
  intro i hi
  change (((x.extractLsb' 0 16).signExtend 32).sshiftRight 4 |>.extractLsb' 0 16).getLsbD i =
    ((x.extractLsb' 0 16).sshiftRight 4).getLsbD i
  simp only [BitVec.getLsbD_extractLsb', BitVec.getLsbD_sshiftRight,
    BitVec.getLsbD_signExtend, BitVec.msb_eq_getLsbD_last, Nat.zero_add]
  have h32 : ¬32 ≤ i := by omega
  have h16 : ¬16 ≤ i := by omega
  have h4 : 4+i < 32 := by omega
  by_cases h : 4+i < 16 <;>
    simp [hi, h32, h16, h4, h]

private theorem rvHigh (x : Word) : low (rvSrai x 20) = (high x).sshiftRight 4 := by
  apply BitVec.eq_of_getLsbD_eq
  intro i hi
  change ((x.sshiftRight 20).extractLsb' 0 16).getLsbD i =
    ((x.extractLsb' 16 16).sshiftRight 4).getLsbD i
  simp only [BitVec.getLsbD_extractLsb', BitVec.getLsbD_sshiftRight,
    BitVec.msb_eq_getLsbD_last, Nat.zero_add]
  have h32 : ¬32 ≤ i := by omega
  have h16 : ¬16 ≤ i := by omega
  by_cases h : 4+i < 16
  · have g : 20+i < 32 := by omega
    simp [g, h, hi, h32, h16, ← Nat.add_assoc]
  · have g : ¬20+i < 32 := by omega
    simp [g, h, hi, h32, h16]

/-- The complete four value-producing instructions agree with signed per-lane
floor division by 16 for every possible RV32 input register value. -/
theorem hazard3_correct (x : Word) : hazard3Floor x = floorSpec x := by
  change low (rvSrai x 20) ++ low (rvSrai (rvSextH x) 4) = (high x).sshiftRight 4 ++ (low x).sshiftRight 4
  rw [rvHigh, rvLow]

-- Explicit guardrails against swapping PACK sources or using logical shifts.
example : rvPack 0xabcd1234 0x56789abc = 0x9abc1234 := by decide
example : hazard3Floor 0x7fff8000 = 0x07fff800 := by decide
example : hazard3Floor 0xffef0010 = 0xfffe0001 := by decide

end RP2
