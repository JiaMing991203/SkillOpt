"""The formal record of one analyst request: trajectories, steps and explicit statements (research; no network).

docs/research/TRACE_ONLY_REPORT_PROTOTYPE.md section 3. Input: the user message the analyst received (only its
"## Failed/Successful Trajectories" part is parsed; the skill and memory are context, not evidence). Output, per
trajectory: the header fields as printed (task sentence, task type, failure reason, declared step count, listed under
Failed or Successful), and per step its index, the agent's think line (if printed), the action line and the observation
line, plus the observation split into explicit statements ("atoms").

Atoms are literal readings of the observation SENTENCE FORMS that occur in these records (surveyed from the 15
requests themselves, not from engine code or game files), e.g. "You pick up the pen 2 from the desk 1." ->
pick_up(obj="pen 2", recep="desk 1"). An atom says only that the observation at that step contains that sentence.
It is not a world state: there is no persistence, no initial state, no hidden object and no rule here. A sentence
that fits no form is kept verbatim as a raw atom; queries that would depend on it are reported as not determinable.

Faithfulness check: render(atoms) must reproduce every observation string exactly (round trip), and a second,
independent implementation (tor_ref.py) answers the same queries directly from the text.
"""
from __future__ import annotations

import re

NAME = r"[a-z]+ \d+"
ITEM_LIST = r"(?:an? " + NAME + r")(?:, an? " + NAME + r")*(?:, and an? " + NAME + r")?"
TRAJ_SECTION = re.compile(r"\n## (Failed|Successful) Trajectories \((\d+) total\)\n")
TRAJ_HEAD = re.compile(r"^### Trajectory (\d+) \(id=(tr:\d+)\)$", re.M)
STEP_LINE = re.compile(r"^\[step (\d+) (think|action|obs)\] ?(.*)$")
OBS_PAD = "    "  # fmt_trajectory prints "[step i obs]    <text>" (four spaces)

# Sentence forms (full match on one sentence). Groups are names or item lists.
FORMS = [
    ("arrive", re.compile(r"You arrive at (" + NAME + r")\.")),
    ("see_on", re.compile(r"On the (" + NAME + r"), you see (nothing|" + ITEM_LIST + r")\.")),
    ("see_in", re.compile(r"In it, you see (nothing|" + ITEM_LIST + r")\.")),
    ("is_closed", re.compile(r"The (" + NAME + r") is closed\.")),
    ("is_open", re.compile(r"The (" + NAME + r") is open\.")),
    ("open_done", re.compile(r"You open the (" + NAME + r")\.")),
    ("close_done", re.compile(r"You close the (" + NAME + r")\.")),
    ("pick_up", re.compile(r"You pick up the (" + NAME + r") from the (" + NAME + r")\.")),
    ("move_done", re.compile(r"You move the (" + NAME + r") to the (" + NAME + r")\.")),
    ("turn_on", re.compile(r"You turn on the (" + NAME + r")\.")),
    ("heat_done", re.compile(r"You heat the (" + NAME + r") using the (" + NAME + r")\.")),
    ("cool_done", re.compile(r"You cool the (" + NAME + r") using the (" + NAME + r")\.")),
    ("clean_done", re.compile(r"You clean the (" + NAME + r") using the (" + NAME + r")\.")),
    ("nothing_happens", re.compile(r"Nothing happens\.")),
    ("carrying", re.compile(r"You are carrying: (" + ITEM_LIST + r")\.")),
    ("carrying_nothing", re.compile(r"You are not carrying anything\.")),
    ("facing", re.compile(r"You are facing the (" + NAME + r"(?:, " + NAME + r")*(?:, and " + NAME + r")?)\.")),
    ("next_to", re.compile(r"Next to it, you see (nothing|" + ITEM_LIST + r")\.")),
    ("middle_of_room", re.compile(r"You are in the middle of a room\.")),
    ("look_around", re.compile(r"Looking quickly around you, you see (nothing|" + ITEM_LIST + r")\.")),
    ("nothing_special", re.compile(r"There's nothing special about (" + NAME + r")\.")),
    ("normal", re.compile(r"This is a normal (" + NAME + r")\.")),
]
# Atom kinds and their argument names (the query vocabulary uses the same names).
ATOM_ARGS = {
    "arrive": ("recep",), "see_on": ("recep", "items"), "see_in": ("recep", "items"), "is_closed": ("recep",),
    "is_open": ("recep",), "open_done": ("recep",), "close_done": ("recep",), "pick_up": ("obj", "recep"),
    "move_done": ("obj", "recep"), "turn_on": ("obj",), "heat_done": ("obj", "appliance"),
    "cool_done": ("obj", "appliance"), "clean_done": ("obj", "appliance"), "nothing_happens": (),
    "carrying": ("items",), "facing": ("receps",), "next_to": ("items",), "middle_of_room": (),
    "look_around": ("items",), "nothing_special": ("obj",), "normal": ("obj",), "raw": ("text",),
}


def split_items(s: str) -> list:
    """"a pen 2, a book 1, and a cd 3" -> ["pen 2", "book 1", "cd 3"]; "nothing" -> []."""
    if s == "nothing":
        return []
    parts = re.split(r", and |, ", s)
    return [re.sub(r"^an? ", "", p) for p in parts]


