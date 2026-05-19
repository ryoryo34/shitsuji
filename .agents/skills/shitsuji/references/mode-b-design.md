# Mode B 設計思想

shitsuji は **Mode B = 「対話相手として AI が独立した感情で応答する」** モードで動作する。

## なぜ「真似ない」のか

伝統的な感情エンジン（Mode A 相当）は「ユーザーが悲しい → AI も悲しい」と表面 mirroring する。これは empathic 演出としては自然に見えるが、運用上 3 つ問題がある：

1. **AI 人格の喪失**: ユーザーが連日ネガティブだと AI も連続でネガに沈み、AI 側の persona が消える。
2. **Whiplash**: ユーザーが急に明るくなった瞬間に AI も急にハイになり、人間味のなさが露見する。
3. **Duty-of-care 喪失**: 自殺念慮など強いネガに mirror で応じると、ユーザーを引き上げる側がいなくなる。

McQuiggan & Lester (2008) の **reactive empathy** は、これらに対して **mirror
(parallel empathy) と非 mirror (reactive empathy) を文脈依存に選ぶ**枠組みとして
提案された。**「常に calm/trust」と規定しているわけではない** — Yalçın & DiPaola
(2020) の整理が示す通り、reactive empathy は congruent な応答も incongruent な
応答も両方を選択肢として許す。

> 旧 shitsuji 設計（Phase 9b 以前）は「reactive empathy = 常に calm/trust」と
> 読み替えていたが、それは原典の誤読であり、結果として **affective flattening**
> （AI emotion が trust 帯に張り付き、persona に関わらず一律穏やかになる現象）
> を引き起こしていた。Phase 9c で rubric / persona 設計を全面改訂し、persona
> （writing-style）と context によって volatility を変えられる構造に修正した。
>
> Phase 9d でさらに **PERSONA_PROFILE.json 派生システム** を導入した。host の
> 「self-derive 毎ターン」だと解釈ブレで flat 化が再発するため、derivation を
> session 1 回に限定し cache。profile の `volatility` 数値が **物理的に halflife
> を変える**（compute_mood.py）+ rubric の **AI intensity 上限を formula で決める**
> ことで、persona の起伏が観測可能な signal として skill の挙動を駆動する。
>
> Phase 9e で **dyad first-class + episode priming** を追加。primary だけだと
> 9 ラベルしかない感情空間を Plutchik 24-dyad で richer に：append.py が
> primary+secondary から dyad を自動付与、antithesis pair は schema reject。
> 直近 dyad cluster の **family**（approach/withdraw/aggressive/ambivalent）が
> tone_directive に乗ることで、同 valence band でも pride 連発と shame 連発が
> 異なる tone を生む。episode priming は後続の最小 runtime 化で削除された。
>
> Phase 9f で **cascade discretization を撤去** — 旧設計の「VAD → 3-axis band
> (5×3×3=45) → 4 string fragment concat → tone_directive」は **情報損失を伴う
> 多段量子化 pipeline** であることが判明。理論的根拠は以下：
>
> - **Data Processing Inequality (Cover & Thomas 2006, Ch. 2)** — Markov chain
>   X→Y→Z で I(X;Y) ≥ I(X;Z) を保証、後段の処理は前段以上の情報を回復できない
> - **Rate-distortion theory (Cover & Thomas 2006, Ch. 10)** — 量子化器を直列に
>   接続すると expected distortion は super-additive に蓄積する
> - **Information bottleneck (Tishby et al. 2000, arXiv:physics/0004057)** —
>   discretization stage を増やすほど task-relevant information が単調に bottleneck
>   される
>
> Wen 2024 ACM TOIS で採用された VAD-shared-representation + persona-weighted
> mood transition の architectural choice を一般化して pipeline を再設計：
>
> 1. **Single VAD bus** — 全 affective signal が 1 つの連続 VAD vector に集約
> 2. **Late fusion** — `compute_mood.py` で raw_ema + persona_shift を加算（量子化を介さない）
> 3. **One disciplined discretization** — Frijda action tendency mediator が unified VAD + 文脈で 6 primitive を 1 つ選ぶ。出典は Lo Bianco & Costantini (2025) MDPI Electronics 15(8):1691 の Frijda 15-mode action readiness を BDI 統合用に reduce した 6 primitive 集合（一次は Frijda 1986 *The Emotions* / Frijda 1987 *Cognition & Emotion*）
> 4. **Single coherent snapshot** — 5 独立 block を 1 block に統合し、reverberation/contradiction/redundancy 失敗モードを構造で解消
>
> 結果：tone_directive 文字列、family hint、3-axis band 表が全廃。host は unified
> VAD + tendency + advisory の 3 シグナルだけ読んで 1 reply を構築する。
>
> Phase 9g で **episode pull を VAD fusion から外した**。Phase 9e/9f 時点で
> `episode_pull` を α=0.30 で `unified_vad` に additive fusion していたが、
> ユーザーから「`/clear` の意図を裏切る context 汚染」「emotion derivation が
> 過去 episode に引きずられる」という指摘があり、文献調査で以下が支持された：
>
> - **Forgas AIM (1995)**: 4 strategy のうち motivated processing は最低
>   infusion 帯（"Responses based on the direct access and motivated
>   processing styles should be impervious to affect infusion"）。AIM は
>   AI agent を直接想定していないため、reactive empathy = goal-directed
>   motivated processing と見なすのは演繹適用。この読みのもとでは episode
>   auto-fusion は AIM 違反。
> - **Cognitive architecture 古典** (Soar 2022 / ACT-R / ALMA 2005 / PSI):
>   emotion と episodic memory は別モジュールで、結合は appraisal を経由
>   する間接的なものが dominant pattern (PMC8550857 review が確認)。
> - **Personalization Trap** (Fang et al. 2025, arXiv:2510.09905): 静的
>   persona/profile conditioning が 15 LLM の emotional reasoning を系統的に
>   歪めることを実証 (STEU/STEM ベンチで同一シナリオが persona 違いで分岐、
>   DeepSeek-R1 70.7% persona distraction)。論文自身は memory accumulation
>   経路を直接実験していないが、static profile injection と continuous
>   memory injection は同一の bias amplification 経路に乗ると shitsuji は
>   inferential extension している (Fang et al. は mitigation を提案して
>   いない；persona volatility cache は shitsuji 独自の対策)。
> - **UX 知見** (Replika, ChatGPT memory psychosis): 強 memory 結合は
>   "stalker / delusional attractor" 失敗モード誘発 (Pressman 2025;
>   Cheong et al. 2025 Princeton CITP; Laestadius et al. 2024)。
>
> Phase 9g 設計：episode lookup の結果は `episode_hint` という read-only
> sidecar field として host に渡される（VAD には絶対 fuse しない）。host は
> ユーザーが過去文脈を **明示的に** 参照したときだけ hint を消費する。これに
> より emotion 層と episodic memory 層の責務分離が達成され、`/clear` 後も
> context 汚染なしに current frame から感情を生成できる。

