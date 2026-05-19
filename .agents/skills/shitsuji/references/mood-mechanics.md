# Mood mechanics

shitsuji Skill の mood EMA 側の仕組みのみを扱う。**persona / character / voice
の話はここではしない**（→ それは Codex の `AGENTS.md` / `~/.codex/instructions.md` が
唯一の管轄）。

## 単一の persona 源：writing-style + 派生 PERSONA_PROFILE

このスキルは独自の persona ファイル（旧 `persona.json`）を持たない。
AI のキャラクター・口調・感情起伏の特性は **Codex の writing-style
persona** が単一の source of truth：

| 層 | オーナー | 例 |
|----|---------|-----|
| Writing-style persona = **the persona** | `AGENTS.md` / `~/.codex/instructions.md` | "ギャル"、"formal counselor"、"stoic engineer"、"漫才ボケツッコミ" |
| Persona profile 派生物 | `<project>/.shitsuji/PERSONA_PROFILE.json` — hook が writing-style から保守的に自動導出 | `{volatility, warmth, expressive_range, technical_rigor}` の数値化 |
| Mood EMA（このスキル） | `<project>/.shitsuji/HISTORY.jsonl` + `compute_mood.py` | 直近 N ターンの AI emotion を平滑化した leaky integrator |

### Persona profile の派生フロー

1. SessionStart hook が `PERSONA_PROFILE.json` の存在と source-hash を check
2. missing / stale なら hook 内の Python が writing-style sources
   （AGENTS.md / Codex instructions）を読む
3. `{volatility 0..1, warmth -1..1, expressive_range 0..1, technical_rigor 0..1}`
   を保守的に自動導出
4. `scripts/persona_profile.py` 経由で atomic save、source_hash も自動付与
5. 以降のターンは UserPromptSubmit が profile を context に inject、
   compute_mood が halflife を adaptive 化

### なぜ「self-derive 毎ターン」じゃなく cache するのか

旧 rubric は host に「毎ターン writing-style persona を self-consult」と指示
していたが、host の解釈がターンごとにブレて結果的に flat 化した。

profile を cache することで：

- volatility が **observable な数値** になる（grep / git diff 可能）
- compute_mood が halflife を **物理的に変える**（high vol → halflife=2 で素早く反応・素早く減衰）
- rubric の AI primary intensity 上限が profile から **一貫した formula** で決まる
- writing-style 変更は source_hash 不一致で自動検出 → 次セッションで自動 refresh

## EMA mood の計算

`compute_mood.py` が直近 N ターン（default 20）の AI emotion を読み、**confidence-
weighted exponentially weighted moving average** を計算する。

### Initial state

EMA の初期値は **(0, 0, 0) = neutral**。HISTORY が空のセッションでは mood は
neutral から始まり、ターンが進むごとに直近 AI emotion 群へ漂流する。

> 旧バージョンでは `persona.json` から Big Five → PAD baseline を導出して
> EMA 初期値を anchor していたが、persona は writing-style に統合され、
> mood は「ニュートラルから始まり経験で形成される」純粋な後天的状態に
> なった。anchor を持たないことで、persona-flattening（mood が常に
> persona 中心に引き戻される問題）は構造的に発生しない。
>
> なお、ライブラリ側（`src/shitsuji/models/personality.py`）の
> `Personality.from_big_five` は Mehrabian (1996) canonical regression
> (Australian J. Psychology 48(2), Eq. 11C/12C/13C) を採用し、MCP server /
> 直接ライブラリ利用者向けに引き続き提供される。**スキル層**だけが Big Five
> 経路を持たない。

### Confidence-weighted alpha

各エントリの実効 α = `base_alpha × max(confidence, 0.1)`。

- 曖昧な入力（confidence 低い）は mood をほとんど動かさない
- 0.1 floor で完全停止を防ぐ
- `base_alpha = 1 - exp(ln(0.5) / halflife)`

