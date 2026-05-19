# .agents/skills/shitsuji/evals — skill-creator format eval set

This directory contains a `skill-creator`-compatible eval set for the
shitsuji Skill. It is intended for manual or external behavior review.

| Layer | Format | Tooling | Purpose |
|-------|--------|---------|---------|
| **Behavioral / host-Codex** | `.agents/skills/shitsuji/evals/evals.json` | manual / future Codex eval runner | Verifies Codex compliance with the Mode B rubric: schema correctness, writing-style-aware AI emotion, tone application. Requires running prompts through Codex with hooks enabled. |

## File: `evals.json`

skill-creator format. Five canonical evals covering:

1. `negative-recovery-arc-T1` — sad user, AI emotion is persona-dependent (low-volatility: trust-leaning concern; high-volatility: genuine co-suffering) but never cheerful
2. `anger-attentive-response` — angry user, AI emotion is persona-dependent (low-volatility: composed; high-volatility: congruent solidarity) but never cold/escalating
3. `joy-shared-warmth` — happy user, AI shares joy at persona-aligned intensity
4. `fear-anticipation-mixed` — anxious user, AI provides steady support without amplifying anxiety
5. `technical-only-no-whiplash` — neutral technical question, AI emotion stays near neutral while writing-style controls voice

Each eval has assertion strings that a grader subagent (or human) can
check against the host's saved HISTORY entry and final reply.

## Running with skill-creator

```bash
# Open the skill-creator workspace path; consult its docs.
# In rough outline:
#
#   1. Spawn one with-skill subagent per eval.id, capture outputs to
#      <workspace>/iteration-N/eval-<id>/with_skill/
#   2. Spawn baseline subagents (no skill) for the same prompts.
#   3. Aggregate via:
#         python -m scripts.aggregate_benchmark <workspace>/iteration-N \
#                --skill-name shitsuji
#   4. Open the review with:
#         python <skill-creator-path>/eval-viewer/generate_review.py \
#                <workspace>/iteration-N \
#                --skill-name shitsuji \
#                --benchmark <workspace>/iteration-N/benchmark.json
```

## Description optimizer

The frontmatter description in `.agents/skills/shitsuji/SKILL.md` can be optimized via
skill-creator's `run_loop.py`. See `.agents/skills/shitsuji/evals/trigger_evals.json` for
the 20-query trigger eval set used to score description trigger accuracy.

### Trigger-eval limitation for hook-driven skills

Shitsuji は本来 `UserPromptSubmit` hook で**先回り注入**する設計（host
Codex が能動的に invoke しなくても、hook 側で rubric / mood / dyad context が
既に inject されている）。hook が無効な
環境では構造的に recall が低くなる（実測では現 description でも recall=0%、
specificity=100%）。

これは description の品質問題ではなく**測定環境のミスマッチ**：

- 実運用（hook 有効）：UserPromptSubmit が rubric / mood を毎ターン inject
  → host は skill invoke せずとも emotion-aware な応答ができる
- trigger-eval 環境（hook 無効）：host は skill description だけで invoke
  判断 → 一発応答で済むなら invoke しないのが合理的

そのため description 最適化のシグナルは **specificity（false trigger 防止）**
側を主に見るのが妥当。Codex での本格運用には、hook 起動を伴う E2E test を別途構築する必要がある。

現 description は 1,536 char cap 以内、specificity=100%（10/10 の non-emotional
query で誤発火なし）を維持しつつ、emotional keyword の列挙・side effect
（HISTORY append）・tone calibration の役割を明記する形で documented している。
