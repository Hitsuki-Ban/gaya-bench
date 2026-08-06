# -*- coding: utf-8 -*-
"""gaya-bench 実戦投入ガイド PDF (A4×2枚) を生成する。"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    KeepTogether,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("JP", r"C:\Windows\Fonts\meiryo.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("JPB", r"C:\Windows\Fonts\meiryob.ttc", subfontIndex=0))

ACCENT = colors.HexColor("#b45309")
INK = colors.HexColor("#292524")
SUB = colors.HexColor("#78716c")
LINE = colors.HexColor("#e7e5e4")

C_PRESET = colors.HexColor("#0369a1")   # 青系: プリセット
C_CLONE = colors.HexColor("#15803d")    # 緑系: クローン
C_PROMPT = colors.HexColor("#b45309")   # 琥珀系: テキスト指示
BG_PRESET = colors.HexColor("#eff6ff")
BG_CLONE = colors.HexColor("#f0fdf4")
BG_PROMPT = colors.HexColor("#fffbeb")

body = ParagraphStyle("body", fontName="JP", fontSize=9.8, leading=15,
                      textColor=INK, spaceAfter=4)
small = ParagraphStyle("small", parent=body, fontSize=8.2, leading=11.5, textColor=SUB)
h1 = ParagraphStyle("h1", fontName="JPB", fontSize=18, leading=22, textColor=INK)
h2 = ParagraphStyle("h2", fontName="JPB", fontSize=12.5, leading=17, textColor=INK,
                    spaceBefore=13, spaceAfter=5)
cell = ParagraphStyle("cell", parent=body, fontSize=8.8, leading=12.4, spaceAfter=0)
cellb = ParagraphStyle("cellb", parent=cell, fontName="JPB")
cardh = ParagraphStyle("cardh", fontName="JPB", fontSize=10.2, leading=13,
                       textColor=colors.white)
card = ParagraphStyle("card", parent=body, fontSize=8.6, leading=12.6, spaceAfter=0)
li = ParagraphStyle("li", parent=body, leftIndent=10, firstLineIndent=-10,
                    spaceAfter=3.5)

def P(t, s=body):
    return Paragraph(t, s)

def H2(t):
    return Paragraph(f"<font color='#b45309'>▌</font>{t}", h2)

def LI(t):
    return Paragraph(f"<font color='#b45309'>・</font>{t}", li)

doc = SimpleDocTemplate(
    "production-adoption-guide.pdf", pagesize=A4,
    leftMargin=15*mm, rightMargin=15*mm, topMargin=13*mm, bottomMargin=12*mm,
    title="ガヤボイスTTS 実戦投入ガイド", author="gaya-bench (Director: Claude)",
)

story = []
story.append(P("ガヤボイスTTS 実戦投入ガイド", h1))
story.append(P("9モデル × 161セリフの実測ベンチから要点だけ ｜ 2026-08 ｜ 聴き比べ: <b>gaya-bench.pages.dev</b>", small))
story.append(HRFlowable(width="100%", thickness=1.4, color=ACCENT, spaceBefore=3, spaceAfter=2))

# ---- 1. 3方式カード -------------------------------------------------------
story.append(H2("1. 声の作り方は3方式 — 「何を入力するか」が違う"))

def method_card(title, color, bg, lines):
    inner = [[P(title, cardh)]] + [[P(t, card)] for t in lines]
    tt = Table(inner, colWidths=[57*mm])
    tt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), color),
        ("BACKGROUND", (0, 1), (0, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.8, color),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tt

cards = Table([[
    method_card("プリセット話者型", C_PRESET, BG_PRESET, [
        "<b>入力</b>: セリフ ＋ 声とスタイルの選択",
        "<font color='#0369a1'>例: 「コハク」×「ささやき」で<br/>『いらっしゃいませ』</font>",
        "声はモデル付属のものから選ぶだけ。<b>安定・高速</b>だが声の種類は増やせない",
    ]),
    method_card("参照音声クローン型", C_CLONE, BG_CLONE, [
        "<b>入力</b>: セリフ ＋ 見本音声 (5〜10秒)",
        "<font color='#15803d'>例: 女性声優の収録WAVを渡すと<br/>その声質で『いらっしゃいませ』</font>",
        "見本の声・演技を写し取る。<b>見本の品質と権利がすべて</b>",
    ]),
    method_card("テキスト指示型", C_PROMPT, BG_PROMPT, [
        "<b>入力</b>: セリフ ＋ 声の説明文",
        "<font color='#b45309'>例: 「低く落ち着いた中年男性の声」<br/>と書く → その声で話してくれる</font>",
        "見本音声いらずで自由度最大。ただし<b>指示への従い方に癖</b>がある",
    ]),
]], colWidths=[60*mm, 60*mm, 60*mm])
cards.setStyle(TableStyle([
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
]))
story.append(cards)

# ---- 2. モデル早見表 ------------------------------------------------------
story.append(H2("2. モデル早見表 (実測所感)"))

def chip(kind):
    m = {"P": ("プリセット", "#0369a1"), "C": ("クローン", "#15803d"),
         "T": ("テキスト指示", "#b45309"), "TC": ("指示＋見本", "#b45309")}
    label, col = m[kind]
    return P(f"<font color='{col}'><b>{label}</b></font>", cell)

rows = [
    ("AivisSpeech コハク", "P", "日本語アクセントが最も安定。読みを直接指定できる", "声は1話者＋数スタイルのみ"),
    ("Supertonic 3", "P", "圧倒的に高速 (量産・リアルタイム向き)", "日本語が早口になる癖。表現の幅は狭い"),
    ("Chatterbox V3", "C", "感情の誇張度を数値で制御できる", "日本語で声質崩壊・誤読が出やすい"),
    ("CosyVoice 3", "C", "指示文と読み指定を併用でき、平均点が安定", "同一条件でも毎回変わる (数打ち前提)"),
    ("GPT-SoVITS v2ProPlus", "C", "<b>数分の音声で追加学習可</b> — 自社収録と組む本命", "見本の切り出し品質に敏感"),
    ("Irodori-TTS v3", "T", "日本語特化。声質の文章指定がよく効く", "演技過剰気味。長い指示で男性役が崩れる"),
    ("Irodori-TTS v4", "TC", "v3の男性声問題が改善。見本は計120秒まで連結可", "公開直後 (2026年)。実績蓄積中"),
    ("Qwen3-TTS", "T", "感情・話し方の指示語彙が豊富", "声の同一性が揺れる — 役ごとの固定アンカー必須"),
    ("VoxCPM2", "T", "人外 (精霊・ゴブリン・機械) の声設計が得意", "取れ高の音質ばらつきが大きい"),
]
table_data = [[P("モデル", cellb), P("方式", cellb), P("強み", cellb), P("弱み・注意", cellb)]]
for name, kind, good, bad in rows:
    table_data.append([P(name, cellb), chip(kind), P(good, cell), P(bad, cell)])

t = Table(table_data, colWidths=[34*mm, 24*mm, 62*mm, 60*mm], repeatRows=1)
style = [
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f4")),
    ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.HexColor("#a8a29e")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 3.4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
]
for i in range(1, len(table_data)):
    style.append(("LINEBELOW", (0, i), (-1, i), 0.4, LINE))
    if i % 2 == 0:
        style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafaf9")))
t.setStyle(TableStyle(style))
story.append(t)
story.append(Spacer(1, 2))
story.append(P("全モデル完全ローカル動作 (GPU 12GB 1枚で確認)・生成物商用可の条件で選定。ライセンス詳細はサイトのクレジット参照。", small))

# ---- 2b. 対応入力マトリクス ------------------------------------------------
mid = ParagraphStyle("mid", parent=cell, alignment=1)
midb = ParagraphStyle("midb", parent=cellb, alignment=1)

def ox(v, color="#15803d"):
    return P(f"<font color='{color}'><b>○</b></font>", mid) if v else P(
        "<font color='#d6d3d1'>—</font>", mid)

caps_rows = [
    # (model, emotion, voice_prompt, clone, nonverbal, reading)
    ("AivisSpeech コハク", True, False, False, False, True),
    ("Supertonic 3", False, False, False, False, True),
    ("Chatterbox V3", True, False, True, False, False),
    ("CosyVoice 3", True, False, True, False, True),
    ("GPT-SoVITS v2ProPlus", False, False, True, False, True),
    ("Irodori-TTS v3", True, True, True, True, True),
    ("Irodori-TTS v4", True, True, True, True, False),
    ("Qwen3-TTS", False, True, True, False, False),
    ("VoxCPM2", True, True, True, False, True),
]
cap_data = [[P("モデル", cellb), P("感情の指示", midb), P("声質の説明文", midb),
             P("見本音声", midb), P("非言語音", midb), P("読み仮名指定", midb)]]
for name, e, vp, c, n, r in caps_rows:
    cap_data.append([P(name, cellb), ox(e), ox(vp), ox(c), ox(n), ox(r)])

ct = Table(cap_data, colWidths=[40*mm, 28*mm, 28*mm, 28*mm, 28*mm, 28*mm], repeatRows=1)
cstyle = [
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f4")),
    ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.HexColor("#a8a29e")),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 2.6),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
]
for i in range(1, len(cap_data)):
    cstyle.append(("LINEBELOW", (0, i), (-1, i), 0.4, LINE))
    if i % 2 == 0:
        cstyle.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafaf9")))
ct.setStyle(TableStyle(cstyle))
story.append(KeepTogether([
    H2("2b. 各モデルが受け付ける入力要素 (公開データと同一の真値)"),
    ct,
    Spacer(1, 2),
    P("○=対応。<b>感情の指示</b>=感情や強度を指定できる ／ <b>声質の説明文</b>=文章で声を作れる ／ <b>見本音声</b>=クローン元を渡せる ／ <b>非言語音</b>=笑い・ため息などの表現指示 ／ <b>読み仮名指定</b>=漢字の読みを指定できる。", small),
]))

# ---- 3. パイプライン ------------------------------------------------------
story.append(H2("3. 量産パイプライン — 5段で回す"))

steps = [
    ("1", "台本の構造化", "セリフごとに役柄情報 (性別・年齢・声質・感情・強度) をデータで付与する"),
    ("2", "役の声を固定", "クローン型は見本を役に割当。指示型は役ごとに<b>アンカー音声</b>を先に1つ確定し全セリフで使い回す — <b>感情ごとに変えると別人の声になる</b>"),
    ("3", "数打ち生成", "1セリフにつき別シードで3〜4テイク。合格3本たまるまで追加"),
    ("4", "自動チェック", "音量統一 → 音声認識で読み合わせ (誤読検出) → 声の高さで性別検査"),
    ("5", "人手は最後だけ", "疑義フラグ付きのテイクだけ聴いて選ぶ。全量聴取は不要 (161セリフ×9モデルで実証済み)"),
]
step_data = []
for num, name, desc in steps:
    step_data.append([
        P(f"<font color='white'><b>{num}</b></font>", ParagraphStyle(
            "num", fontName="JPB", fontSize=10, leading=12, alignment=1)),
        P(f"<b>{name}</b>", cell),
        P(desc, cell),
    ])
st = Table(step_data, colWidths=[8*mm, 30*mm, 142*mm])
sts = [
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 3.6),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
]
for i in range(len(step_data)):
    sts.append(("BACKGROUND", (0, i), (0, i), ACCENT))
    sts.append(("LINEBELOW", (0, i), (-1, i), 0.4, LINE))
st.setStyle(TableStyle(sts))
story.append(st)

# ---- 4. チューニング ------------------------------------------------------
story.append(H2("4. チューニングのコツ"))
story.append(LI("<b>クローン型は見本が品質の9割</b>。5〜10秒・雑音なし・目的の演技トーンに近い素材を。権利クリーンな公開素材は少なく、量産の本命は<b>声優と契約した自社収録</b> (収録指示書はモデルの入力要件から自動生成できる設計済み)。"))
story.append(LI("<b>指示型の説明文は「短く・固定・必須情報のみ」</b>。形容を盛るほど従わなくなる (男性役が女性声化した根本原因)。セリフごとに変えてよいのは感情・演技の指示だけ。"))
story.append(LI("<b>誤読は曖昧語だけ読み仮名指定</b> (例:「辛(つら)い」)。全文を仮名にするとアクセントが崩れ、外国人風の平板な発音になる。"))
story.append(LI("<b>全テイクの生成条件 (シード・パラメータ・見本) を記録</b>。当たりテイクの再現・差し替えができる。"))

# ---- 5. 注意 --------------------------------------------------------------
story.append(H2("5. 注意事項 — これだけは外さない"))
story.append(LI("<b>権利は「モデル・生成物・見本音声」の3層で別</b>。モデルが商用可でも見本の権利は別途必要。他社TTSの出力を見本や学習に流用するのは規約違反の場合あり。実在人物の声の無断模倣は全モデルで禁止 — 架空キャラ用途に限定。"))
story.append(LI("<b>一部モデルは出力に不可聴の電子透かしを自動で埋め込む</b> (Irodori系・Chatterbox)。納品要件と事前に照合を。"))
story.append(LI("<b>「渡したつもり」を信用しない</b>。役柄条件が実際にモデルへ渡ったかは別問題 (本ベンチでも公開後に伝達バグを2系統発見し全量再生成)。実際に使われた条件の記録を成果物とセットで保存し、機械検証できる形で検収する。"))

story.append(Spacer(1, 6))
story.append(HRFlowable(width="100%", thickness=0.8, color=LINE, spaceAfter=3))
story.append(P("出典: gaya-bench 実測 (15場面161セリフ×9モデル、公開1,449音声＋検証用数千テイク)。品質注記は自動判定・人手確認は順次。根拠はリポジトリ docs/research/ と各Issueに記録。", small))

doc.build(story)
print("OK")
