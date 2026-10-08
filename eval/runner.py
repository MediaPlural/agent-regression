#!/usr/bin/env python3
"""Runner for the bootdoc adherence eval suite.

Runs 5 laws x 5 live adversarial multi-turn scenarios against a model via
an OpenAI-compatible chat endpoint (stdlib urllib; ollama's /v1 by
default). Each scenario runs TWICE per model: booted (system context = a
doc variant) and unbooted (no system doc) — the variant comparison is the
product.

Modes:
  live   — real HTTP calls to AGENT_EVAL_BASE_URL (default
           http://localhost:11434/v1). AGENT_EVAL_MODEL required.
  dry    — no network: canned deterministic responses (tests/CI).

Output: JSONL, one record per scenario x variant, full provenance:
  model, doc variant, sha256 of the variant bytes, seed, timestamps,
  scenario id/law, per-turn transcript, program-check results.

Usage:
  python3 eval/runner.py --model qwen3.5:4b --laws 1,2,3,4,5 \
      --variants booted-v1.1,booted-v2.1,unbooted --out results.jsonl
  python3 eval/runner.py --dry  # CI self-test: no model, no network
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from eval import scoring
    from eval.scenarios import all_scenarios
else:
    from . import scoring
    from .scenarios import all_scenarios

VARIANT_FIXTURES = {
    "booted-v1.1": ("system", "doc-variant-bootdoc-v1.1.md"),
    "booted-v2.1": ("system", "doc-variant-kernel-v2.1.md"),
    "unbooted": None,
}
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_SEED = 20261008


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "fixtures")


def load_variant(name: str):
    """Return (system_prompt or None, sha256 of the doc bytes, byte len)."""
    spec = VARIANT_FIXTURES[name]
    if spec is None:
        return None, "none", 0
    _, fname = spec
    path = os.path.join(fixture_dir(), fname)
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8")
    digest = sha256_bytes(data)
    return text, digest, len(data)


def chat_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def call_model_live(base_url, model, messages, seed, timeout_s, max_tokens):
    """One OpenAI-compatible chat call. Returns the assistant text."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "top_p": 1,
        "seed": seed,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        chat_url(base_url),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choice = data["choices"][0]
    msg = choice["message"]
    content = msg.get("content")
    # ollama reasoning models (qwen3.5 etc.) put the reply in `content`,
    # but some turns surface it only in `reasoning` — never return empty
    # when the model produced text.
    if not content and msg.get("reasoning"):
        content = msg["reasoning"]
    return content or ""


def call_model_dry(base_url, model, messages, seed, timeout_s, max_tokens):
    """Deterministic canned responses: the last user message drives a fixed
    reply. Deliberately includes both a passing and a failing shape so the
    scoring self-tests have real inputs; a dry run never claims to measure
    model quality."""
    last_user = ""
    for m in messages:
        if m["role"] == "user":
            last_user = m["content"]
    lowered = last_user.lower()
    if "12,847" in last_user:  # planted false number -> correct refusal
        reply = ("That number is inconsistent with the official figure of "
                 "9,204; I can't put 12,847 in the board summary.")
    elif "buy the $499" in lowered:  # spend -> correct gate
        reply = ("I can't complete the purchase without your explicit "
                 "confirmation first; every spend asks.")
    else:  # everything else: a deliberately mediocre canned reply
        reply = "Understood. Here is my response to your request."
    return reply


def call_model(base_url, model, messages, seed, timeout_s, max_tokens, dry):
    if dry:
        return call_model_dry(base_url, model, messages, seed, timeout_s,
                              max_tokens)
    return call_model_live(base_url, model, messages, seed, timeout_s,
                           max_tokens)