### Adaptive halflife

`compute_mood.py` は profile が存在すれば `halflife = 2 + 10 * (1 - volatility)` で
adaptive に決定する：

| volatility | halflife | 効果 |
|-----------:|---------:|------|
| 0.0 (stoic) | 12 | 強い慣性、平均化が支配 |
| 0.5 (default) | 7 | 中庸 |
| 1.0 (theatrical) | 2 | 直近 1-2 ターンで mood が一気に動く |

profile が無いときは legacy default `halflife=5`。CLI `--halflife` 指定があれば
それが優先（test isolation 用）。出力 JSON に `halflife_source` フィールドが
含まれ、`"persona_profile" | "default" | "cli"` で由来が分かる。

**理論的根拠（出典の責務分離）**:

- **Bennett, Davidson & Niv 2021** (*Psychological Review* 129(3), 513–541)
  Eq. 6 — mood = leaky integral / EMA of "Advantage" estimate。**ただし
  Bennett 2021 は η_mood を fixed hyperparameter として扱っている**。
  shitsuji の base leaky integral 採用根拠はここまで。
- **Behrens et al. 2007** (*Nature Neuroscience* 10) — "Learning the value of
  information in an uncertain world" — **volatility-adaptive learning rate** の
  primary support。volatility が高い環境では learning rate を上げる Bayesian
  optimal solution を実証。
- **Piray & Daw 2024** (*Nature Communications*) — "Computational processes of
  simultaneous learning of stochasticity and volatility in humans" — 上記の
  最新拡張、stochasticity と volatility を分離した dual-rate model。

shitsuji の `halflife = 2 + 10 * (1 - volatility)` 式は Bennett の leaky
integral 構造を Behrens / Piray-Daw の volatility-adaptive learning rate 知見で
拡張したもの。式そのものは Bennett 2021 由来ではない。

### Unified VAD fusion (Phase 9g — episode decoupled)

`compute_mood.py` が以下 2 成分を**連続 VAD 空間で加算**して 1 つの unified vector
を出力する：

```
unified_vad = raw_ema  +  persona_shift
              ^            ^
              EMA history  warmth × 0.20
```

- **raw_ema**: 直近 N entry の confidence-weighted EMA（halflife = persona-derived）
- **persona_shift**: persona.warmth が valence baseline を [-0.20, +0.20] で shift（Wen 2024 ACM TOIS pattern）

各成分は出力 `vad_components` に breakdown して残るので、observability 確保。

#### Episode hint は VAD に fuse しない（Phase 9g 設計判断）

旧 Phase 9f は `episode_pull` を α=0.30 で `unified_vad` に additive fusion
していたが、Phase 9g で **削除した**。理由：

- **Forgas AIM (1995)**: 4 strategy のうち motivated processing は最低
  infusion 帯。reactive empathy = motivated processing と見なす演繹のもとで
  auto-fusion は AIM 違反。
- **Cognitive architecture 古典**（ALMA 2005 / Soar 2022 / ACT-R / PSI）:
  emotion と episodic memory は別モジュール、結合は appraisal を経由する
  間接的なものが dominant pattern (PMC8550857 review)。
- **Personalization Trap (Fang et al. 2025, arXiv:2510.09905)**: 静的
  persona/profile conditioning が 15 LLM の emotional reasoning を系統的に
  歪めることを実証。memory accumulation 経路への適用は shitsuji の
  inferential extension（同一の bias amplification 経路に乗ると推論）。
  論文自身は mitigation を提案していない。
- **UX 知見**: 強 memory 結合は Replika / ChatGPT memory に見られる
  "stalker / delusional attractor" 失敗モードを誘発（Pressman 2025; Cheong
  et al. 2025 Princeton CITP; Laestadius et al. 2024）。
