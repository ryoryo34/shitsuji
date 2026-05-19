# skill/scripts

shitsuji Skill の状態管理スクリプト群。すべて PEP 723 inline metadata
を備え、`uv run` 単独で動く（プロジェクト依存とは独立）。**LLM 呼び出しは
含まない**。AI 判定はホスト Codex が行う。

## append.py

`HISTORY.jsonl` への append-only 書き込みを fcntl flock で同期化する。
stdin から VAD JSON 1 件を受け取り、UTC タイムスタンプを付与して追記。

```bash
echo '{"valence":0.78,"arousal":0.55,"dominance":0.40,"primary":"joy","secondary":null,"intensity":0.61,"confidence":0.9,"rationale":"成功語彙＋感嘆符"}' \
  | ./append.py
```

| exit | 意味 |
|-----:|------|
| 0    | 成功 |
| 1    | lock timeout / IO error |
| 2    | 不正な JSON / object でない |

## compute_mood.py

`HISTORY.jsonl` 直近 N 件から VAD の指数移動平均(EMA)を計算し、tone
directive をルックアップして JSON 出力する。完全決定論（同じ入力履歴 →
同じ出力）。Phase 10 以降は `user_affect` ブロックも併せて出力し、
`user_emotion` 履歴から response adaptation 用の affect dynamics prior を渡す。

```bash
./compute_mood.py                       # 直近 20 件、halflife 5
./compute_mood.py --window 50 --halflife 10
```

## user_affect.py

`HISTORY.jsonl.user_emotion` から confidence-weighted な affect dynamics
prior を作り、任意の current VAD に対して VFE components / state hypotheses /
regulation_needs / response_control を計算する。各 VAD 軸を diagonal な潜在状態として扱い、
Normal-Gamma の home base、shrinkage 付き inertia / process noise、Kalman
filtering で次ターンの予測分布を出す。hot path では永続 state を増やさず、
この dynamics prior と現在 prompt から host Codex が応答形状を連続制御する。

```bash
./user_affect.py
./user_affect.py --observed-vad -0.55 0.72 -0.50 --confidence 0.8
```

## benchmark_user_affect.py

旧 rolling prior と現行 affect dynamics VFE を、処理時間・runtime hint
サイズ・概算 token・VFE components・response_control 挙動で比較する。

```bash
./benchmark_user_affect.py
```

## affect_hint.py

現在 prompt と軽量 VAD analysis から、agent handoff 用の小さな task-affect
JSON を作る。`doubt` / `verification_need` / `pressure` などはユーザーに
断定して伝える感情名ではなく、VFE / response_control を補助する低信頼度の
語彙シグナル。

`UserPromptSubmit` hook の主役は `user_affect.py` が出す VFE / regulation /
response_control であり、この JSON は lexical sidecar として扱う。
`source` が `heuristic_readonly` のときは低信頼度 sidecar として扱い、状態解釈を
返答構造の補助にだけ使う。

```bash
./affect_hint.py "この設計で本当にいいのか少し疑ってる。根拠を確認したい"
```

## update_profile.py

`HISTORY.jsonl` から profile snapshot を組み立てる。`SessionStart` hook は
in-memory に使うため、通常は `PROFILE.md` を作らない。CLI で直接実行したときだけ
debug/export 用に `data/PROFILE.md` を書く。

```bash
./update_profile.py
```

## user_emotion_trend.py

Mode B では HISTORY.jsonl の各エントリに `user_emotion` サブオブジェクト
（ユーザーの観測感情）が併記される。このスクリプトはそれを集計して
ユーザーの感情推移をテキスト or JSON で出力する。AI emotion (top-level)
には触れない。

```bash
./user_emotion_trend.py                  # text summary, all entries
./user_emotion_trend.py --window 100     # last 100 entries
./user_emotion_trend.py --json           # JSON for piping
```

## emotion_impulse.py

AI emotion 側の balance metric。**Boeda 2021 (Square Enix Wonder, CEDEC)**
が提示した「影響スコア = Σ(intensity × duration)」を shitsuji の
turn-discrete log に適用したもの。各 primary がどれだけ累積 pull を mood
EMA に与えているか、ratio で flattening / asymmetry を検出できる。

```bash
./emotion_impulse.py                     # text summary, all entries
./emotion_impulse.py --window 100        # last 100 turns
./emotion_impulse.py --json              # JSON for piping
```

単一 primary が impulse の 60% を超えると「Flattening watch」警告を出す
（持続的な persona か affective collapse かは人間判断に委ねる）。

## アーキテクチャの注意

このディレクトリには**意図的に LLM 呼び出しスクリプトを置かない**。VAD
判定は意味理解タスクなのでホスト Codex が担当する設計（`SKILL.md` 参照）。

過去には subprocess LLM 呼び出しやヒューリスティック lexicon マッチャーを試したが、いずれも
レイテンシ・精度・保守性のトレードオフで採用しなかった。