def split_bare(s: str) -> list:
    return re.split(r", and |, ", s)


def render_items(items: list, last_and: bool = True) -> str:
    if not items:
        return "nothing"
    a = ["a " + x for x in items]
    if len(a) == 1:
        return a[0]
    return ", ".join(a[:-1]) + ", and " + a[-1]


def sentences(obs: str) -> list:
    """Split an observation into sentences with offsets. A sentence ends at '.' followed by whitespace or the end."""
    out, pos = [], 0
    for m in re.finditer(r"\.(?=\s|$)", obs):
        end = m.end()
        seg = obs[pos:end]
        lead = len(seg) - len(seg.lstrip())
        out.append((pos + lead, end, obs[pos + lead:end]))
        pos = end
    rest = obs[pos:]
    if rest.strip():
        lead = len(rest) - len(rest.lstrip())
        out.append((pos + lead, len(obs.rstrip()), rest.strip()))
    return out


def parse_obs(obs: str) -> list:
    """Observation text -> atoms (each with its exact sentence, offsets and the separator before it)."""
    atoms, last_recep = [], None
    for start, end, sent in sentences(obs):
        atom = None
        for kind, rx in FORMS:
            m = rx.fullmatch(sent)
            if not m:
                continue
            if kind == "see_in":
                if last_recep is None:
                    break  # "In it" with no receptacle named earlier in the same observation: keep raw
                atom = {"kind": kind, "recep": last_recep, "items": split_items(m.group(1))}
            elif kind == "see_on":
                atom = {"kind": kind, "recep": m.group(1), "items": split_items(m.group(2))}
            elif kind in ("carrying", "next_to", "look_around"):
                atom = {"kind": kind, "items": split_items(m.group(1))}
            elif kind == "carrying_nothing":
                atom = {"kind": "carrying", "items": [], "_form": "not_carrying"}
            elif kind == "facing":
                atom = {"kind": kind, "receps": split_bare(m.group(1))}
            else:
                args = ATOM_ARGS[kind]
                atom = {"kind": kind}
                for i, an in enumerate(args):
                    atom[an] = m.group(i + 1)
            break
        if atom is None:
            atom = {"kind": "raw", "text": sent}
        atom["quote"], atom["start"], atom["end"] = sent, start, end
        atoms.append(atom)
        if atom["kind"] in ("is_open", "open_done", "is_closed", "close_done"):
            last_recep = atom["recep"]
        elif atom["kind"] == "normal":  # "This is a normal box 1. In it, you see nothing." ("it" = the box)
            last_recep = atom["obj"]
        elif atom["kind"] == "arrive":
            last_recep = None  # "In it" follows the open/closed sentence of the receptacle, never the arrival alone
    return atoms


def render_atom(a: dict) -> str:
    k = a["kind"]
    if k == "raw":
        return a["text"]
    if k == "arrive":
        return f"You arrive at {a['recep']}."
    if k == "see_on":
        return f"On the {a['recep']}, you see {render_items(a['items'])}."
    if k == "see_in":
        return f"In it, you see {render_items(a['items'])}."
    if k == "is_closed":
        return f"The {a['recep']} is closed."
    if k == "is_open":
        return f"The {a['recep']} is open."
    if k == "open_done":
        return f"You open the {a['recep']}."
    if k == "close_done":
        return f"You close the {a['recep']}."
    if k == "pick_up":
        return f"You pick up the {a['obj']} from the {a['recep']}."
    if k == "move_done":
        return f"You move the {a['obj']} to the {a['recep']}."
    if k == "turn_on":
        return f"You turn on the {a['obj']}."
    if k in ("heat_done", "cool_done", "clean_done"):
        return f"You {k.split('_')[0]} the {a['obj']} using the {a['appliance']}."
    if k == "nothing_happens":
        return "Nothing happens."
    if k == "carrying":
        if a.get("_form") == "not_carrying" or not a["items"]:
            return "You are not carrying anything."
        return f"You are carrying: {render_items(a['items'])}."
    if k == "facing":
        r = a["receps"]
        return "You are facing the " + (r[0] if len(r) == 1 else ", ".join(r[:-1]) + ", and " + r[-1]) + "."
    if k == "next_to":
        return f"Next to it, you see {render_items(a['items'])}."
    if k == "middle_of_room":
        return "You are in the middle of a room."
    if k == "look_around":
        return f"Looking quickly around you, you see {render_items(a['items'])}."
    if k == "nothing_special":
        return f"There's nothing special about {a['obj']}."
    if k == "normal":
        return f"This is a normal {a['obj']}."
    raise ValueError(k)


def round_trip(obs: str, atoms: list) -> bool:
    """Every atom re-renders to its exact sentence, and the sentences with their original separators give obs."""
    pos, out = 0, []
    for a in atoms:
        if render_atom(a) != a["quote"]:
            return False
        out.append(obs[pos:a["start"]])
        out.append(a["quote"])
        pos = a["end"]
    out.append(obs[pos:])
    return "".join(out) == obs and all(sep.strip() == "" for sep in _separators(obs, atoms))