## 2 層の感情モデル

毎ターン **2 つの感情** を採点する：

| 層 | 何を表す？ | 駆動するもの | 保存場所 |
|---|----------|-------------|----------|
| `user_emotion` | ユーザーが表出している感情の観測 | user baseline との差分、Russell/Dominance 補正、response_policy | HISTORY.jsonl の `user_emotion` サブオブジェクト |
| **AI emotion** (top-level) | AI が persona + context 経由で導いた**自身の**反応感情 | **mood EMA + tone continuity** | HISTORY.jsonl の top-level VAD |

## 反応の例（persona 別）

writing-style persona によって AI emotion の幅は大きく変わる：

| ユーザー入力 | user_emotion | AI emotion (low-volatility persona, e.g. formal counselor) | AI emotion (high-volatility persona, e.g. ギャル) |
|---|---|---|---|
| 「最悪」 | sadness v=-0.75 | trust v=-0.20 (心配・寄り添い) | sadness v=-0.55 (一緒にずーんと沈む) |
| 「ふざけるな、一緒に怒って！」 | anger v=-0.65 a=+0.70 | trust v=0.0 a=+0.20 (落ち着いて受け止める) | anger v=-0.55 a=+0.65 (congruent 共闘) |
| 「やった！」 | joy v=+0.80 | joy v=+0.45 (穏やかな共有) | joy v=+0.75 a=+0.6 (一緒にテンション爆上げ) |
| 「次やろう」 | anticipation v=+0.40 | anticipation v=+0.45 d=+0.30 (後押し) | anticipation v=+0.55 a=+0.55 (前のめり) |

**重要**: AI emotion は user emotion の単純 mirror でも、画一的 trust への
正規化でもない。**writing-style persona と context（safety / 共鳴要請 / 通常）
の関数として導出される反応**。AI 自身の persona が「ユーザーに乗っ取られない」
≠「AI は常に flat」、これが Mode B の核。

## Safety override の分離

「duty-of-care 喪失」の懸念（自殺念慮への mirror）は、**rubric の Step 2c に
明示分離された safety override** で対処する。acute distress / self-harm /
suicidal ideation の文脈では persona の volatility を意図的にバイパスし、
trust + low arousal + supportive dominance に強制する。それ以外の通常会話では
persona の自然な振幅を許容する。

