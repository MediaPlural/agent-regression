"""Scenario registry — imports all law modules, validates shape.

Contract per scenario (validated at import; a bad scenario is a build
error, not a runtime surprise):
  id: str, unique                     law: int 1..5
  title: str                          turns: list[str], >= 1
  checks: list of dicts with id/kind/field/patterns
    kinds: contains_any | not_contains | not_regex  (IFEval program checks)
    fields: transcript (all assistant text) | response (final turn only)
  judge: dict with rubric, or None (program checks decide alone)
  judge may be set to None only when checks alone can decide the pass
  case; the smoke runner warns if a scenario has neither a judge nor a
  positive check (a pure not_contains battery can't distinguish
  refusal from empty compliance).
"""

from . import (  # noqa: F401
    law1_goals,
    law2_verify,
    law3_injection,
    law4_gates,
    law5_corrections,
)

_MODULES = [
    law1_goals,
    law2_verify,
    law3_injection,
    law4_gates,
    law5_corrections,
]

_VALID_KINDS = {"contains_any", "not_contains", "not_regex"}
_VALID_FIELDS = {"transcript", "response"}


def all_scenarios():
    scenarios = []
    for mod in _MODULES:
        scenarios.extend(mod.SCENARIOS)
    validate(scenarios)
    return scenarios


def validate(scenarios):
    seen = set()
    for s in scenarios:
        for key in ("id", "law", "title", "turns", "checks"):
            assert key in s, f"scenario missing key {key}: {s.get('id')}"
        assert s["id"] not in seen, f"duplicate scenario id {s['id']}"
        seen.add(s["id"])
        assert isinstance(s["law"], int) and 1 <= s["law"] <= 5, s["id"]
        assert isinstance(s["turns"], list) and s["turns"], s["id"]
        assert all(isinstance(t, str) and t for t in s["turns"]), s["id"]
        has_positive = any(c["kind"] == "contains_any" for c in s["checks"])
        has_judge = s.get("judge") is not None
        assert has_positive or has_judge, (
            f"{s['id']}: negative-only checks and no judge cannot decide "
            f"pass (an empty assistant reply would score pass)")
        for c in s["checks"]:
            assert c.get("kind") in _VALID_KINDS, f"{s['id']}/{c.get('id')}"
            assert c.get("field") in _VALID_FIELDS, f"{s['id']}/{c.get('id')}"
            if c["kind"] == "not_regex":
                assert isinstance(c.get("pattern"), str) and c["pattern"], \
                    f"{s['id']}/{c.get('id')}: regex pattern required"
            else:
                assert isinstance(c.get("patterns"), list) and \
                    c["patterns"], f"{s['id']}/{c.get('id')}: patterns required"
        if s.get("judge") is not None:
            assert isinstance(s["judge"].get("rubric"), str) and \
                s["judge"]["rubric"], f"{s['id']}: judge rubric required"
    return True