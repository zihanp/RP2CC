import Proofs

/-! A small total *translation validator*: independently normalize both submitted terms
and compare them. Failure need not imply inequality. Success has a semantic theorem.
The two sides may contain any of the pure DSL operators, not just the benchmark. -/
namespace RP2

inductive Op where | add | sub | mul | band | bor | bxor deriving DecidableEq, Repr

def Op.run (op : Op) (x y : Word) : Word := match op with
  | .add => x+y | .sub => x-y | .mul => x*y
  | .band => x &&& y | .bor => x ||| y | .bxor => x ^^^ y

inductive Expr where
  | lit (n : Word) | var (name : String) | bin (op : Op) (l r : Expr)
  | compl (e : Expr) | shl (e : Expr) (k : Nat) | lshr (e : Expr) (k : Nat)
  | floor2 (e : Expr) | affine (e : Expr) (n : Nat) (a b : Word)
  deriving DecidableEq, Repr

def Expr.eval (ρ : Env) : Expr → Word
  | .lit n => n | .var s => ρ s | .bin op l r => op.run (l.eval ρ) (r.eval ρ)
  | .compl e => ~~~ e.eval ρ | .shl e k => e.eval ρ <<< k | .lshr e k => e.eval ρ >>> k
  | .floor2 e => floorSpec (e.eval ρ) | .affine e n a b => affineIter a b n (e.eval ρ)

def substKnown (known : String → Option Word) : Expr → Expr
  | .lit n => .lit n | .var s => (known s).elim (.var s) .lit
  | .bin op l r => .bin op (substKnown known l) (substKnown known r)
  | .compl e => .compl (substKnown known e) | .shl e k => .shl (substKnown known e) k
  | .lshr e k => .lshr (substKnown known e) k | .floor2 e => .floor2 (substKnown known e)
  | .affine e n a b => .affine (substKnown known e) n a b

theorem subst_sound (known : String → Option Word) (ρ : Env) (e : Expr) :
    (substKnown known e).eval ρ = e.eval (fun s => (known s).getD (ρ s)) := by
  induction e with
  | var s => cases h : known s <;> simp [substKnown, Expr.eval, h]
  | _ => simp_all [substKnown, Expr.eval]

def cancel (op : Op) (l r : Expr) : Expr := match op, l with
  | .add, .bin .sub a b => if b=r then a else .bin .add (.bin .sub a b) r
  | .sub, .bin .add a b => if b=r then a else .bin .sub (.bin .add a b) r
  | op, l => .bin op l r

theorem cancel_sound (op : Op) (l r : Expr) (ρ : Env) :
    (cancel op l r).eval ρ = op.run (l.eval ρ) (r.eval ρ) := by
  unfold cancel
  split <;> (first | split | skip) <;> simp_all [Expr.eval, Op.run] <;> grind

private def ones : Word := BitVec.allOnes 32

def neutral (op : Op) (l r : Expr) : Expr := match op with
  | .add => if l=.lit 0 then r else if r=.lit 0 then l else cancel .add l r
  | .sub => if l=r then .lit 0 else if r=.lit 0 then l else cancel .sub l r
  | .mul => if l=.lit 0 ∨ r=.lit 0 then .lit 0 else if l=.lit 1 then r
            else if r=.lit 1 then l else .bin .mul l r
  | .band => if l=.lit 0 ∨ r=.lit 0 then .lit 0 else if l=.lit ones then r
             else if r=.lit ones ∨ l=r then l else .bin .band l r
  | .bor => if l=.lit ones ∨ r=.lit ones then .lit ones else if l=.lit 0 then r
            else if r=.lit 0 ∨ l=r then l else .bin .bor l r
  | .bxor => if l=r then .lit 0 else if l=.lit 0 then r else if r=.lit 0 then l
             else .bin .bxor l r

