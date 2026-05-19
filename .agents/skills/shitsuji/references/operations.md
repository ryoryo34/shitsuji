# Operations — 環境変数・失敗時挙動

## 環境変数

| 変数 | 値 | 効果 |
|------|----|------|
| `SHITSUJI_DISABLED` | `1` | UserPromptSubmit hook を no-op 化（policy capsule も注入されない） |
| `SHITSUJI_DYAD_TABLE_ENABLED` | `1` | Plutchik 24-dyad reference を SessionStart に注入する。**default は OFF**（Phase 9b 削減）。rationale に love/hope/awe 等の dyad 名を出させたいとき opt-in する |
| `SHITSUJI_DATA_DIR` | path | `HISTORY.jsonl` / `PERSONA_PROFILE.json` など runtime data の格納先。テスト隔離やプロファイル切替に |
| `SHITSUJI_HOOK_LOG` | `1` | hook 診断ログ `hook.log` を出す。default は OFF |
| `SHITSUJI_SUPPRESS_DEPRECATION` | `1` | 旧 MCP server 起動時の deprecation 通知を抑止 |
| `SHITSUJI_SCHEMA_LENIENT` | `1` | `append.py` の schema validation 違反を warn のみで通す（移行モード）。デフォルトは strict reject |
| `SHITSUJI_REDACT_USER_TEXT` | `1` | `HISTORY.jsonl` の free-text rationale を SHA256 prefix に置換（プライバシー保護モード） |

## 失敗時の挙動

- hook が無効化されている (`SHITSUJI_DISABLED=1`) → policy capsule が
  来ないので、通常応答を返す。
- `compute_mood.py` が失敗 → hook が neutral mood を fallback 注入。応答は
  ニュートラルトーンで OK。
- `dyad-table.md` が読めない / `SHITSUJI_DYAD_TABLE_ENABLED` 未設定 → dyad
  reference セクションは省略される。rubric の "Dyad annotation" は残るが
  host Codex は dyad 名を rationale に含めない（MAY 規定なので問題なし）。
- `append.py` schema 違反（`user_emotion` 欠損、VAD 範囲外、未知の primary 等）
  → exit 2 で reject。rubric を再確認して JSON を組み直す（部分パッチでなく全体再構築）。
- `append.py` 実行を忘れた場合 → 該当ターンの VAD は HISTORY に残らない。
  EMA は leaky integral なので 1〜2 ターン程度の欠損では mood は壊れない。

## Minimal runtime

通常 runtime が永続作成するファイルは以下だけ：

- `HISTORY.jsonl`
- `PERSONA_PROFILE.json`

作らないもの：

- `ADVISORY_CACHE.txt` — 毎ターン snapshot を直接注入するため不要
- `AFFECT_STATE.json` — current state は host が current prompt から判定する
- `PROFILE.md` — SessionStart で in-memory render する
- `EPISODES.md` / `EPISODES.md.archive` — 長期 episode 層は hot path から削除
- `.lock` — `HISTORY.jsonl` 自身に flock する
- `hook.log` — `SHITSUJI_HOOK_LOG=1` のときだけ診断用に作る

UserPromptSubmit は毎ターン short policy capsule を注入する。raw 履歴、current
prompt text、label probabilities、内部 scoring は capsule に含めない。

> **Note**: 旧バージョンには `safety` mode（self-harm keyword 検出時に専用 override を inject する機構）と Stop hook の reflection blocking 機能があったが、前者は host の base safety training に委ねる設計へ寄せ、後者は consumer のない AUDIT.jsonl と footgun リスクのある block 機構を YAGNI で撤去（commit 履歴参照）。skill は personal emotion log + tone calibration に scope を絞り、clinical safety tool / save-rate enforcement の役割は持たない。
