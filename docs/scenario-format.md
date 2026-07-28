# シナリオ構造化フォーマット仕様 (v1)

テストシナリオは `scenarios/*.yaml` に1シーン1ファイルで置く。スキーマは
[`scenarios/schema/scenario.schema.json`](../scenarios/schema/scenario.schema.json) が正で、本ドキュメントは意図の解説。

設計原則: **「生成に必要な情報を、どのモデルにも変換しやすい中立形式で持つ」**。
モデル固有の記法 (感情タグ、スタイルプロンプト構文など) はここには書かず、アダプタが変換する。
将来的には人間の走り書きト書きをLLMでこの形式に整理・補完するフローを想定 (M6)。

## 構造

```yaml
format_version: 1
id: tavern-night            # ケバブケース、ファイル名と一致させる
title: 酒場・夜の喧騒
locale: ja
tags: [fantasy, indoor, crowd]

scene:
  setting: >-               # 場所・時間帯・状況の散文。スタイルプロンプト対応モデルへの文脈
    中世ファンタジー都市の酒場。夜、満席で騒がしい。
  acoustics: 木造の中規模ホール。喧騒と食器の音が常にある。
  listener: プレイヤーは席の間を通り抜ける。声は2〜5m の中距離で聞こえる。

characters:
  - id: barmaid
    name: 給仕の女性
    kind: human             # human / machine / creature / spirit (任意、既定値: human)
    gender: female          # 聞こえ方: female / male / neutral
    age: young_adult        # 聞こえ方: child / teen / young_adult / adult / middle_aged / elderly
    archetype: 給仕
    voice: 明るく張りのある声。やや早口で、喧騒を通す接客の声。
    personality: 気さくで世話焼き。常連には遠慮がない。
    reference_voice: null   # assets/voices/metadata.yaml の素材ID (クローン系モデル用、任意)

lines:
  - id: barmaid-001         # シーン内一意。グローバルIDは <scenario>/<line> で合成
    character: barmaid
    text: はいよっ、エール二つお待ち！
    reading: null           # かな読み (漢字誤読対策、任意)
    emotion: cheerful       # 下記enum
    intensity: 2            # 1=控えめ 2=普通 3=強い
    delivery: 客へ呼びかける。喧騒に負けないやや大きめの声。語尾が弾む。
    situation: 両手にジョッキを持ってテーブルへ運んでいる。
    difficulty: standard    # standard / hard (TTSに難しい要素を含む行)
    loop_ok: true           # ガヤループ素材として繰返し利用可か
```

## フィールドの意図

| フィールド | 用途 |
| --- | --- |
| `scene.setting / acoustics / listener` | スタイルプロンプト対応モデルに渡す文脈。サイトのシーン説明にも使用 |
| `character.kind` | キャラクター種別。`human / machine / creature / spirit`。任意で、省略時は `human` として扱う |
| `character.gender / age` | 生物学的属性ではなく、音声から受ける声質・演技上の印象。非人間キャラクターにも適用する |
| `character.voice` | **声質のテキスト記述**。声質プロンプト対応モデルにはそのまま、非対応モデルには話者選択のヒントとして使う |
| `character.reference_voice` | ゼロショットクローン系モデルへの参照音声。`assets/voices/metadata.yaml` に登録された権利確認済み素材ID（`^[a-z0-9]+(?:-[a-z0-9]+)*$`）のみ許可 |
| `line.emotion` + `intensity` | 機械可読な感情ラベル。感情タグ対応モデルへ直接マップ、フィルタUIにも使用 |
| `line.delivery` | 演技指示の散文。instruct系モデル (自然文で演技指示できるもの) へ渡す |
| `line.reading` | 漢字の誤読対策。読み指定対応モデ用。非対応モデルには使わない (誤読も品質差として観測する) |
| `line.difficulty` | `hard` = 方言・叫び・笑い混じり・囁き・フィラー・非言語音などTTSの苦手要素を含む行。集計分析用 |

`辛い / 行った / 人気 / 大分` のように文脈で読みが変わる語を含む場合、
`line.reading` を省略すると `gaya validate` が warning を出す。自動 G2P
の推定を正解として採用せず、発話意図に合う全文のかな読みを明記する。

## emotion enum

`neutral / cheerful / angry / sad / fearful / surprised / tired / drunk / whisper / shout / laughing / pain`

enumはフィルタと機械マッピングのための粗い分類。ニュアンスは `delivery` の散文で表現する。

## 執筆ガイドライン (ガヤらしさ)

- **一言で完結**: 5〜30文字目安。文脈がなくても状況が伝わる
- 固有名詞・クエスト依存の情報を入れない (どのゲームでも使い回せる汎用性)
- 話し言葉として自然に。書き言葉的な説明口調はNG
- シーンごとに `hard` 行を最低2本入れる (叫び・笑い・囁き・方言風・フィラー入りなど)
- 同一キャラに感情の異なる行を持たせ、モデルの演じ分け幅を観測できるようにする

カバレッジ計画・シーン一覧は [content-plan.md](content-plan.md) を参照。