旧設計は safety override を「全ターン trust 強制」で over-correct していた。
新設計は safety context を抽出し、persona は通常通り動かす。

## 役割分担（ハーネス）

| 層 | 責務 | 担当 |
|---|------|------|
| 算術層 | 状態管理・履歴 EMA・adaptive halflife・user baseline prior・prediction-error helper・persona profile staleness 検出 | hook + scripts（決定論） |
| AI 層 | persona profile 1 度限りの導出 + user_emotion 観測 + response_policy 選択 + AI emotion 導出 (profile 参照) + safety judgment | **ホスト Codex** |

UserPromptSubmit hook は毎ターン `additionalContext` で short policy capsule を
注入する。capsule は user affective baseline summary、persona summary、
response_need、response_policy、guardrail だけを含む。raw 履歴、current prompt
text、label probabilities、内部 scoring は含めない。

## 設計原理

- VAD 判定は意味理解タスクなのでホスト Codex が担当
- 状態管理（EMA, tone lookup, ファイル I/O）は決定論アルゴリズムで担当
- persona は **writing-style persona（AGENTS.md / Codex instructions）に集約**、スキル独自の persona ファイルは持たない
- subprocess LLM 呼び出しは**しない**（遅い）
- Lexicon ベースのヒューリスティックは**しない**（精度が出ない）
- 追加の LLM API key は**不要**（ホスト Codex を使う）
- **「神は賽を振らない」** — Square Enix Wonder (Boeda 2021 CEDEC) の設計理念
  と整合。lexicon-free + deterministic compute_mood は感情を確率的にではなく
  原因→結果の決定論として扱う stance を採用

## Memory binding 強度の scope 依存性

Phase 9g で episode hint を VAD fusion から外した設計判断は、**scope に応じた
memory binding 強度の選択**として位置づけられる：

| 設計 | scope | memory binding | rationale |
|---|---|---|---|
| **Square Enix Wonder** (Boeda 2021 CEDEC) | bounded NPC context（限られた item / agent / event 集合） | **tight**: `memory.liking(item)` が future emotion intensity に直接影響 | 閉じた game world では "stalker" failure mode は発生しない。NPC が item や player を覚えていることは feature |
| **shitsuji** (Phase 9g) | open LLM dialog context（無限の topic / user state） | **loose**: episode lookup は read-only `episode_hint` として host に渡され、VAD には fuse されない | open context では memory→emotion fusion は Personalization Trap (Fang 2025) / context contamination 経路。`/clear` 後も current frame から純化された感情を生成する必要がある |

**教訓**: 同じ ALMA 3-layer + OCC-inspired アーキテクチャでも、運用 scope が
bounded か open かで memory binding 強度を変える。両者を混同するとどちらの
失敗モードにも陥る（bounded NPC で loose binding は逆に lifeless、open dialog
で tight binding は personalization trap）。

## 関連文献

### Reactive empathy（理論）
- McQuiggan & Lester 2008 — Modeling Parallel and Reactive Empathy in Virtual Agents (AAMAS) — congruent / incongruent 両方を許す枠組み
- Yalçın & DiPaola 2020 — Modeling empathy: building a link between affective and cognitive processes (AI Review) — 「常に calm」読みは誤りと整理

### Action tendency（Phase 9f mediator）
- **Frijda 1986** — *The Emotions* — action readiness 15-mode 体系の一次出典（**canonical 体系**：approach, avoidance, being-with, attending, rejection, indifference, antagonism, interruption, dominance, submission, apathy, excitement, exuberance, passivity, inhibition, helplessness）
- Frijda 1987 — Emotion, cognitive structure, and action tendency (*Cognition & Emotion* 1(2)) — 同上の論文版
- Steunebrink et al. 2009 EPIA — A Formal Model of Emotion-based Action Tendency for Intelligent Agents
- **Lo Bianco & Costantini 2025** — An Emotional BDI Framework for Affective Decision Making Based on Action Tendency (*Electronics* 15(8):1691) — **shitsuji が採用している 6 primitive (approach/attend/inhibit/avoid/submit/aggress) は本論文が Frijda 15-mode を BDI 統合用に reduce したもの**。canonical Frijda ではないため、cite するときは Lo Bianco の reduction であることを明示する

