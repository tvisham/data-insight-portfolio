"""Parse UIUC prerequisite strings into a small AND/OR tree.

UIUC descriptions look roughly like:

    "Prerequisite: CS 173 and one of CS 125 or CS 128."
    "Prerequisite: CS 225. Credit is not given for both..."
    "Prerequisite: MATH 231 or MATH 220; consent of instructor."

We only care about the substring that follows "Prerequisite:" up to the
next full stop or semicolon. Everything else in the description is
noise for graph-building purposes.

Design notes:
- We intentionally keep this rule-based. A regex + a tiny recursive
  descent parser handles the ~90% case, and the tests below spell out
  what "handled" means. Cases we don't handle (e.g. "senior standing",
  "consent of instructor") are dropped and recorded — see how
  `parse_prereq_string` returns `unparsed_notes`.
- The output has two shapes: a nested tree (for display / debugging)
  and a flat edge list (for SQL). Loaders use the edge list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# "CS 225", "MATH 231", "STAT 400", "ECE 220"
_COURSE_RE = re.compile(r"\b([A-Z]{2,6})\s?([0-9]{3}[A-Z]?)\b")

# Grab the clause that starts with "Prerequisite" or "Prerequisites" up
# to the first period. UIUC uses ';' to separate AND-groups *inside* the
# prereq clause (e.g. "CS 125 or CS 128; CS 173."), so we don't want to
# stop at ';'.
_PREREQ_CLAUSE_RE = re.compile(
    r"[Pp]rerequisites?:\s*([^.]+)"
)

Op = Literal["AND", "OR"]


@dataclass
class PrereqNode:
    """AND/OR tree. Leaf = a course code string like 'CS 225'."""

    op: Op | None = None  # None on leaves
    course: str | None = None  # set on leaves
    children: list["PrereqNode"] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return self.course is not None

    def to_dict(self) -> dict:
        if self.is_leaf():
            return {"course": self.course}
        return {"op": self.op, "children": [c.to_dict() for c in self.children]}


@dataclass
class PrereqParse:
    tree: PrereqNode | None
    edges: list[tuple[str, str, int]]  # (target, prereq_course, group_id)
    raw_clause: str | None
    unparsed_notes: list[str]


def _extract_courses(text: str) -> list[str]:
    """Return a de-duped, order-preserving list of course codes in `text`."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _COURSE_RE.finditer(text):
        code = f"{m.group(1).upper()} {m.group(2)}"
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _build_tree(clause: str) -> PrereqNode | None:
    """Very small heuristic parser.

    Rules:
      * ';' inside a prereq clause acts as an AND separator, same as
        the word "and".
      * If the clause contains both an AND-marker and an OR-marker we
        split on those AND-markers first (higher precedence); each
        conjunct that itself contains "or" becomes an OR node.
      * If it contains only AND-markers, produce an AND of leaves.
      * If it contains only "or" (or "one of"), produce an OR of leaves.
      * A single course produces a leaf.

    Deliberately simpler than a full grammar; edge cases become
    unparsed_notes on the enclosing PrereqParse.
    """
    lowered = clause.lower()
    has_and = re.search(r"\band\b|;", lowered) is not None
    has_or = re.search(r"\bor\b|\bone of\b", lowered) is not None

    if has_and and has_or:
        conjuncts = re.split(r"\band\b|;", clause, flags=re.IGNORECASE)
        and_node = PrereqNode(op="AND")
        for part in conjuncts:
            courses = _extract_courses(part)
            if not courses:
                continue
            if len(courses) == 1:
                and_node.children.append(PrereqNode(course=courses[0]))
            else:
                or_node = PrereqNode(
                    op="OR", children=[PrereqNode(course=c) for c in courses]
                )
                and_node.children.append(or_node)
        if not and_node.children:
            return None
        # Collapse a single-child AND into just that child.
        if len(and_node.children) == 1:
            return and_node.children[0]
        return and_node

    courses = _extract_courses(clause)
    if not courses:
        return None
    if len(courses) == 1:
        return PrereqNode(course=courses[0])
    if has_and:
        return PrereqNode(
            op="AND", children=[PrereqNode(course=c) for c in courses]
        )
    # default to OR — matches "one of", "or", or a bare list
    return PrereqNode(op="OR", children=[PrereqNode(course=c) for c in courses])


def _flatten_edges(
    target: str, node: PrereqNode
) -> list[tuple[str, str, int]]:
    """Turn the tree into (target_course, prereq_course, group_id) rows.

    group_id disambiguates OR groups: all leaves under the same OR node
    share a group_id, so downstream code knows "any one of these
    satisfies the requirement." AND-only prereqs get sequential group
    ids of size 1.
    """
    edges: list[tuple[str, str, int]] = []
    counter = {"g": 0}

    def walk(n: PrereqNode) -> None:
        if n.is_leaf():
            counter["g"] += 1
            assert n.course is not None
            edges.append((target, n.course, counter["g"]))
            return
        if n.op == "OR":
            counter["g"] += 1
            gid = counter["g"]
            for child in n.children:
                if child.is_leaf():
                    assert child.course is not None
                    edges.append((target, child.course, gid))
                else:
                    walk(child)
            return
        # AND
        for child in n.children:
            walk(child)

    walk(node)
    return edges


def parse_prereq_string(
    description: str | None, *, target_course: str
) -> PrereqParse:
    """Public entry point. Returns a PrereqParse (never raises)."""
    if not description:
        return PrereqParse(tree=None, edges=[], raw_clause=None, unparsed_notes=[])

    m = _PREREQ_CLAUSE_RE.search(description)
    if not m:
        return PrereqParse(tree=None, edges=[], raw_clause=None, unparsed_notes=[])

    clause = m.group(1).strip()
    tree = _build_tree(clause)

    notes: list[str] = []
    # Any non-course phrasing we can't act on — record it so a human
    # can look later. Cheap to compute.
    for phrase in [
        "consent of instructor",
        "senior standing",
        "graduate standing",
        "junior standing",
    ]:
        if phrase in clause.lower():
            notes.append(phrase)

    edges = _flatten_edges(target_course, tree) if tree is not None else []
    return PrereqParse(
        tree=tree, edges=edges, raw_clause=clause, unparsed_notes=notes
    )
