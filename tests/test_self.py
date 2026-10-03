"""Self-test battery for agent_regression.py — the gate tool gates itself.

Runs anywhere: stdlib unittest only — no pytest, no network, no POSIX-only
calls (Windows-safe). Every test is a red/green proof of one command
contract: manifest stability and drift, compare exit codes, golden
validation accept/reject.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_regression as ar  # noqa: E402


def make_stack(root: Path) -> None:
    (root / "prompts").mkdir(parents=True)
    (root / "skills" / "s1").mkdir(parents=True)
    (root / "prompts" / "system.md").write_text("sys prompt\n", encoding="utf-8")
    (root / "skills" / "s1" / "SKILL.md").write_text(
        "---\nname: s1\ndescription: \"S.\"\n---\nbody\n", encoding="utf-8"
    )


def runtime(model: str = "m1", provider: str = "p1") -> dict:
    return {
        "model": model,
        "provider": provider,
        "harness_revision": "test",
        "seed": "0",
    }


def write_manifest(root: Path, out: Path, model: str = "m1") -> dict:
    m = ar.build_manifest(root, runtime(model=model), ar.DEFAULT_FAMILIES)
    out.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")
    return m


class ManifestTests(unittest.TestCase):
    """manifest: identical stacks -> identical digests; any drift -> new digest."""

    def test_stability_identical_digests(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_stack(root)
            m1 = ar.build_manifest(root, runtime(), ar.DEFAULT_FAMILIES)
            m2 = ar.build_manifest(root, runtime(), ar.DEFAULT_FAMILIES)
            self.assertEqual(m1["manifest_digest"], m2["manifest_digest"])

    def test_drift_on_file_change_names_family(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_stack(root)
            before = ar.build_manifest(root, runtime(), ar.DEFAULT_FAMILIES)
            (root / "prompts" / "system.md").write_text(
                "changed prompt\n", encoding="utf-8"
            )
            after = ar.build_manifest(root, runtime(), ar.DEFAULT_FAMILIES)
            self.assertNotEqual(before["manifest_digest"], after["manifest_digest"])
            self.assertNotEqual(
                before["families"]["prompts"]["digest"],
                after["families"]["prompts"]["digest"],
            )
            self.assertEqual(
                before["families"]["skills"]["digest"],
                after["families"]["skills"]["digest"],
            )

    def test_drift_on_model_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_stack(root)
            m1 = ar.build_manifest(root, runtime(model="m1"), ar.DEFAULT_FAMILIES)
            m2 = ar.build_manifest(root, runtime(model="m2"), ar.DEFAULT_FAMILIES)
            self.assertNotEqual(m1["manifest_digest"], m2["manifest_digest"])


class CompareTests(unittest.TestCase):
    """compare: 0 compatible, 1 drifted (reasons named), 2 error."""

    def test_compatible_exit0(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cfg"
            make_stack(root)
            a = Path(td) / "a.json"
            b = Path(td) / "b.json"
            write_manifest(root, a)
            write_manifest(root, b)
            self.assertEqual(ar.cmd_compare(a, b), 0)

    def test_drift_exit1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cfg"
            make_stack(root)
            a = Path(td) / "a.json"
            b = Path(td) / "b.json"
            write_manifest(root, a)
            (root / "prompts" / "system.md").write_text(
                "changed\n", encoding="utf-8"
            )
            write_manifest(root, b)
            self.assertEqual(ar.cmd_compare(a, b), 1)

    def test_error_exit2_on_garbage(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.json"
            p.write_text("not json", encoding="utf-8")
            self.assertEqual(ar.cmd_compare(p, p), 2)


class ValidateGoldenTests(unittest.TestCase):
    """validate-golden: valid passes (0); every invalid shape fails (1)."""

    VALID = {
        "case_id": "case-001",
        "origin": "production-failure",
        "input": "Summarize the Q3 report.",
        "criteria": [
            {"id": "c1", "kind": "tool_called", "family": "deterministic",
             "statement": "Calls get_report.", "expect": "get_report"},
            {"id": "c2", "kind": "required_fact", "family": "judged",
             "statement": "Mentions revenue.", "tolerance": 0.2},
        ],
    }

    def _check(self, case: dict, expected: int) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "case.json"
            p.write_text(json.dumps(case), encoding="utf-8")
            self.assertEqual(ar.cmd_validate_golden(p), expected)

    def test_valid_case_exit0(self):
        self._check(self.VALID, 0)

    def test_unknown_kind_rejected(self):
        case = json.loads(json.dumps(self.VALID))
        case["criteria"][0]["kind"] = "vibes"
        self._check(case, 1)

    def test_kind_family_mismatch_rejected(self):
        case = json.loads(json.dumps(self.VALID))
        case["criteria"][0]["family"] = "judged"  # tool_called is deterministic
        self._check(case, 1)

    def test_missing_keys_rejected(self):
        self._check({"case_id": "x"}, 1)

    def test_empty_criteria_rejected(self):
        case = json.loads(json.dumps(self.VALID))
        case["criteria"] = []
        self._check(case, 1)


if __name__ == "__main__":
    unittest.main()