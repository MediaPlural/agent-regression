#!/usr/bin/env python3
"""agent_regression.py — the portable agent-stack regression gate.

Code has CI. The agent stack — model, prompts, skills, tools, config — has
vibes. This tool makes one part of it deterministic: MANIFEST every behavior-
affecting file family (hashed) and runtime knob, COMPARE two manifests to
gate a change, VALIDATE golden replay cases. Stdlib only, single file,
any runtime, any platform.

Commands:
    manifest       Capture a stack manifest (family digests + runtime knobs).
    compare         Compare two manifests; exit 1 if behavior-affecting
                   state drifted (CI gate shape). Lists what changed.
    validate-golden Check a golden case JSON against the golden-case contract
                   (required keys, criterion kinds/families consistent).

Docs: https://github.com/MediaPlural/agent-regression
Doctrine suite: https://github.com/MediaPlural/agent-proven (agent-regression skill)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Behavior-affecting file families: keys are family names, values are glob
# patterns relative to the config root. Extend per-runtime as needed.
# ---------------------------------------------------------------------------
DEFAULT_FAMILIES: dict[str, list[str]] = {
    "prompts": ["prompts/**/*", "*.md", "SOUL.md"],
    "skills": ["skills/**/SKILL.md", "skills/**/*.md"],
    "tools": ["tools/**/*.json", "mcp*.json", ".mcp.json"],
    "config": ["config.yaml", "config.json", "*.toml"],
}

EXCLUDE_NAMES = {"__pycache__", ".git", "node_modules", ".venv", "__MACOSX"}

GOLDEN_REQUIRED = {"case_id", "input", "criteria"}
CRITERION_REQUIRED = {"id", "kind", "family", "statement"}
DETERMINISTIC_KINDS = {
    "tool_called", "tool_not_called", "field_value",
    "forbidden_pattern", "file_changed", "exit_code",
}
JUDGED_KINDS = {"required_fact", "rubric_1_5"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def family_digest(paths: list[Path]) -> str:
    """Order-independent digest over a file family (sorted relpath:hash)."""
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: str(x).lower()):
        rel = str(p).replace(os.sep, "/").lower()
        h.update(f"{rel}:{sha256_file(p)}\n".encode())
    return h.hexdigest()


def collect(root: Path, patterns: list[str]) -> list[Path]:
    out: set[Path] = set()
    for pat in patterns:
        for p in root.glob(pat):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_NAMES for part in p.parts):
                continue
            out.add(p)
    return sorted(out)


def build_manifest(root: Path, runtime: dict, families_spec: dict) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"error: root not a directory: {root}")

    families: dict[str, dict] = {}
    for name, patterns in families_spec.items():
        files = collect(root, patterns)
        families[name] = {
            "files": [str(p.relative_to(root)) for p in files],
            "digest": family_digest(files),
        }

    manifest = {
        "schema": "agent-regression.stack-manifest/1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "runtime": runtime,
        "families": families,
    }
    digest_input = json.dumps(
        {k: v["digest"] for k, v in sorted(families.items())}
        | {"runtime": runtime},
        sort_keys=True,
    )
    manifest["manifest_digest"] = hashlib.sha256(digest_input.encode()).hexdigest()
    return manifest


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

def cmd_compare(a_path: Path, b_path: Path) -> int:
    """Exit 0 = behavior-affecting state unchanged; 1 = drifted; 2 = error."""
    try:
        a = json.loads(a_path.read_text())
        b = json.loads(b_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot load manifests: {exc}", file=sys.stderr)
        return 2

    for m, name in ((a, "first"), (b, "second")):
        if "manifest_digest" not in m or "families" not in m:
            print(f"error: {name} manifest missing manifest_digest/families", file=sys.stderr)
            return 2

    if a["manifest_digest"] == b["manifest_digest"]:
        print("compatible: no behavior-affecting drift between manifests")
        return 0

    print("drifted: behavior-affecting state changed between manifests")
    for fam in sorted(set(a["families"]) | set(b["families"])):
        da = a["families"].get(fam, {}).get("digest")
        db = b["families"].get(fam, {}).get("digest")
        if da != db:
            print(f"  family changed: {fam}")
    ra, rb = a.get("runtime", {}), b.get("runtime", {})
    for key in sorted(set(ra) | set(rb)):
        if ra.get(key) != rb.get(key):
            print(f"  runtime changed: {key}: {ra.get(key)!r} -> {rb.get(key)!r}")
    return 1


# ---------------------------------------------------------------------------
# validate-golden
# ---------------------------------------------------------------------------

def cmd_validate_golden(path: Path) -> int:
    """Exit 0 = valid golden case; 1 = invalid (reasons printed)."""
    try:
        case = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid: cannot parse JSON: {exc}", file=sys.stderr)
        return 1

    problems: list[str] = []
    if not isinstance(case, dict):
        print("invalid: case must be a JSON object")
        return 1
    missing = GOLDEN_REQUIRED - set(case)
    if missing:
        problems.append(f"missing required keys: {sorted(missing)}")

    criteria = case.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        problems.append("criteria must be a non-empty list")
    else:
        for i, c in enumerate(criteria):
            if not isinstance(c, dict):
                problems.append(f"criteria[{i}] must be an object")
                continue
            cm = CRITERION_REQUIRED - set(c)
            if cm:
                problems.append(f"criteria[{i}] missing: {sorted(cm)}")
                continue
            if c["kind"] not in DETERMINISTIC_KINDS | JUDGED_KINDS:
                problems.append(f"criteria[{i}] unknown kind: {c['kind']!r}")
            if c["family"] not in ("deterministic", "judged"):
                problems.append(f"criteria[{i}] unknown family: {c['family']!r}")
            expected_family = (
                "judged" if c["kind"] in JUDGED_KINDS else "deterministic"
            )
            if c["family"] != expected_family:
                problems.append(
                    f"criteria[{i}] kind {c['kind']!r} belongs to "
                    f"{expected_family!r}, declared {c['family']!r}"
                )
        if not problems:
            print(
                f"valid: case {case.get('case_id', '?')!r}, "
                f"{len(criteria)} criteria "
                f"({sum(1 for c in criteria if c['family'] == 'deterministic')} deterministic, "
                f"{sum(1 for c in criteria if c['family'] == 'judged')} judged)"
            )
            return 0
    if problems:
        print("invalid:")
        for p in problems:
            print(f"  - {p}")
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="agent_regression",
        description="The portable agent-stack regression gate.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    m = sub.add_parser("manifest", help="Capture a stack manifest.")
    m.add_argument("--root", required=True, help="agent config root")
    m.add_argument("--model", required=True)
    m.add_argument("--provider", required=True)
    m.add_argument("--harness-revision", default=None)
    m.add_argument("--seed", default="0")
    m.add_argument("--out", type=Path, default=Path("stack-manifest.json"))

    c = sub.add_parser("compare", help="Gate two manifests (exit 1 on drift).")
    c.add_argument("baseline", type=Path)
    c.add_argument("candidate", type=Path)

    v = sub.add_parser("validate-golden", help="Validate a golden case JSON.")
    v.add_argument("case", type=Path)

    args = ap.parse_args(argv)

    if args.command == "manifest":
        runtime = {
            "model": args.model,
            "provider": args.provider,
            "harness_revision": args.harness_revision
            or os.environ.get("AGENT_REGRESSION_HARNESS", "unknown"),
            "seed": args.seed,
        }
        manifest = build_manifest(Path(args.root), runtime, DEFAULT_FAMILIES)
        args.out.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"manifest: {args.out}")
        print(f"manifest_digest: {manifest['manifest_digest']}")
        print("families: " + ", ".join(
            f"{k}({len(v['files'])})" for k, v in manifest["families"].items()
        ))
        return 0

    if args.command == "compare":
        return cmd_compare(args.baseline, args.candidate)

    if args.command == "validate-golden":
        return cmd_validate_golden(args.case)

    return 2


if __name__ == "__main__":
    sys.exit(main())