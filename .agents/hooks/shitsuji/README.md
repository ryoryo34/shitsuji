# .agents/hooks/shitsuji

shitsuji Skill の Codex ハーネス層。project-local hooks 機構経由で**決定論的**に発火し、
project-level memory と応答方針の注入を担う。
hook 内の VAD 推定は低信頼度の軽量 auto-memory に限定し、精密な感情理解は
ホスト LLM の文脈判断に委ねる。

hook 入出力 JSON schema は `hookSpecificOutput.{hookEventName,additionalContext}`。

## 一覧

| ファイル | フック | 用途 | context 注入 |
|----------|--------|------|--------------|
| `session_start.py`    | SessionStart    | `PERSONA_PROFILE.json` を作成/更新し、affect/persona の short session capsule を Codex に渡す | ✅ |
| `user_prompt_submit.py` | UserPromptSubmit | 全ユーザーターンを軽量 auto-append + `HISTORY.jsonl.user_emotion` affect dynamics 由来の short response guidance を注入 | ✅ |
| `codex_project_user_prompt_submit.py` | UserPromptSubmit (Codex wrapper) | `SHITSUJI_PROJECT_ROOT` 配下だけで `user_prompt_submit.py` を実行し、project-local memory に保存 | ✅ |

## ハーネス契約

すべての hook は **fail-safe**:

- 内部エラー時も stdout には**no-op JSON** を返す。`SHITSUJI_HOOK_LOG=1` のときだけ runtime data dir の `hook.log` に診断ログを書く
- 全 hook が timeout を持つ → Codex を待たせない
- exit code は常に 0（hook 失敗で Codex のターンを止めない）

実測レイテンシ: UserPromptSubmit hook = **~150ms**（M1 mac）。

無効化:

```bash
SHITSUJI_DISABLED=1 codex   # UserPromptSubmit を no-op 化
```

## ホスト Codex との分担

| 算術層（hook） | AI 層（host Codex） |
|----------------|---------------------|
| HISTORY.jsonl 読み書き | 精密な current user VAD 判定 |
| 軽量 auto-memory 推定（low-confidence sidecar） | tone / response guidance 反映 |
| user_emotion affect dynamics / VFE helper | response guidance の背景解釈 |
| short response guidance の context 注入 | recall クエリへの応答 |

LLM 呼び出しを hook subprocess で行わない。HISTORY への最低限の保存は hook が自動で行うが、
その軽量 observation は response driver ではない。ホスト Codex は short
response guidance 内の `analysis_summary` / `response_surface` / `insight` を読み、
現在文脈で返答スタイルを再スコアする。
guidance は raw 履歴、current prompt text、label probabilities、内部 scoring を
含めない。VFE / regulation / response_control の詳細は `SHITSUJI_HOOK_LOG=1`
のときだけ `hook.log` の `affect_debug` JSON として残す。

## 設定

- **Codex**: `~/.codex/config.toml` で `[features] hooks = true` を有効化する。
  project-level 運用では SessionStart に `SHITSUJI_PROJECT_ROOT` /
  `SHITSUJI_CWD` / `SHITSUJI_DATA_DIR`、UserPromptSubmit に
  `SHITSUJI_PROJECT_ROOT` を渡す。

## デバッグ

- `<project>/.shitsuji/HISTORY.jsonl` — 感情イベント append-only ログ
- `<project>/.shitsuji/PERSONA_PROFILE.json` — `AGENTS.md` 由来の構造化 persona
- `<project>/.shitsuji/hook.log` — `SHITSUJI_HOOK_LOG=1` のときだけ作られる診断ログ

`SHITSUJI_DATA_DIR` 未指定時は fallback として
`.agents/skills/shitsuji/data/` を使う。この fallback path も repository 側で
ignore している。

各 hook は手で叩いて単体検証できる:

```bash
echo '{"prompt":"テスト"}' | .agents/hooks/shitsuji/user_prompt_submit.py
echo '{"source":"startup"}' | SHITSUJI_PROJECT_ROOT="$(pwd)" SHITSUJI_CWD="$(pwd)" SHITSUJI_DATA_DIR="$(pwd)/.shitsuji" .agents/hooks/shitsuji/session_start.py
```
