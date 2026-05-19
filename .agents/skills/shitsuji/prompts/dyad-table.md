# Plutchik dyad lookup

Plutchik wheel 上の 8 primary 感情の隣接距離に応じて、3 種類の合成感情
（dyad）が定義される。本表は shitsuji Skill の UserPromptSubmit hook が
rubric と同じ `additionalContext` ブロック末尾に同梱して host Codex に提供する。

`primary` と `secondary` が両方 set のとき、対応する dyad 名を rationale に
含めても良い（MAY、MUST ではない）。詳細運用は `vad-rubric.md` の "Dyad
annotation (optional)" セクション参照。

## Primary dyads (adjacent on wheel — 同強度の隣接ペア)

| primary + secondary       | dyad emotion    |
|---------------------------|-----------------|
| joy + trust               | love            |
| trust + fear              | submission      |
| fear + surprise           | awe             |
| surprise + sadness        | disapproval     |
| sadness + disgust         | remorse         |
| disgust + anger           | contempt        |
| anger + anticipation      | aggressiveness  |
| anticipation + joy        | optimism        |

## Secondary dyads (one apart — 1 つ飛ばし)

| primary + secondary       | dyad emotion    |
|---------------------------|-----------------|
| joy + fear                | guilt           |
| trust + surprise          | curiosity       |
| fear + sadness            | despair         |
| surprise + disgust        | unbelief        |
| sadness + anger           | envy            |
| disgust + anticipation    | cynicism        |
| anger + joy               | pride           |
| anticipation + trust      | hope            |

## Tertiary dyads (two apart — 2 つ飛ばし / opposite-but-one)

| primary + secondary       | dyad emotion    |
|---------------------------|-----------------|
| joy + surprise            | delight         |
| trust + sadness           | sentimentality  |
| fear + disgust            | shame           |
| surprise + anger          | outrage         |
| sadness + anticipation    | pessimism       |
| disgust + joy             | morbidness      |
| anger + trust             | dominance       |
| anticipation + fear       | anxiety         |

## Lookup rules

- **ペアは順序非依存**: `joy + trust` と `trust + joy` は同じ `love` を指す。
- **`primary == secondary`**: dyad は無し。`primary` 単独として扱う。
- **`primary` または `secondary` が `neutral`**: dyad は無し。中立は組合せ対象外。
- **直径上のペア (4 つ離れた組)**: **antithesis** であり dyad ではない。
  - `joy ↔ sadness`, `trust ↔ disgust`, `fear ↔ anger`, `surprise ↔ anticipation`
  - これらが同時に立つのは矛盾シグナル。`secondary` には設定すべきでない。
  - もし両者が近い距離で観測された場合は、より強い方を `primary`、もう一方を捨てて `secondary = null` とする。

## Antithesis warning (do not output as dyad)

| 出てはいけない組合せ | 意味 |
|----------------------|------|
| joy + sadness        | 矛盾（直径上） |
| trust + disgust      | 矛盾（直径上） |
| fear + anger         | 矛盾（直径上） |
| surprise + anticipation | 矛盾（直径上） |

これらが test_vad や rationale で見えたら、scoring を見直すこと。

## 設計判断: Plutchik antithesis vs continuous gradient

antithesis pair の reject ルールは **Plutchik (1980) postulate #8**「polar
opposite emotions are typically in conflict and rarely if ever experienced
simultaneously」に従う **Plutchik 原理主義** の選択。

これに対し **Cowen & Keltner (2017) PNAS** (n=853, 2185 videos) は 27
emotion category が "bridged by continuous gradients" であり、antithesis
pair 間の bittersweet/mixed-emotion state は smooth に経由可能であることを
実証。Barrett et al. (2018) *TiCS* は Cowen-Keltner への反論としてカテゴリ
の独立性を擁護しており、現代的にはこの 2 立場が併存する。

shitsuji は次の理由で **Plutchik categorical** 側を採用：

1. host Codex の VAD scoring rubric は label を必要とする — gradient だけ
   では tone directive に落とせない
2. `dyad cluster` analytics（pride 連発 vs shame 連発で family 判定）は
   discrete category 前提
3. mixed/bittersweet state は `primary + secondary` の dyad 機構で表現
   可能（adjacent / one-apart / two-apart）

将来 Cowen-Keltner gradient view へ移行する場合、(a) antithesis pair の同時
表現を許容、(b) 27-category space への拡張、(c) softmax 重みでの混合分布表現
が必要。それが必要になる前に、現状は Plutchik wheel の構造的制約を保持する。
