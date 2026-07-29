"""Tests for the prerequisite parser.

These are the cases that motivated the parser design. Read them
top-to-bottom for a tour of what shapes we do and don't handle.
"""

from src.pipeline.prereq_parser import parse_prereq_string


def test_no_prereq_returns_empty():
    r = parse_prereq_string("Introductory course. No prerequisites.", target_course="CS 125")
    assert r.tree is None
    assert r.edges == []


def test_single_prereq_produces_leaf():
    r = parse_prereq_string("Prerequisite: CS 225.", target_course="CS 233")
    assert r.tree is not None
    assert r.tree.is_leaf()
    assert r.tree.course == "CS 225"
    assert r.edges == [("CS 233", "CS 225", 1)]


def test_and_only_produces_and_of_leaves():
    r = parse_prereq_string(
        "Prerequisite: CS 225 and CS 233.", target_course="CS 241"
    )
    assert r.tree is not None and r.tree.op == "AND"
    # Two AND-children, each in its own group_id.
    prereqs = {e[1] for e in r.edges}
    groups = {e[2] for e in r.edges}
    assert prereqs == {"CS 225", "CS 233"}
    assert len(groups) == 2  # different groups => ANDed


def test_or_only_produces_or_group():
    r = parse_prereq_string(
        "Prerequisite: CS 125 or CS 128.", target_course="CS 225"
    )
    assert r.tree is not None and r.tree.op == "OR"
    # Both prereqs share the same group_id => OR.
    groups = {e[2] for e in r.edges}
    assert len(groups) == 1
    assert {e[1] for e in r.edges} == {"CS 125", "CS 128"}


def test_mixed_and_or_puts_or_inside_and():
    r = parse_prereq_string(
        "Prerequisite: CS 173 and one of CS 125 or CS 128.",
        target_course="CS 225",
    )
    assert r.tree is not None and r.tree.op == "AND"
    edges = r.edges
    # CS 173 in its own group; CS 125 and CS 128 share a group.
    by_prereq = {e[1]: e[2] for e in edges}
    assert by_prereq["CS 125"] == by_prereq["CS 128"]
    assert by_prereq["CS 173"] != by_prereq["CS 125"]


def test_records_unparsed_notes():
    r = parse_prereq_string(
        "Prerequisite: CS 233 and CS 374; consent of instructor.",
        target_course="CS 421",
    )
    assert "consent of instructor" in r.unparsed_notes


def test_cross_subject_prereq():
    r = parse_prereq_string(
        "Prerequisite: one of CS 225 or ECE 220; CS 173.",
        target_course="CS 374",
    )
    assert r.tree is not None
    prereqs = {e[1] for e in r.edges}
    assert "ECE 220" in prereqs
