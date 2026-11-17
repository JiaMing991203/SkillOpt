/-
  LearnedContracts (research; Lean 4 core only, no Mathlib). Design v0.1 section 5.

  What Lean checks here, independently of the Python implementation:
    * contract EVIDENCE: given, for every applicable attempt, the Kleene values of the contract's ground atoms
      (exported from the Python state estimate: a TRUSTED input) and the attempt's outcome, the classification of
      the attempt (counterexample / explained failure / consistent success / neutral / undetermined) and its counts
      are recomputed from the contract's clauses and polarities with the direction of the contract:
        requires  outcome ok and condition false        -> counterexample
        suffices  condition true and outcome fail       -> counterexample
        effect    outcome ok and an effect literal false -> counterexample
    * COMPOSITION: a propositional obligation (hypothesis -> goal, unrolled from the abstract procedure by the
      Python side: a TRUSTED encoding) is valid over all assignments, or a given assignment refutes it.
  What it does not establish: that the state estimate or the procedure encoding is correct, that the environment
  obeys any contract, or that a violation caused a task failure. No axiom, sorry, native_decide or extern is used.
-/
namespace LC

inductive Tri where
  | t | f | u
deriving DecidableEq, Repr

def tnot : Tri → Tri
  | .t => .f
  | .f => .t
  | .u => .u

def tand : Tri → Tri → Tri
  | .f, _ => .f
  | _, .f => .f
  | .t, .t => .t
  | _, _ => .u

def tor : Tri → Tri → Tri
  | .t, _ => .t
  | _, .t => .t
  | .f, .f => .f
  | _, _ => .u

def nthT : List Tri → Nat → Tri
  | [], _ => .u
  | x :: _, 0 => x
  | _ :: xs, n + 1 => nthT xs n

/-- A literal = (slot of its ground atom in the row, polarity). -/
def litVal (row : List Tri) (l : Nat × Bool) : Tri :=
  if l.2 then nthT row l.1 else tnot (nthT row l.1)

def clauseVal (row : List Tri) : List (Nat × Bool) → Tri
  | [] => .f
  | l :: ls => tor (litVal row l) (clauseVal row ls)

def cnfVal (row : List Tri) : List (List (Nat × Bool)) → Tri
  | [] => .t
  | c :: cs => tand (clauseVal row c) (cnfVal row cs)

inductive Out where
  | ok | fail | unk
deriving DecidableEq, Repr

inductive Kind where
  | req | suf | eff
deriving DecidableEq, Repr

inductive Cls where
  | counter | supFail | supOk | neutral | undet
deriving DecidableEq, Repr

structure Row where
  vals : List Tri
  out : Out
deriving DecidableEq, Repr

def classify (k : Kind) (c : List (List (Nat × Bool))) (r : Row) : Cls :=
  match k, r.out, cnfVal r.vals c with
  | .req, .unk, _ => .undet
  | .req, _, .u => .undet
  | .req, .ok, .f => .counter
  | .req, .fail, .f => .supFail
  | .req, .ok, .t => .supOk
  | .req, .fail, .t => .neutral
  | .suf, .unk, _ => .undet
  | .suf, _, .u => .undet
  | .suf, .fail, .t => .counter
  | .suf, .ok, .t => .supOk
  | .suf, _, .f => .neutral
  | .eff, .ok, .f => .counter
  | .eff, .ok, .t => .supOk
  | .eff, .ok, .u => .undet
  | .eff, _, _ => .neutral

structure Counts where
  counter : Nat
  supFail : Nat
  supOk : Nat
  neutral : Nat
  undet : Nat
deriving DecidableEq, Repr

def Counts.bump (n : Counts) : Cls → Counts
  | .counter => { n with counter := n.counter + 1 }
  | .supFail => { n with supFail := n.supFail + 1 }
  | .supOk => { n with supOk := n.supOk + 1 }
  | .neutral => { n with neutral := n.neutral + 1 }
  | .undet => { n with undet := n.undet + 1 }

def nthRow : List Row → Nat → Row
  | [], _ => ⟨[], .unk⟩
  | x :: _, 0 => x
  | _ :: xs, n + 1 => nthRow xs n

def count (k : Kind) (c : List (List (Nat × Bool))) : List Row → Counts
  | [] => ⟨0, 0, 0, 0, 0⟩
  | r :: rs => (count k c rs).bump (classify k c r)

/-- Propositional formulas over Boolean variables (composition obligations). -/
inductive Fm where
  | v (i : Nat)
  | tt
  | ff
  | neg (a : Fm)
  | conj (a b : Fm)
  | disj (a b : Fm)
deriving Repr

def nthB : List Bool → Nat → Bool
  | [], _ => false
  | x :: _, 0 => x
  | _ :: xs, n + 1 => nthB xs n

def Fm.eval (s : List Bool) : Fm → Bool
  | .v i => nthB s i
  | .tt => true
  | .ff => false
  | .neg a => !(Fm.eval s a)
  | .conj a b => Fm.eval s a && Fm.eval s b
  | .disj a b => Fm.eval s a || Fm.eval s b

def expand : List (List Bool) → List (List Bool)
  | [] => []
  | s :: r => (false :: s) :: (true :: s) :: expand r

def allAssign : Nat → List (List Bool)
  | 0 => [[]]
  | n + 1 => expand (allAssign n)

def allImp (hyp goal : Fm) : List (List Bool) → Bool
  | [] => true
  | s :: r => (!(Fm.eval s hyp) || Fm.eval s goal) && allImp hyp goal r

/-- hyp → goal holds for every assignment of the first n variables. -/
def valid (n : Nat) (hyp goal : Fm) : Bool := allImp hyp goal (allAssign n)

/-- The assignment s satisfies hyp and falsifies goal (a countermodel). -/
def refutes (s : List Bool) (hyp goal : Fm) : Bool := Fm.eval s hyp && !(Fm.eval s goal)

-- Library smoke checks (constructed values).
theorem smoke_req_counter : classify .req [[(0, true)]] ⟨[.f], .ok⟩ = .counter := by decide
theorem smoke_req_unknown : classify .req [[(0, true), (1, false)]] ⟨[.f, .u], .ok⟩ = .undet := by decide
theorem smoke_suf : classify .suf [[(0, false)]] ⟨[.f], .fail⟩ = .counter := by decide
theorem smoke_count : count .req [[(0, true)]] [⟨[.f], .ok⟩, ⟨[.f], .fail⟩, ⟨[.t], .ok⟩, ⟨[.u], .ok⟩] =
    ⟨1, 1, 1, 0, 1⟩ := by decide +kernel
theorem smoke_valid : valid 2 (.conj (.v 0) (.disj (.neg (.v 0)) (.v 1))) (.v 1) = true := by decide +kernel
theorem smoke_refutes : refutes [true, false] (.v 0) (.v 1) = true := by decide

end LC
