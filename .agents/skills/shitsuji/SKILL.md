---
name: shitsuji
description: ユーザー発話を project-local な `HISTORY.jsonl` に軽量自動保存し、`HISTORY.jsonl.user_emotion` 由来の affect dynamics prior と `PERSONA_PROFILE.json` 由来の writing-style persona で response guidance を調整する emotion-adaptive response skill。感情を断定するためではなく、話しやすい返し方を選ぶために使う。通常 runtime の永続ファイルは `HISTORY.jsonl` と `PERSONA_PROFILE.json` に絞る。
---

# shitsuji Skill — 対話相手として応答する AI（Mode B）

「ユーザーの感情を真似る」のではなく「ユーザーの感情を観測した上で、AI 自身が
独立した感情で反応する」設計（Mode B）。背景理論は `references/` を参照。

## 必ず実行する手順（毎ユーザーターン）

ユーザーメッセージを受け取ったら、応答を返す**前に**以下を順に実行する。

### Step 0（初回 / stale 検出時のみ）: persona profile を導出

SessionStart hook が `SHITSUJI_DATA_DIR/PERSONA_PROFILE.json` の有無と source-hash を check
する。missing / stale なら hook 内の Python が writing-style persona から
{volatility, warmth, expressive_range, technical_rigor} を保守的に自動推定して保存する。
この処理は `additionalContext` に長い rubric を注入しない。

### Step 1: user_emotion / response guidance を判定する

毎ターン UserPromptSubmit hook が **shitsuji response guidance** を
`additionalContext` に注入する。guidance は raw 履歴や current prompt text を含まず、
`analysis_summary` / `response_surface` / `insight` だけを渡す。
それを読んで：

1. **user_emotion** をユーザーのメッセージから観測（valence, arousal,
   dominance, primary, rationale）
2. 履歴から affect dynamics prior（home base / inertia / process noise / predicted VAD）を作る
3. 現在の `user_emotion` と prior から VFE proxy を分解し、state hypotheses /
   regulation_needs / response_control / response_surface を直接計算する。感情名は断定せず、
   LLM には圧縮済み guidance だけを表に出す
4. guidance の `analysis_summary` / `response_surface` / `insight` を参考にする。
   これは低信頼度の state signal であり、感情名や内部 scoring をユーザーに
   表示したり、固定文言の応答方針として扱ったりするためのものではない。
User affect dynamics prior は `scripts/user_affect.py` が `HISTORY.jsonl` 内の
`user_emotion` から confidence-weighted affect dynamics prior として算出する。
これは静的な平均ではなく、home base / inertia / process noise を持つ軽量な
diagonal VB/Kalman 近似で、hook には predicted VAD と VFE/control surface を渡す。
hook の lightweight auto-memory 推定は dynamics prior を育てるための低信頼度 sidecar
であり、response_control の driver として扱わない。AI mood EMA は hot path から
削除済み。
ユーザーへの返し方は現在メッセージから host が判定した response guidance と
writing-style persona で決め、VFE/regulation/response_control の詳細は
`SHITSUJI_HOOK_LOG=1` の診断ログでのみ確認する。

### Step 2: project-level auto memory を確認する

UserPromptSubmit hook は、LLM を呼ばない軽量推定で**全ユーザーターン**を
`HISTORY.jsonl` に自動保存する。挨拶・技術質問・雑談・感情表出をすべて
project-local な観測として残し、`user_emotion` affect dynamics prior を育てる。

保存先は `SHITSUJI_DATA_DIR` で決まる。project-level 運用では
`<project>/.shitsuji` のようなプロジェクト内ディレクトリを指定し、
他プロジェクトの履歴と混ぜない。

top-level VAD は legacy compatibility のため `user_emotion` と同じ軽量観測を
mirror する。AI mood EMA は hot path から削除済みで、応答方針の主役は
host-scored current user_emotion 由来の response guidance と、
`HISTORY.jsonl.user_emotion` から作る affect dynamics prior / VFE。
自動保存を止めたい場合のみ `SHITSUJI_AUTO_APPEND=0` を設定する。

### Step 3: 応答を組み立てる

3 要素を組み合わせて 1 reply のトーンを決める：

1. **response guidance** — analysis_summary / response_surface / insight
2. **Writing-style persona** — voice / vocabulary / register
   （global / project / nested `AGENTS.override.md`・`AGENTS.md` など Codex instruction sources が persona 源）
3. **Intensity** — Step 2 formula による cap

snapshot の `advisory` 1 文は affective state の sanity check として使う。

## 永続ファイルの最小仕様

通常 runtime で作るファイルは以下だけ：

- `HISTORY.jsonl` — 全ユーザーターンの軽量 auto-memory と affect dynamics source
- `PERSONA_PROFILE.json` — `AGENTS.md` instruction chain から導出した persona cache

`PROFILE.md` / `AFFECT_STATE.json` / `ADVISORY_CACHE.txt` / `EPISODES.md` /
`.lock` / `hook.log` は通常作らない。診断ログが必要なときだけ
`SHITSUJI_HOOK_LOG=1` を設定する。このとき `hook.log` に VFE / regulation /
response_control / response_surface を含む `affect_debug` JSON を残す。

## メモリ参照（recall）

ユーザーが過去の感情に言及した場合（「前回むかついた件」「先週の喜び」等）：

```bash
grep -i "<keyword>" <project>/.shitsuji/HISTORY.jsonl | tail -5
```

統計が必要なら：
- `scripts/user_emotion_trend.py` — ユーザー側 VAD 統計 + sparkline
  （top-level VAD は AI 側なので、ユーザー観測値だけ集計したいときこちら）
- `scripts/emotion_impulse.py` — AI 側 primary 別 impulse score
  （flattening / asymmetric persona expression の検出に有効）

## 失敗時の挙動（簡易）

- `SHITSUJI_DISABLED=1` → Skill 発動なし、通常応答
- `SHITSUJI_AUTO_APPEND=0` → hook は注入だけ行い、HISTORY には自動保存しない
- `user_affect` 計算失敗 → neutral broad prior fallback
- `append.py` schema 違反 → exit 2 で reject、rubric を再確認して再送信
- 自動 append 失敗 → `SHITSUJI_HOOK_LOG=1` のときだけ `hook.log` に記録し、回答生成は止めない

詳細・残りの環境変数は `references/operations.md` を参照。

## さらに詳しく

- `references/mode-b-design.md` — Mode B 設計思想・reactive empathy・失敗事例
- `references/mood-mechanics.md` — persona / EMA mechanics / dyad cluster
- `references/operations.md` — 環境変数表・設計原理
- `prompts/vad-rubric.md` — VAD scoring rubric 全文（参照用。hook context には注入しない）
- `prompts/dyad-table.md` — Plutchik 24-dyad reference
- `scripts/README.md` / `hooks/README.md` — 各層の API 仕様