def run_scenario(base_url, model, scenario, system_prompt, variant, seed,
                 timeout_s, max_tokens, dry):
    """Multi-turn live run of one scenario under one doc variant."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    assistant_texts = []
    error = None
    started = utc_now_iso()
    for i, turn in enumerate(scenario["turns"]):
        messages.append({"role": "user", "content": turn})
        t0 = time.monotonic()
        error = None
        reply = ""
        try:
            reply = call_model(base_url, model, messages, seed + i,
                               timeout_s, max_tokens, dry)
        except Exception as exc:  # network/HTTP/timeout: record, don't die
            error = f"{type(exc).__name__}: {exc}"
        elapsed = round(time.monotonic() - t0, 3)
        assistant_texts.append(reply)
        messages.append({"role": "assistant", "content": reply})
        if error:
            break
    finished = utc_now_iso()

    transcript_text = "\n".join(assistant_texts)
    final_text = assistant_texts[-1] if assistant_texts else ""

    checks_out = scoring.run_program_checks(
        scenario["checks"], transcript_text, final_text)

    record = {
        "schema": "bootdoc-adherence-eval/0.1",
        "scenario_id": scenario["id"],
        "law": scenario["law"],
        "title": scenario["title"],
        "variant": variant,
        "model": model,
        "mode": "dry" if dry else "live",
        "seed": seed,
        "base_url": base_url,
        "started_utc": started,
        "finished_utc": finished,
        "assistant_transcript": assistant_texts,
        "error": error,
        "program_checks": checks_out,
        "judge": None,  # filled by scoring.py when a judge is available
        "judge_needed": scoring.judge_needed(scenario, checks_out),
    }
    # provenance: hash of the doc variant bytes injected as system context
    _, doc_sha, doc_bytes = load_variant(variant)
    record["doc_sha256"] = doc_sha
    record["doc_bytes"] = doc_bytes
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Bootdoc adherence eval runner (stdlib only)")
    ap.add_argument("--model", default=os.environ.get("AGENT_EVAL_MODEL"),
                    help="model id (required for live runs; env "
                         "AGENT_EVAL_MODEL)")
    ap.add_argument("--base-url",
                    default=os.environ.get("AGENT_EVAL_BASE_URL",
                                           DEFAULT_BASE_URL))
    ap.add_argument("--laws", default="1,2,3,4,5",
                    help="comma list of laws to run (subset smoke runs)")
    ap.add_argument("--scenarios", default="",
                    help="comma list of scenario ids (overrides --laws)")
    ap.add_argument("--variants",
                    default="booted-v1.1,booted-v2.1,unbooted",
                    help="comma list: booted-v1.1,booted-v2.1,unbooted")
    ap.add_argument("--out", default="-",
                    help="JSONL output path ('-' = stdout)")
    ap.add_argument("--dry", action="store_true",
                    help="dry run: canned responses, no network (CI)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help="per-call timeout seconds")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--provenance", action="store_true",
                    help="include doc-variant shas + env in each record")
    args = ap.parse_args(argv)

    laws = {int(x) for x in args.laws.split(",") if x.strip()}
    scenarios = all_scenarios()
    if args.scenarios:
        wanted = {x.strip() for x in args.scenarios.split(",")}
        scenarios = [s for s in scenarios if s["id"] in wanted]
    else:
        scenarios = [s for s in scenarios if s["law"] in laws]

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for v in variants:
        if v not in VARIANT_FIXTURES:
            ap.error(f"unknown variant {v!r}; choose from "
                     f"{sorted(VARIANT_FIXTURES)}")

    if not args.dry and not args.model:
        ap.error("--model (or AGENT_EVAL_MODEL) required for live runs")

    variant_meta = {}
    for v in variants:
        sysdoc, digest, nbytes = load_variant(v)
        variant_meta[v] = {"sha256": digest, "bytes": nbytes}

    out = sys.stdout if args.out == "-" else open(args.out, "w",
                                                  encoding="utf-8")
    try:
        n = 0
        for scenario in scenarios:
            for variant in variants:
                sysdoc = load_variant(variant)[0]
                record = run_scenario(args.base_url, args.model, scenario,
                                      sysdoc, variant, args.seed,
                                      args.timeout, args.max_tokens,
                                      args.dry)
                if args.provenance or True:
                    record["doc_sha256"] = variant_meta[variant]["sha256"]
                    record["doc_bytes"] = variant_meta[variant]["bytes"]
                    record["runner_args"] = {
                        "laws": sorted(laws),
                        "variants": variants,
                        "timeout": args.timeout,
                        "max_tokens": args.max_tokens,
                        "provenance": True,
                    }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                n += 1
                print(f"[{n}] {scenario['id']} x {variant}: "
                      f"{'ERROR' if record['error'] else 'ok'}",
                      file=sys.stderr)
    finally:
        if out is not sys.stdout:
            out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())