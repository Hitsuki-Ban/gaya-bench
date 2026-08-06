# -*- coding: utf-8 -*-
"""モデル別調理レシピ (A4×1枚×9モデル) を docs/recipes/ 用に生成する。"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("JP", r"C:\Windows\Fonts\meiryo.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("JPB", r"C:\Windows\Fonts\meiryob.ttc", subfontIndex=0))

ACCENT = colors.HexColor("#b45309")
INK = colors.HexColor("#292524")
SUB = colors.HexColor("#78716c")
LINE = colors.HexColor("#e7e5e4")
DARK = colors.HexColor("#1c1917")
METHOD_COLORS = {"プリセット": "#0369a1", "クローン": "#15803d", "テキスト指示": "#b45309"}

body = ParagraphStyle("body", fontName="JP", fontSize=9.6, leading=14.4,
                      textColor=INK, spaceAfter=3, wordWrap="CJK")
small = ParagraphStyle("small", parent=body, fontSize=8, leading=11, textColor=SUB)
h1 = ParagraphStyle("h1", fontName="JPB", fontSize=18, leading=22,
                    textColor=colors.white, wordWrap="CJK")
h1sub = ParagraphStyle("h1sub", fontName="JP", fontSize=8.8, leading=12,
                       textColor=colors.HexColor("#fcd34d"), wordWrap="CJK")
h2 = ParagraphStyle("h2", fontName="JPB", fontSize=11.8, leading=15, textColor=INK,
                    wordWrap="CJK")
li = ParagraphStyle("li", parent=body, leftIndent=10, firstLineIndent=-10,
                    spaceAfter=3.5)
code = ParagraphStyle("code", fontName="JP", fontSize=8.4, leading=12.2,
                      textColor=colors.HexColor("#e7e5e4"), wordWrap="CJK")

def P(t, s=body):
    return Paragraph(t, s)

def LI(t):
    return Paragraph(f"<font color='#b45309'>◆</font> {t}", li)

def section(num, title, note=None):
    numcell = P(f"<font color='white'><b>{num}</b></font>", ParagraphStyle(
        "secnum", fontName="JPB", fontSize=10.5, leading=13, alignment=1))
    row = [numcell, P(f"<b>{title}</b>" + (f"　<font size=7.6 color='#78716c'>{note}</font>" if note else ""), h2)]
    t = Table([row], colWidths=[7.5*mm, 172.5*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), ACCENT),
        ("LINEBELOW", (1, 0), (1, 0), 1.3, ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (1, 0), (1, 0), 5),
    ]))
    return [Spacer(1, 9), t, Spacer(1, 4.5)]

def codeblock(lines):
    tt = Table([[P(t, code)] for t in lines], colWidths=[180*mm])
    tt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (0, 0), 4),
        ("BOTTOMPADDING", (0, -1), (0, -1), 4),
    ]))
    return tt

Y = "<font color='#fbbf24'>"  # key color
G = "<font color='#a8a29e'>"  # comment color
E = "</font>"

COMMON_TRY = lambda extra, mid: [
    f"{G}# リポジトリ取得〜1本試し生成 (Windows / PowerShell){E}",
    f"git clone git@github.com:Hitsuki-Ban/gaya-bench.git; cd gaya-bench",
    f"uv sync --project pipeline --locked{(' --extra ' + extra) if extra else ''}",
    f"uv run --project pipeline --locked{(' --extra ' + extra) if extra else ''} gaya gen `",
    f"&nbsp;&nbsp;--model {mid} --scenario tavern-night --line barmaid-001 --takes 1 --seed-base 42",
    f"{G}# → artifacts/takes/&lt;run-id&gt;/audio/ にWAV+Opus。重みは初回に固定版が自動DLされる{E}",
]

MODELS = [
    dict(
        id="aivisspeech-kohaku", name="AivisSpeech コハク", method="プリセット",
        tagline="日本語アクセント最安定・読みを直接指定できる唯一のモデル。多役の書き分けは不可",
        stats=[("VRAM", "Engine側で軽量"), ("速度", "高速 (Engine処理)"), ("ライセンス", "ACML-1.0")],
        setup=[
            "<b>AivisSpeech Engine</b> (公式アプリ) をインストールし起動 — pipelineは <b>http://127.0.0.1:10101</b> のHTTP APIを呼ぶ (GPU直載せ不要)",
            "話者モデル「コハク」(AIVMX 1.1.0) を AivisHub からEngineへ追加",
            "追加のPython extraは不要 (基本依存のみで動く)",
        ],
        try_extra=None,
        inputs=[
            "<b>話者とスタイルの選択が入力のすべて</b>。スタイル (ノーマル/あまあま/せつなめ/ねむたい) を感情へマッピングして使う (例: 悲しみ・痛み→せつなめ)",
            "<b>読み・アクセントは accent_phrases で直接指定可能</b> — 全モデル中で唯一の完全アクセント制御。誤読・平板化はここで確実に直せる",
        ],
        tuning=[
            "強度は <b>intonation_scale</b> (0.8/1.0/1.2) と <b>tempo_dynamics_scale</b> で付ける。上げすぎると不自然",
            "声が1話者しかないため、多役は「スタイル×パラメータ差」で疑似的に書き分けるか、他モデルと併用する",
            "決定論的 (同入力=同出力) なので数打ち不要。品質はほぼ入力設計で決まる",
        ],
        record=[
            "公開161/161行。決定論的で同入力なら同一出力 — 差し替え・再現が最も容易",
            "明示読みが必要な25行はaccent_phrases経由で適用済み。読み起因のカタコト化なし",
        ],
    ),
    dict(
        id="supertonic-3", name="Supertonic 3", method="プリセット",
        tagline="ONNXで動く超高速モデル。量産・リアルタイム向き、表現の幅は狭い",
        stats=[("VRAM", "軽量 (ONNX)"), ("速度", "最速クラス"), ("弱点", "日本語が早口")],
        setup=[
            "extra <b>supertonic3</b> を同期 (onnxruntime含む)。重み (Supertone/supertonic-3) は初回自動DL",
            "CPUでも動くがGPU providerがあれば更に高速",
        ],
        try_extra="supertonic3",
        inputs=[
            "<b>voice_style (プリセット声) の選択</b> + セリフ。expression_tags で簡単な表現付けが可能",
            "読み仮名指定に対応 — 誤読はかな指定で修正 (曖昧語のみ、全文かな化はしない)",
        ],
        tuning=[
            "<b>speed</b> (0.7〜2.0、既定1.05) — ただし日本語の早口感はモデル側の癖で、1.0にしても約5%しか変わらない (実測検証済み)。ゆっくり聞かせたい台詞は0.9前後を試す",
            "total_steps 8 が既定。上げても品質向上は限定的で速度メリットが消える",
            "決定論的なので数打ち不要。合わない台詞は諦めて他モデルへ回すのが早い",
        ],
        record=[
            "公開161/161行。話速の速さはadapter設定ではなくモデル側性質と切り分け済み (公式SDK・issueまで確認)",
            "生成が最速のため、台詞の当たり外れ確認の「最初の一周」に向く",
        ],
    ),
    dict(
        id="chatterbox-multilingual-v3", name="Chatterbox Multilingual V3", method="クローン",
        tagline="感情の誇張度を数値制御できるクローン型。日本語では荒れやすく選抜前提",
        stats=[("VRAM", "実測ピーク 4.0GB"), ("速度", "中速"), ("透かし", "PerTh自動埋込")],
        setup=[
            "extra <b>chatterbox</b> を同期。重み (ResembleAI/chatterbox 固定版) は初回自動DL",
            "見本音声 (5〜10秒WAV) を用意 — assets/voices/README.md の公開素材入手手順を参照",
        ],
        try_extra="chatterbox",
        inputs=[
            "<b>見本音声が声質のすべて</b>。雑音なし・目的の演技トーンに近い5〜10秒を役ごとに1つ固定",
            "<b>language_id=ja を必ず明示</b> (多言語モデルのため落ちると英語訛りになる)",
        ],
        tuning=[
            "<b>exaggeration</b> (感情誇張、強度1〜3→0.4/0.6/0.8目安) と <b>cfg_weight</b> (0.5) が主ノブ。上げるほど演技は派手だが崩壊率も上がる",
            "日本語では<b>声質崩壊・棒読み化・誤読が一定率で発生</b> (実測)。N=4以上の数打ち+音声認識チェックで弾く運用が必須",
            "min_p / repetition_penalty は既定のままが安定。悪化時はまず見本を疑う",
        ],
        record=[
            "公開161/161行 (自動選抜)。日本語での声線崩壊・誤読は別途診断継続中 — 公開版にも品質注記バッジあり",
            "見本はハドウ/Lux等の公開5素材を使用。同一見本でも台詞により崩れ方が変わる",
        ],
    ),
    dict(
        id="cosyvoice3-0.5b-2512", name="CosyVoice 3 (0.5B)", method="クローン",
        tagline="指示文と読み指定を併用できるクローン型。平均点が安定、ただし非決定",
        stats=[("VRAM", "実測ピーク 5.3GB"), ("速度", "中速"), ("性質", "同条件でも毎回変わる")],
        setup=[
            "extra <b>cosyvoice3</b> を同期 (Matcha-TTS同梱)。重み (Fun-CosyVoice3-0.5B-2512) は初回自動DL",
            "見本音声 (5〜10秒WAV) を用意 — assets/voices/README.md 参照",
        ],
        try_extra="cosyvoice3",
        inputs=[
            "<b>見本音声 + 指示文 (instruction) の二段構え</b>。指示文は「怒って強く」程度の短い日本語で感情・話し方を足す",
            "<b>読み仮名の明示指定に対応</b> — 誤読しやすい語だけかな指定 (全文かな化はアクセント崩れの元)",
        ],
        tuning=[
            "<b>seedを固定しても出力が毎回変わる (非決定)</b> — 再現性は期待せず、N=4前後の数打ちから選抜する前提で回す",
            "speed パラメータあり。fp16で十分な品質",
            "指示文を長くしても効果は頭打ち。感情は「指示文+見本のトーン」の組合せで作る方が効く",
        ],
        record=[
            "公開161/161行。初回ランの生成失敗2行は補録で解消",
            "同一seed・同一入力で出力が変わる非決定性を実測で確認済み (順序汚染ではない)",
        ],
    ),
    dict(
        id="gpt-sovits-v2-pro-plus", name="GPT-SoVITS v2ProPlus", method="クローン",
        tagline="数分の音声で追加学習できる唯一の選択肢。自社収録と組む正式起用の本命",
        stats=[("VRAM", "実測ピーク 1.8GB"), ("速度", "中速"), ("強み", "few-shot追加学習")],
        setup=[
            "extra <b>gpt-sovits</b> を同期。事前学習重み (lj1995/GPT-SoVITS 固定版) は初回自動DL",
            "見本音声を用意: <b>5秒前後のクリップ + その書き起こし (prompt_text)</b> のペアが必要",
        ],
        try_extra="gpt-sovits",
        inputs=[
            "<b>見本の切り出し品質が最重要</b>: 3〜10秒・先頭末尾の無音を除去・1文で完結・書き起こしと完全一致。ここが雑だと全出力が濁る",
            "感情の専用入力はない — 感情は<b>見本自体の演技トーン</b>で与える (感情別の見本を役ごとに揃えると強い)",
        ],
        tuning=[
            "<b>temperature / top_k / top_p</b> が主ノブ。棒読み気味なら temperature を少し上げる",
            "<b>正式起用では few-shot 追加学習が本命</b>: 契約声優の数分〜数十分の収録で専用モデル化でき、ゼロショットより一貫性が大きく向上する",
            "text_split_method は短いガヤ台詞ならそのまま。VRAM 2GB弱で最軽量クラス — 並行運用しやすい",
        ],
        record=[
            "公開161/161行。VRAM 2GB弱で全モデル中最軽量 — 他モデルと並行運用しやすい",
            "見本の書き起こし (prompt_text) の精度が出力の明瞭さに直結することを確認",
        ],
    ),
    dict(
        id="irodori-tts-600m-v3-voicedesign", name="Irodori-TTS v3 (600M VoiceDesign)", method="テキスト指示",
        tagline="日本語特化のテキスト指示型。声質の文章指定がよく効くが、指示文設計に規律が要る",
        stats=[("VRAM", "実測ピーク 6.0GB"), ("速度", "中速"), ("透かし", "SilentCipher自動埋込")],
        setup=[
            "extra <b>irodori</b> を同期。重み (Aratako/Irodori-TTS-600M-v3-VoiceDesign) + 日本語コーデックは初回自動DL",
        ],
        try_extra="irodori",
        inputs=[
            "<b>声質caption</b>: 名前・種別・性別・年齢・役柄・声質を必須情報として短く列挙。<b>形容を盛った長文は禁止</b> — 男性役が女性声化する実測根因",
            "<b>役ごとに一度生成した声をアンカー (自作見本) として固定</b>し、全セリフで参照する。感情はcaption内の感情行+絵文字 (emotion_emoji) で台詞ごとに変える",
            "読み仮名指定に対応。誤読語のみ最小指定",
        ],
        tuning=[
            "<b>cfg / num_steps</b> が品質ノブ。stepsを下げると速いが子音が甘くなる",
            "<b>演技過剰・母音伸びの傾向</b> (実測)。落ち着いた台詞はcaptionの感情強度を一段下げると自然",
            "seed固定で決定論的 — 数打ちはseed違いで。崩れ役はcaption短縮→アンカー再生成の順で直す",
        ],
        record=[
            "公開161/161行。当初は長captionで男性27役中10役が女性声化 → <b>caption短縮で解決</b> (男性役median F0が180Hz超→110〜155Hzへ)",
            "演技過剰・母音伸びの傾向は聴取で確認。global設定変更はブラインド比較で棄却し、現行captionを維持",
        ],
    ),
    dict(
        id="irodori-tts-v4-small", name="Irodori-TTS v4-Small (766M)", method="テキスト指示",
        tagline="caption生成とクローンの統一モデル。v3の男性声問題が改善、見本対応が最強 (計120秒)",
        stats=[("VRAM", "実測ピーク 4.0GB"), ("速度", "実測RTF 0.60"), ("透かし", "SilentCipher自動埋込")],
        setup=[
            "extra <b>irodori-v4</b> を同期。重み (Aratako/Irodori-TTS-v4-Small) + コーデックは初回自動DL (SHA検証つき)",
            "<b>FFmpeg shared build が必須</b> (torchcodecが要求): winget で Gyan.FFmpeg.Shared を入れ、bin を PATH へ (pipeline/README.md 記載)",
        ],
        try_extra="irodori-v4",
        inputs=[
            "<b>caption はv3と同じ規律</b> (短く・固定・必須情報のみ)。v4は男性役の遵守が改善済み (53役全て機械検査合格)",
            "<b>見本音声は複数クリップを合計120秒まで連結可能</b> — 長い見本ほどクローンが安定する設計。単発5秒だけならv3系より弱い",
        ],
        tuning=[
            "<b>num_steps 40 / cfg (caption・text別スケール)</b> が既定。品質はこの既定で安定",
            "クローン品質を上げたいときは<b>見本を足して長くする</b>のが第一手 (パラメータ弄りより効く)",
            "量産ランでは take 境界のGPUメモリ解放が組込み済み — 長時間連続生成も安定 (実測644テイク完走)",
        ],
        record=[
            "公開161/161行・品質注記0件。役アンカー53役が修正済みF0検査で<b>一発全役合格</b> (v3は26役が要再試行だった)",
            "644テイクの量産ランを完走 (音声時間加重RTF 0.596)。増分導入の手順は docs/model-onboarding.md に記録",
        ],
    ),
    dict(
        id="qwen3-tts-12hz-1.7b", name="Qwen3-TTS (12Hz 1.7B)", method="テキスト指示",
        tagline="説明文で話し方まで細かく書ける多言語モデル。声の固定運用が生命線",
        stats=[("VRAM", "実測ピーク 5.8GB"), ("速度", "中速"), ("構成", "Base+VoiceDesignの2段")],
        setup=[
            "extra <b>qwen</b> を同期。Base / VoiceDesign 両モデル (固定版) は初回自動DL",
        ],
        try_extra="qwen",
        inputs=[
            "<b>VoiceDesignで役の声を1つ生成→その音声をBaseの見本として全セリフに使う</b>2段運用が必須。台詞ごとにVoiceDesignし直すと別人化する (実測で確定した失敗パターン)",
            "声の説明文には<b>性別・年齢・種別・役柄を必ず入れる</b>。感情・話し方は台詞ごとの指示文で変える",
        ],
        tuning=[
            "<b>感情ごとに見本を変えない</b> — 話者同一性が崩れる。感情は指示文だけで付ける",
            "sampling (temperature系) は既定で安定。荒れたらまずアンカー音声の質を疑う",
            "多言語混在台詞 (中華街シーン等) に強い — 外国語風の役はこのモデルが第一候補",
        ],
        record=[
            "公開161/161行。初期実装は見本を渡したつもりで破棄するバグがあり、条件レシート検証で発見→修正した経緯あり",
            "アンカー2段運用の確立後は話者同一性が安定。感情別に見本を変えた旧方式はブラインド評価で不採用",
        ],
    ),
    dict(
        id="voxcpm2", name="VoxCPM 2", method="テキスト指示",
        tagline="人外 (精霊・ゴブリン・機械) の声設計が得意。VRAM食いで音質ばらつき大",
        stats=[("VRAM", "実測ピーク 11.0GB"), ("速度", "中速"), ("性質", "非決定・数打ち前提")],
        setup=[
            "extra <b>voxcpm2</b> を同期。重み (openbmb/VoxCPM2 固定版) は初回自動DL",
            "<b>VRAM 12GBギリギリまで使う</b> (実測11GB) — 他のGPUアプリを閉じてから実行",
        ],
        try_extra="voxcpm2",
        inputs=[
            "<b>2通りの声の作り方</b>: ①voice_design (説明文から自作参照を生成) ②既存見本音声+reference_text。人外役は①、人間役は②が安定",
            "controlの指示文 (英語) で感情・話し方を付ける。「Speak strongly shouting and loud.」のような短い命令形",
        ],
        tuning=[
            "<b>cfg_value 2.0 / inference_timesteps 10</b> が既定。timestepsを上げると音質は締まるが遅くなる",
            "<b>非決定 + 音質ばらつき大</b> — N=4以上の数打ちが前提。retry_badcase を有効にしておく",
            "囁き・息声・超低音などの極端な声はモデル中で最も出せる。まず人外役で試すと強みが分かる",
        ],
        record=[
            "公開161/161行。精霊・ゴブリン等の人外役はvoice_design経由で生成 — 極端な声域の再現は全モデル中最良クラス",
            "VRAM実測11GBで12GB機の上限に近い。量産時は単独実行が前提",
        ],
    ),
]

FOOT = ("共通の量産手順 (数打ち→自動チェック→選抜) と権利・透かしの注意は "
        "docs/production-adoption-guide.pdf を参照。パラメータ実測値の出典は data/manifest.json (公開1,449音声のprovenance)。")

for mspec in MODELS:
    fname = f"recipe-{mspec['id']}.pdf"
    doc = SimpleDocTemplate(
        fname, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm, topMargin=11*mm, bottomMargin=10*mm,
        title=f"調理レシピ: {mspec['name']}", author="gaya-bench (Director: Claude)",
    )
    story = []
    mcol = METHOD_COLORS[mspec["method"]]
    header = Table([
        [P(mspec["name"], h1),
         P(f"<font color='white'><b>{mspec['method']}型</b></font>", ParagraphStyle(
             "mchip", fontName="JPB", fontSize=9.5, leading=12, alignment=1))],
        [P(mspec["tagline"], h1sub), ""],
    ], colWidths=[148*mm, 32*mm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor(mcol)),
        ("SPAN", (0, 1), (1, 1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]))
    story.append(header)

    statc = ParagraphStyle("statc", fontName="JP", fontSize=7.8, leading=10,
                           textColor=SUB, alignment=1, wordWrap="CJK")
    stats = Table([[P(f"<font size=9.5 color='#b45309'><b>{v}</b></font><br/>{k}", statc)
                    for k, v in mspec["stats"]]], colWidths=[60*mm]*3)
    stats.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fafaf9")),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    story.append(stats)

    story.extend(section("1", "用意するもの・セットアップ", "0からローカルで試すまで"))
    for t in mspec["setup"]:
        story.append(LI(t))
    story.append(Spacer(1, 2))
    story.append(codeblock(COMMON_TRY(mspec["try_extra"], mspec["id"])))

    story.extend(section("2", "入力の用意", "このモデルに何を渡すか"))
    for t in mspec["inputs"]:
        story.append(LI(t))

    story.extend(section("3", "最適化アドバイス", "実測で効いた順"))
    for t in mspec["tuning"]:
        story.append(LI(t))

    story.extend(section("4", "本ベンチでの成績", "gaya-bench.pages.dev で全音声を試聴可"))
    for t in mspec["record"]:
        story.append(LI(t))

    story.append(Spacer(1, 7))
    story.append(P(FOOT, small))
    doc.build(story)
    print("OK", fname)
