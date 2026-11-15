"""CONSTRUCTED OfficeQA traces for Stage A software checks (design v0.1 section 7, "Stage A").

These are NOT recordings. No OfficeQA question, answer or document text is reproduced here: the OfficeQA CSV is
gated on Hugging Face and had not been materialized when these cases were written (2026-11-15). The questions are
written by hand in the style of the benchmark, and the page text imitates the layout of a Treasury bulletin table
(a heading that states a scale, then rows with a year label and figures).

Their only purpose is to establish what the P / S / C software does on inputs whose intended diagnosis is known in
advance. Any count taken from them is a statement about the code, never about the actor, the benchmark or the
research hypothesis. Replace them with real traces from the three-question baseline check before any empirical
claim is made.

Each case states: the failure family it belongs to, the intended diagnosis, and which condition is expected to make
it. The expectations are asserted in tests/test_officeqa_contracts.py.
"""
from __future__ import annotations

PAGE_1970 = """TABLE OPD-1. - OWNERSHIP OF PUBLIC DEBT SECURITIES
(In millions of dollars)

End of period        Total        Held by banks      Held by individuals
June 1970            372,607      58,114             81,243
June 1969            353,720      55,908             79,106
June 1968            344,663      62,851             76,422
"""

PAGE_1964 = """TABLE OPD-1. - OWNERSHIP OF PUBLIC DEBT SECURITIES
(In millions of dollars)

End of period        Total        Held by banks      Held by individuals
December 1964        317,940      61,208             67,015
December 1963        309,347      60,117             65,881
"""

PAGE_1988 = """TABLE FD-2. - FEDERAL DEBT OUTSTANDING
(In millions of dollars)

End of period        Gross Federal debt     Held by the public
September 1988       2,602,338              2,050,799
September 1987       2,350,276              1,890,784
"""


def case_operand_from_wrong_period() -> dict:
    """Family: source selection / dates. Intended diagnosis: the second operand is taken from the page that was
    already open instead of the page for the second asked period, so the difference answers a different question.
    Expected: P silent, S fires `unsupported_operands` (ADVICE)."""
    return {
        "id": "CONSTRUCTED-A1",
        "family": "source_selection_dates",
        "intended": "second operand taken from the 1970 page's neighbouring row instead of the 1964 page",
        "expect": {"P": set(), "S_adds": {"unsupported_operands"}},
        "question": "What was the change in total public debt securities held by individuals between June 1970 "
                    "and December 1964?",
        "candidate_files": ["treasury_bulletin_1970_06.txt", "treasury_bulletin_1964_12.txt"],
        "source_hints": ["https://fraser.stlouisfed.org/...june-1970?page=21",
                         "https://fraser.stlouisfed.org/...december-1964?page=18"],
        "oracle_pages": [
            {"file": "treasury_bulletin_1970_06.txt", "page": 21, "text": PAGE_1970},
            {"file": "treasury_bulletin_1964_12.txt", "page": 18, "text": PAGE_1964},
        ],
        "steps": [
            {"i": 1, "kind": "tool", "tool": "grep",
             "args": {"pattern": "held by individuals", "path": "treasury_bulletin_1970_06.txt"},
             "cmd": "grep(pattern='held by individuals', path='treasury_bulletin_1970_06.txt')",
             "obs": "412: End of period        Total        Held by banks      Held by individuals"},
            {"i": 2, "kind": "message",
             "text": "The June 1970 page gives 81,243 for individuals. The row below it gives 79,106, so the "
                     "change is 81,243 - 79,106 = 2,137."},
            {"i": 3, "kind": "message", "text": "<answer>2137</answer>"},
        ],
        "final_answer": "2137",
    }