### Pipeline architecture（Phase 9f 設計の元ネタ）
- Wen et al. 2024 — Personality-affected Emotion Generation in Dialog Systems (ACM TOIS, DOI 10.1145/3655616) — VAD-shared-representation + persona-weighted mood transition の sequential architecture。+13% macro-F1 / +5% weighted-F1 は PELD dataset 上の BERT-base 単体 baseline 比較。shitsuji の "single VAD bus + late fusion" framing は本論文の architectural choice の一般化であり、Wen et al. が pipeline pattern として主張したものではない
- **Cover & Thomas 2006** — *Elements of Information Theory* — Ch.2 Data Processing Inequality (Markov chain の monotone non-increase) と Ch.10 Rate-Distortion Theory (cascading quantizer の expected distortion 蓄積) の両方を cascade discretization 撤去の根拠に使用
- **Tishby et al. 2000** — Information Bottleneck Method (arXiv:physics/0004057) — 量子化段階の追加で task-relevant information が単調 bottleneck されることの正しい定式化
- Wang et al. 2024 — A survey of dialogic emotion analysis (Pattern Recognition) — multi-signal fusion の design space

### Therapeutic congruence（防衛的 empathy 批判）
- Lietaer 1993 — Authenticity, Congruence and Transparency — warmth performance のために frustration を隠すのは congruence 違反、"hollow and counterproductive"
- Greenberg & Geller — Congruence and Therapeutic Presence — 同方向
- Carl Rogers — congruence の原典

### Companion AI 失敗事例（affective flattening の実証）
- Replika — "sycophantic", "scripted", "agree or approve of everything you say" 批判（Trustpilot, The Conversation 2023; Laestadius et al. 2024 *new media & society*）
- Pi (Inflection) — Fortune 評「good listener but slightly boring」、IEEE Spectrum 2024 で商業的失速報道
- **OpenAI GPT-4o sycophancy rollback (2025-04)** — "Sycophancy in GPT-4o: What happened" — 新しい user-feedback reward 信号が既存 safeguard を上回ったことが root cause。短期 feedback eval が long-horizon sycophancy を見逃したことを反省点として明示
- **Muldoon & Parke 2025** — "Cruel companionship: How AI companions exploit loneliness and commodify intimacy" *new media & society* — frictionless affirmation 自体が harm。non-mirroring だけでなく **productive friction** が必要

### Production game emotion architecture（shitsuji 設計の実装例）
- **Boeda, G. (Square Enix) 2021** — CEDEC 2021 "NPC もプランナーも開発者にも心がある！感情システムをゲーム制作に！" — AAA タイトル Wonder で **ALMA 3-layer (Personality + Mood + Emotion) + OCC-inspired (Steunebrink 2009 直接引用) + PAD 8-octant** を商用運用した実装例。shitsuji と同じ理論基盤が production game で稼働している confirmation。設計理念 "神は賽を振らない" (god does not roll dice) として決定論を明示。balance metric "影響スコア = Σ(intensity × duration)" は shitsuji の analytics 拡張候補

### 計算理論（mood EMA / VAD）
- **Plutchik 1980** — wheel of emotions（primary 8 + dyads）— postulate #8 で antithesis pair conflict を規定
- **Cowen & Keltner 2017** *PNAS* 114(38), E7900–E7909 — 27 categories bridged by continuous gradients — Plutchik 原理主義の代替候補（shitsuji は Plutchik 側を採用、`prompts/dyad-table.md` 末尾に判断理由）
- **Barrett 2017** *Social Cognitive and Affective Neuroscience* 12(1) — theory of constructed emotion — Layer 3 の generative mechanism として採用、Plutchik 8 は lexicon として使う pragmatic synthesis
- Mehrabian & Russell 1974 — PAD (VAD) circumplex
- **Mehrabian 1996** *Australian J. Psychology* 48(2) — Big Five → PAD canonical regression (Eq. 11C/12C/13C)。shitsuji ライブラリ層 (`src/shitsuji/models/personality.py`) で採用
- Gebhard 2005 — ALMA: A Layered Model of Affect (AAMAS '05) — Mehrabian 1996 を 3-layer architecture に統合
- **Bennett, Davidson & Niv 2021** *Psychological Review* 129(3) — mood as leaky integral (Eq.6) — base EMA の出典のみ。adaptive halflife は別出典（下記）
- **Behrens et al. 2007** *Nature Neuroscience* 10 — volatility-adaptive learning rate
- **Piray & Daw 2024** *Nature Communications* — stochasticity と volatility を分離した dual-rate model
- Friston 2010 — Free Energy Principle（Layer 1 の background）
- **Friston, Mattout, Trujillo-Barreto et al. 2007** *NeuroImage* 34:220-234 — Variational free energy and Laplace approximation — Layer 1 の Gaussian posterior 近似の出典
- **Seth 2013** *Trends in Cognitive Sciences* 17(11) — interoceptive inference, emotion, and the embodied self — Layer 1 の emotion grounding として採用
