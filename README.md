# agent-regression

**The portable regression gate for AI agent stacks — upgrade the model, edit the prompt, patch a skill; keep the agent's promises.**

Code has CI. The agent stack — model, prompts, skills, tools, config — has
vibes. Any of those changes behavior silently: a model swap breaks one lane's
hard tasks, a prompt edit drops a sentence users needed, a skill patch
reroutes a workflow, a tool schema tweak sends wrong parameters. This tool
makes the change *gated*: manifest every behavior-affecting file and knob,
compare before and after, and let CI fail the change instead of production
discovering it.

Single file. Stdlib only. No provider keys, no platform, any runtime.

## Why this exists

The ecosystem covers fragments — trace-comparison engines require you to
build the harness; eval platforms are walled; regression guides describe
practice without portable tooling. This repo ships the missing local-first
layer:

- **Manifest** — hash every behavior-affecting file family (prompts, SOUL,
  skills, tool schemas, config) and record runtime knobs (model, provider,
  harness revision, seed). The manifest digest changes exactly when
  behavior-affecting state changes.
- **Compare** — the CI gate: two manifests in, exit 0 (compatible) or exit 1
  (drifted, with the changed families and knobs named). Drop it in any
  pipeline.
- **Validate-golden** — golden replay cases (frozen production tasks with
  fixtures and criteria) checked against the contract: required keys,
  known criterion kinds, kind/family consistency — bad cases fail loudly
  before they pollute a suite.

Born from a real failure class: a provider/model swap that passed easy tasks
while silently returning empty answers on hard ones — caught only because a
bench happened to run after the wire. The doctrine layer (when to manifest,
how goldens grow from production, one-variable replay discipline, gate
policy) lives in the
[agent-proven library's agent-regression suite](https://github.com/MediaPlural/agent-proven/tree/master/skills/agent-regression);
this repo is the runnable tool.

## Install

```bash
git clone https://github.com/MediaPlural/agent-regression
# no dependencies; single file, Python 3.9+
```

## Use

```bash
# 1. Capture the baseline manifest
python3 agent_regression.py manifest \
    --root ~/.agent-config \
    --model gpt-4o --provider openai \
    --out baseline-manifest.json

# 2. Apply exactly ONE change (model OR prompt OR skill OR tool schema)

# 3. Capture the candidate manifest
python3 agent_regression.py manifest \
    --root ~/.agent-config \
    --model gpt-5 --provider openai \
    --out candidate-manifest.json

# 4. Gate it
python3 agent_regression.py compare baseline-manifest.json candidate-manifest.json
echo $?   # 0 = compatible, 1 = drifted, 2 = error
```

CI shape (GitHub Actions):

```yaml
- run: python3 agent_regression.py manifest --root ${{ env.CFG }} --model ${{ env.MODEL }} --provider ${{ env.PROVIDER }} --out cand.json
- run: python3 agent_regression.py compare base.json cand.json
```

Golden case contract check:

```bash
python3 agent_regression.py validate-golden cases/my-case.json
```

See `examples/` for a golden case, and `schemas/golden-case.schema.json`
for the contract.

## Credits

- [behaviorlock](https://github.com/christian140903-sudo/behaviorlock) —
  deterministic trace-comparison contract; this repo complements it: emit
  goldens as traces and behaviorlock contracts them, while this tool owns
  the manifest/replay/case layers.
- [Runtype's regression guide](https://www.runtype.com/guides/prompt-change-regression-testing) —
  criterion families (deterministic gates, judged review), flake quarantine.
- [Latitude](https://latitude.so/blog/catch-regressions-after-a-hermes-update) —
  manifest completeness (hash skills and SOUL, not just model names).
- [agentpatterns](https://www.agentpatterns.ai/workflows/simulation-replay-testing/) —
  golden-task replay from merged work.

## License

MIT — see [LICENSE](LICENSE).