def case_scale_not_converted() -> dict:
    """Family: units. Intended diagnosis: the figure is copied correctly from the right row of the right page, but
    the table states millions while the question asks for billions and no conversion is performed.
    Expected: P silent, S silent, C fires `scale_conversion_missing` (ADVICE)."""
    return {
        "id": "CONSTRUCTED-A2",
        "family": "units",
        "intended": "right value, right period, wrong scale; no conversion step",
        "expect": {"P": set(), "S_adds": set(), "C_adds": {"scale_conversion_missing"}},
        "question": "What was the gross Federal debt outstanding at the end of September 1988, in billions of "
                    "dollars?",
        "candidate_files": ["treasury_bulletin_1988_09.txt"],
        "source_hints": ["https://fraser.stlouisfed.org/...september-1988?page=13"],
        "oracle_pages": [{"file": "treasury_bulletin_1988_09.txt", "page": 13, "text": PAGE_1988}],
        "steps": [
            {"i": 1, "kind": "tool", "tool": "grep",
             "args": {"pattern": "gross federal debt", "path": "treasury_bulletin_1988_09.txt"},
             "cmd": "grep(pattern='gross federal debt', path='treasury_bulletin_1988_09.txt')",
             "obs": "88: End of period        Gross Federal debt     Held by the public"},
            {"i": 2, "kind": "message",
             "text": "The September 1988 row gives a gross Federal debt of 2,602,338."},
            {"i": 3, "kind": "message", "text": "<answer>2,602,338</answer>"},
        ],
        "final_answer": "2,602,338",
    }


def case_value_not_in_evidence() -> dict:
    """Family: unsupported operands. Intended diagnosis: the stated figure appears in no returned evidence.
    Expected: P already fires `ungrounded_answer_value`; S adds `operand_provenance_missing` for the same problem
    (more specific, not an additional problem)."""
    return {
        "id": "CONSTRUCTED-A3",
        "family": "unsupported_operands",
        "intended": "a figure that is in no returned evidence is answered as if it had been read",
        "expect": {"P": {"ungrounded_answer_value"}, "S_adds": {"operand_provenance_missing"}},
        "question": "What was the gross Federal debt outstanding at the end of September 1988?",
        "candidate_files": ["treasury_bulletin_1988_09.txt"],
        "source_hints": ["https://fraser.stlouisfed.org/...september-1988?page=13"],
        "oracle_pages": [{"file": "treasury_bulletin_1988_09.txt", "page": 13, "text": PAGE_1988}],
        "steps": [
            {"i": 1, "kind": "tool", "tool": "read",
             "args": {"path": "treasury_bulletin_1988_09.txt", "start": 80, "limit": 20},
             "cmd": "read(path='treasury_bulletin_1988_09.txt', start=80, limit=20)",
             "obs": "TABLE FD-2. - FEDERAL DEBT OUTSTANDING\n(In millions of dollars)"},
            {"i": 2, "kind": "message",
             "text": "The table shows a gross Federal debt of 2,714,900 at the end of September 1988."},
            {"i": 3, "kind": "message", "text": "<answer>2714900</answer>"},
        ],
        "final_answer": "2714900",
    }


def case_disallowed_path_and_grep_cap() -> dict:
    """Family: tool-use mechanics. Intended diagnosis: a read outside the candidate list, and an absence claim made
    after grep hit its 20-line cap. Expected: both findings belong to P (the plain report is sufficient)."""
    obs_capped = "\n".join(f"{100 + n}: interest-bearing debt line {n}" for n in range(20))
    return {
        "id": "CONSTRUCTED-A4",
        "family": "tool_mechanics",
        "intended": "disallowed path; capped grep used to support an absence claim",
        "expect": {"P": {"path_not_allowed", "grep_cap_reached"}, "S_adds": set()},
        "question": "What was the interest-bearing debt at the end of April 1977?",
        "candidate_files": ["treasury_bulletin_1977_04.txt"],
        "source_hints": ["https://fraser.stlouisfed.org/...april-1977?page=9"],
        "oracle_pages": [],
        "steps": [
            {"i": 1, "kind": "tool", "tool": "read",
             "args": {"path": "treasury_bulletin_1977_05.txt", "start": 1, "limit": 80},
             "cmd": "read(path='treasury_bulletin_1977_05.txt', start=1, limit=80)",
             "obs": "[read error: path not allowed]"},
            {"i": 2, "kind": "tool", "tool": "grep",
             "args": {"pattern": "interest-bearing debt", "path": "treasury_bulletin_1977_04.txt"},
             "cmd": "grep(pattern='interest-bearing debt', path='treasury_bulletin_1977_04.txt')",
             "obs": obs_capped},
            {"i": 3, "kind": "message",
             "text": "There is no table of interest-bearing debt in this bulletin."},
            {"i": 4, "kind": "message", "text": "<answer>not reported</answer>"},
        ],
        "final_answer": "not reported",
    }


ALL = [case_operand_from_wrong_period, case_scale_not_converted, case_value_not_in_evidence,
       case_disallowed_path_and_grep_cap]