def _separators(obs: str, atoms: list) -> list:
    seps, pos = [], 0
    for a in atoms:
        seps.append(obs[pos:a["start"]])
        pos = a["end"]
    seps.append(obs[pos:])
    return seps


def parse_trajectories(user: str) -> dict:
    """The trajectories part of an analyst request -> record dict (pure text parsing)."""
    m = TRAJ_SECTION.search(user)
    if not m:
        raise ValueError("no trajectories section")
    outcome = "failed" if m.group(1) == "Failed" else "succeeded"
    text = user[m.end():]
    heads = list(TRAJ_HEAD.finditer(text))
    trajs, issues = [], []
    for k, h in enumerate(heads):
        block = text[h.end():heads[k + 1].start() if k + 1 < len(heads) else len(text)]
        block = block.rstrip("\n")
        if k + 1 < len(heads):  # fmt_minibatch_trajectories joins trajectories with "\n\n---\n\n"
            if not block.endswith("\n\n---"):
                raise ValueError(f"trajectory {h.group(2)}: missing separator")
            block = block[:-len("\n\n---")]
        t = {"pos": int(h.group(1)), "tid": h.group(2), "outcome": outcome, "task": None, "task_type": None,
             "fail_reason": None, "declared_steps": None, "steps": [], "header_lines": [], "issues": []}
        cur = None
        for ln in block.split("\n"):
            if not ln.strip() and cur is None:
                continue
            sm = STEP_LINE.match(ln)
            if sm:
                i, field, val = int(sm.group(1)), sm.group(2), sm.group(3)
                if field == "obs":
                    val = sm.group(0)[len(f"[step {i} obs]"):]
                    val = val[len(OBS_PAD):] if val.startswith(OBS_PAD) else val.lstrip(" ")
                if not t["steps"] or t["steps"][-1]["i"] != i:
                    t["steps"].append({"i": i, "think": None, "action": None, "obs": None})
                st = t["steps"][-1]
                if st[field] is not None:
                    t["issues"].append(f"step {i}: duplicate {field} line")
                st[field] = val
                cur = (st, field)
                continue
            if ln.startswith("Task: ") and not t["steps"]:
                t["task"] = ln[len("Task: "):]
            elif ln.startswith("Task type: ") and not t["steps"]:
                t["task_type"] = ln[len("Task type: "):]
            elif ln.startswith("Failure reason: ") and not t["steps"]:
                t["fail_reason"] = ln[len("Failure reason: "):]
            elif ln.startswith("Steps: ") and not t["steps"]:
                v = ln[len("Steps: "):]
                t["declared_steps"] = int(v) if v.isdigit() else None
            elif cur is not None:  # continuation of a multi-line field
                st, field = cur
                st[field] = (st[field] or "") + "\n" + ln
                t["issues"].append(f"step {st['i']}: multi-line {field}")
            elif ln.strip():
                t["header_lines"].append(ln)
                t["issues"].append(f"unexpected header line: {ln[:60]}")
        idx = [s["i"] for s in t["steps"]]
        t["contiguous"] = idx == list(range(len(idx)))
        t["count_matches_declared"] = t["declared_steps"] == len(idx)
        t["complete_internal"] = t["contiguous"] and t["count_matches_declared"] and \
            all(s["action"] is not None and s["obs"] is not None for s in t["steps"])
        for s in t["steps"]:
            s["atoms"] = parse_obs(s["obs"] or "")
            s["round_trip"] = round_trip(s["obs"] or "", s["atoms"])
            s["n_raw"] = sum(1 for a in s["atoms"] if a["kind"] == "raw")
        trajs.append(t)
    if len(trajs) != int(m.group(2)):
        issues.append(f"{len(trajs)} trajectories parsed, header says {m.group(2)}")
    return {"outcome_section": outcome, "declared_total": int(m.group(2)), "trajectories": trajs, "issues": issues}


def coverage(rec: dict) -> dict:
    n_obs = n_rt = n_sent = n_raw = n_steps = 0
    raw_examples = []
    for t in rec["trajectories"]:
        for s in t["steps"]:
            n_steps += 1
            n_obs += 1
            n_rt += 1 if s["round_trip"] else 0
            n_sent += len(s["atoms"])
            for a in s["atoms"]:
                if a["kind"] == "raw":
                    n_raw += 1
                    if len(raw_examples) < 10:
                        raw_examples.append({"tid": t["tid"], "step": s["i"], "text": a["text"]})
    return {"trajectories": len(rec["trajectories"]), "steps": n_steps, "observations_round_trip": n_rt,
            "observations": n_obs, "sentences": n_sent, "raw_sentences": n_raw, "raw_examples": raw_examples,
            "complete_internal": sum(1 for t in rec["trajectories"] if t["complete_internal"]),
            "issues": rec["issues"] + [f"{t['tid']}: {i}" for t in rec["trajectories"] for i in t["issues"]]}


def find_traj(rec: dict, tid: str):
    for t in rec["trajectories"]:
        if t["tid"] == tid:
            return t
    return None