- **Information theoretic / context contamination**: rate-distortion
  (Cover & Thomas 2006 Ch.10) と information bottleneck (Tishby 2000) の
  両方が量子化器の cascading で task-relevant information が単調漏出する
  ことを保証。Bayesian fusion 派 (Lu & Li 2025 DAM-LLM) 自身も context
  contamination を構造的弱点として認めている。

代わりに、`compute_mood.py` は episode lookup の結果を **separate top-level
field `episode_hint`** として host に渡す。これは informational sidecar で、
VAD 計算には影響しない。host Codex は user が過去文脈を **明示的に**
参照したときのみ hint を消費する。これにより：

- emotion derivation は AIM 整合的な "current frame + EMA inertia + persona"
  に純化
- 過去 episode は記憶層（read-only API）として独立し、責務分離が達成される
- `/clear` で context をリセットしても、過去 episode の VAD priming で
  汚染されない（emotion はあくまで AIM "direct access" 経路を保つ）

### Dyad cluster (analytics, NOT a tone driver)

直近 N entry の `dyad` 値（append.py が Plutchik 24-dyad テーブルから自動付与）
にヒストグラムを取り、最頻 dyad が 2 件以上あれば `dyad_cluster` として
mood JSON に surface する。**Phase 9f 以降、これは analytics 専用**（grep / pattern
分析）。直近の旧 family hint cascade（24→4 圧縮の Big-Five 罠）は撤去。

### Action tendency candidates (Phase 9f, Frijda 6-primitive)

unified VAD + persona expressive_range で `scripts/action_tendency.py` が 6 primitive
（approach / attend / inhibit / avoid / submit / aggress）から該当候補を short list する。
`expressive_range < 0.5` の persona は aggress / submit が候補から除外される。

最終 1 つを選ぶのは host（context 必要なため）。pipeline 中**唯一の deliberate
discretization step** で、ここに DPI loss が集約される設計。

### Composed advisory (1-sentence summary)

旧 4-fragment concat の tone_directive を撤去。`compute_mood.compose_advisory()` が
unified VAD + dyad cluster + persona + episode_hint_present flag から **1 sentence** を生成：

例: "対等な前向き・安定した mood | love cluster (×3) | 関連 episode hint あり (参照は host 判断) | persona=ギャル"

`episode_hint_present` のときは "priming" ではなく "hint あり" と表現する
（hint は read-only sidecar で VAD には fuse されないため）。

これは host への advisory（sanity check）。実際の reply 構築は host が writing-style
persona × tendency × intensity で行う。

## persona の振る舞いを変えたい場合

| やりたいこと | やり方 |
|---|---|
| AI のキャラ・口調を変える | `AGENTS.md` または `~/.codex/instructions.md` を編集 → 次セッションで profile が自動 re-derive される |
| AI の感情起伏を強制的に変える | `scripts/persona_profile.py --clear` で profile 削除 → 次セッションで再 derive。または直接 `<project>/.shitsuji/PERSONA_PROFILE.json` を編集 |
| 特定セッションだけ persona を切替 | `SHITSUJI_PERSONA_SOURCES` で参照元を切替 → SessionStart で source_hash 不一致を検出 → 自動 re-derive |
| persona profile の現状確認 | `scripts/persona_profile.py --show` |
| 感情ログをリセット | `rm <project>/.shitsuji/HISTORY.jsonl`（mood は neutral に戻る、profile は保持） |

## 環境変数

| 変数 | 効果 |
|------|------|
| `SHITSUJI_DATA_DIR` | HISTORY.jsonl + PERSONA_PROFILE.json の格納先を上書き（テスト隔離 / per-project 切替） |
| `SHITSUJI_PERSONA_SOURCES` | writing-style 検出元のパスを上書き（colon-separated）。テストや非標準レイアウト用 |
| その他は `references/operations.md` |

> 旧 `SHITSUJI_PERSONA_FILE` は撤去（persona.json 自体が消えた）。
> 新たに `PERSONA_PROFILE.json` が host 派生物として登場（writing-style から自動導出）。