theorem neutral_sound (op : Op) (l r : Expr) (ρ : Env) :
    (neutral op l r).eval ρ = op.run (l.eval ρ) (r.eval ρ) := by
  cases op <;> simp only [neutral] <;> repeat' split
  all_goals first | exact cancel_sound _ _ _ _ | skip
  all_goals try (rename_i h; rcases h with h|h)
  all_goals simp_all [Expr.eval, Op.run, ones]
  all_goals first | exact BitVec.and_allOnes.symm | exact BitVec.allOnes_and.symm
                  | exact BitVec.or_allOnes.symm | exact BitVec.allOnes_or.symm

def smartBin (op : Op) (l r : Expr) : Expr := match l,r with
  | .lit a, .lit b => .lit (op.run a b)
  | _,_ => neutral op l r

@[simp] theorem smartBin_sound (op : Op) (l r : Expr) (ρ : Env) :
    (smartBin op l r).eval ρ = op.run (l.eval ρ) (r.eval ρ) := by
  unfold smartBin; split <;> first | rfl | exact neutral_sound _ _ _ _

def smartNot (e : Expr) : Expr := match e with
  | .lit a => .lit (~~~ a) | .compl b => b | e => .compl e

@[simp] theorem smartNot_sound (e : Expr) (ρ : Env) :
    (smartNot e).eval ρ = ~~~ (e.eval ρ) := by
  unfold smartNot; split <;> simp [Expr.eval]

def smartShift (left : Bool) (x : Expr) (k : Nat) : Expr :=
  if k=0 then x else match x with
  | .lit a => .lit (if left then a <<< k else a >>> k)
  | x => if left then .shl x k else .lshr x k

@[simp] theorem smartShift_sound (left : Bool) (x : Expr) (k : Nat) (ρ : Env) :
    (smartShift left x k).eval ρ =
      (if left then x.eval ρ <<< k else x.eval ρ >>> k) := by
  unfold smartShift
  split
  · rename_i h; subst k; cases left <;> simp
  · split <;> cases left <;> rfl

def norm (e : Expr) : Expr := match e with
  | .lit n => .lit n | .var s => .var s | .bin op l r => smartBin op (norm l) (norm r)
  | .compl x => smartNot (norm x) | .shl x k => smartShift true (norm x) k
  | .lshr x k => smartShift false (norm x) k
  | .floor2 x => smartBin .bxor (smartBin .add
      (smartBin .band (smartShift false (norm x) 4) (.lit 0x0fff0fff))
      (.lit 0x78007800)) (.lit 0x78007800)
  | .affine x n a b =>
    let (A,B) := coefficients a b n
    smartBin .add (smartBin .mul (norm x) (.lit A)) (.lit B)

theorem norm_sound (e : Expr) (ρ : Env) : (norm e).eval ρ = e.eval ρ := by
  induction e with
  | floor2 x ih => simpa [norm, Expr.eval, Op.run, ih, floorFast] using floor_correct (x.eval ρ)
  | affine x n a b ih =>
    simpa [norm, Expr.eval, Op.run, ih] using (affine_correct a b (x.eval ρ) n).symm
  | _ => simp_all [norm, Expr.eval]

/-- Accept only identical normalized expressions. A proof about every environment
    follows even when every supplied optimization step, hash, or solver is untrusted. -/
def accepts (known : String → Option Word) (src dst : Expr) : Bool :=
  decide (norm (substKnown known src) = norm dst)

theorem accepts_sound {known : String → Option Word} {src dst : Expr}
    (h : accepts known src dst = true) (ρ : Env) :
    dst.eval ρ = src.eval (fun s => (known s).getD (ρ s)) := by
  have e := of_decide_eq_true h
  calc dst.eval ρ = (norm dst).eval ρ := (norm_sound dst ρ).symm
       _ = (norm (substKnown known src)).eval ρ := congrArg (Expr.eval ρ) e.symm
       _ = (substKnown known src).eval ρ := norm_sound _ _
       _ = _ := subst_sound _ _ _

end RP2
