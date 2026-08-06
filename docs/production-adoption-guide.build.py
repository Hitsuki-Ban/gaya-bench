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
DARK = colors.HexColor("#1c1917")

C_PRESET = colors.HexColor("#0369a1")
C_CLONE = colors.HexColor("#15803d")
C_PROMPT = colors.HexColor("#b45309")
BG_PRESET = colors.HexColor("#eff6ff")
BG_CLONE = colors.HexColor("#f0fdf4")
BG_PROMPT = colors.HexColor("#fffbeb")

body = ParagraphStyle("body", fontName="JP", fontSize=9.2, leading=13.8,
                      textColor=INK, spaceAfter=3, wordWrap="CJK")
small = ParagraphStyle("small", parent=body, fontSize=7.9, leading=10.8, textColor=SUB)
h1 = ParagraphStyle("h1", fontName="JPB", fontSize=21, leading=25,
                    textColor=colors.white)
h1sub = ParagraphStyle("h1sub", fontName="JP", fontSize=8.6, leading=12,
                       textColor=colors.HexColor("#fcd34d"))
h2 = ParagraphStyle("h2", fontName="JPB", fontSize=12.5, leading=16, textColor=INK,
                    spaceBefore=0, spaceAfter=0)
cell = ParagraphStyle("cell", parent=body, fontSize=8.6, leading=11.8, spaceAfter=0)
cellb = ParagraphStyle("cellb", parent=cell, fontName="JPB")
cardh = ParagraphStyle("cardh", fontName="JPB", fontSize=10.4, leading=13,
                       textColor=colors.white)
card = ParagraphStyle("card", parent=body, fontSize=8.5, leading=12.2, spaceAfter=0)
li = ParagraphStyle("li", parent=body, fontSize=8.9, leading=13, leftIndent=10,
                    firstLineIndent=-10, spaceAfter=3)
code = ParagraphStyle("code", fontName="JP", fontSize=8.2, leading=12,
                      textColor=colors.HexColor("#e7e5e4"), wordWrap="CJK")

def P(t, s=body):
    return Paragraph(t, s)

def LI(t):
    return Paragraph(f"<font color='#b45309'>◆</font> {t}", li)

def section(num, title, note=None):
    numcell = P(f"<font color='white'><b>{num}</b></font>", ParagraphStyle(
        "secnum", fontName="JPB", fontSize=12.5, leading=15, alignment=1))
    row = [numcell, P(f"<b>{title}</b>" + (f"　<font size=8 color='#78716c'>{note}</font>" if note else ""), h2)]
    t = Table([row], colWidths=[8.5*mm, 171.5*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), ACCENT),
        ("LINEBELOW", (1, 0), (1, 0), 1.6, ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
        ("LEFTPADDING", (1, 0), (1, 0), 5),
    ]))
    return [Spacer(1, 8), t, Spacer(1, 5)]

doc = SimpleDocTemplate(
    "production-adoption-guide.pdf", pagesize=A4,
    leftMargin=15*mm, rightMargin=15*mm, topMargin=11*mm, bottomMargin=11*mm,
    title="ガヤボイスTTS 実戦投入ガイド", author="gaya-bench (Director: Claude)",
)

story = []

# ---- タイトル帯 ------------------------------------------------------------
title_block = Table([
    [P("ガヤボイスTTS 実戦投入ガイド", h1)],
    [P("モブNPCの環境ボイスをTTSで量産するための実測知見 ｜ 2026-08 ｜ 聴き比べサイト: <b>gaya-bench.pages.dev</b>", h1sub)],
], colWidths=[180*mm])
title_block.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), DARK),
    ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ("TOPPADDING", (0, 0), (0, 0), 7),
    ("BOTTOMPADDING", (0, 1), (0, 1), 7),
]))
story.append(title_block)

statc = ParagraphStyle("statc", fontName="JP", fontSize=8, leading=10.5,
                       textColor=SUB, alignment=1, wordWrap="CJK")
def stat(n, label):
    return P(f"<font size=13 color='#b45309'><b>{n}</b></font><br/>{label}", statc)
stats = Table([[stat("9", "モデル"), stat("15", "場面 (酒場・戦場など)"),
                stat("161", "セリフ×全モデル"), stat("1,449", "公開音声"),
                stat("12GB", "GPU 1枚で全て動作")]],
              colWidths=[36*mm]*5)
stats.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fafaf9")),
    ("BOX", (0, 0), (-1, -1), 0.5, LINE),
    ("LINEAFTER", (0, 0), (-2, -1), 0.5, LINE),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
]))
story.append(stats)

# ---- 1. 3方式カード -------------------------------------------------------
story.extend(section("1", "声の作り方は3方式", "「何を入力するか」が違う"))

def method_card(title, color, bg, lines):
    inner = [[P(title, cardh)]] + [[P(t, card)] for t in lines]
    tt = Table(inner, colWidths=[57.5*mm])
    tt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), color),
        ("BACKGROUND", (0, 1), (0, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.8, color),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tt

cards = Table([[
    method_card("プリセット話者型", C_PRESET, BG_PRESET, [
        "<b>入力</b>: セリフ ＋ 声とスタイルの選択",
        "<font color='#0369a1'>例: 「コハク」×「ささやき」を選ぶ<br/>→ その声でセリフを読む</font>",
        "付属の声から選ぶだけ。<b>安定・高速</b>。声の種類は増やせない",
    ]),
    method_card("参照音声クローン型", C_CLONE, BG_CLONE, [
        "<b>入力</b>: セリフ ＋ 見本音声 (5〜10秒)",
        "<font color='#15803d'>例: 女性声優の収録WAVを渡す<br/>→ その声質でセリフを読む</font>",
        "見本の声・演技を写し取る。<b>見本の品質と権利がすべて</b>",
    ]),
    method_card("テキスト指示型", C_PROMPT, BG_PROMPT, [
        "<b>入力</b>: セリフ ＋ 声の説明文",
        "<font color='#b45309'>例: 「低く落ち着いた中年男性の声」<br/>と書く → その声でセリフを読む</font>",
        "見本いらずで自由度最大。ただし<b>指示への従い方に癖</b>がある",
    ]),
]], colWidths=[60*mm, 60*mm, 60*mm])
cards.setStyle(TableStyle([
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
]))
story.append(cards)

# ---- 2. モデル早見表 + 対応入力 --------------------------------------------
story.extend(section("2", "モデル早見表と対応入力", "○の判定は公開サイトと同一データ"))

def chip(kind):
    m = {"P": ("プリセット", "#0369a1"), "C": ("クローン", "#15803d"),
         "T": ("テキスト指示", "#b45309"), "TC": ("指示＋見本", "#b45309")}
    label, col = m[kind]
    return P(f"<font color='{col}' size=7.8><b>{label}</b></font>", cell)

mid = ParagraphStyle("mid", parent=cell, alignment=1)
midb = ParagraphStyle("midb", parent=cellb, fontSize=7.6, leading=9.6, alignment=1)

def ox(v):
    return P("<font color='#15803d'><b>○</b></font>", mid) if v else P(
        "<font color='#d6d3d1'>—</font>", mid)

# (name, kind, 強み, 弱み, emotion, voice_prompt, clone, nonverbal, reading)
rows = [
    ("AivisSpeech コハク", "P", "日本語アクセント最安定。読みを直接指定可", "声は1話者＋数スタイルのみ",
     True, False, False, False, True),
    ("Supertonic 3", "P", "圧倒的に高速 (量産・リアルタイム向き)", "日本語が早口になる癖。表現の幅は狭い",
     False, False, False, False, True),
    ("Chatterbox V3", "C", "感情の誇張度を数値で制御できる", "日本語で声質崩壊・誤読が出やすい",
     True, False, True, False, False),
    ("CosyVoice 3", "C", "指示文と読み指定を併用でき安定", "同一条件でも毎回変わる (数打ち前提)",
     True, False, True, False, True),
    ("GPT-SoVITS v2ProPlus", "C", "<b>数分の音声で追加学習可</b> — 自社収録と組む本命", "見本の切り出し品質に敏感",
     False, False, True, False, True),
    ("Irodori-TTS v3", "T", "日本語特化。声質の文章指定がよく効く", "演技過剰気味。長い指示で男性役が崩れる",
     True, True, True, True, True),
    ("Irodori-TTS v4", "TC", "v3の男性声問題が改善。見本は計120秒まで", "公開直後 (2026年)。実績蓄積中",
     True, True, True, True, False),
    ("Qwen3-TTS", "T", "感情・話し方の指示語彙が豊富", "声の同一性が揺れる — 役ごとアンカー必須",
     False, True, True, False, False),
    ("VoxCPM2", "T", "人外 (精霊・機械など) の声設計が得意", "取れ高の音質ばらつきが大きい",
     True, True, True, False, True),
]
head = ([P(f"<font color='white'>{t}</font>", cellb)
         for t in ["モデル", "方式", "強み", "弱み・注意"]] +
        [P(f"<font color='white'>{t}</font>", midb)
         for t in ["感情", "声質文", "見本", "非言語", "読み"]])
table_data = [head]
for name, kind, good, bad, e, vp, c, n, r in rows:
    table_data.append([P(name, cellb), chip(kind), P(good, cell), P(bad, cell),
                       ox(e), ox(vp), ox(c), ox(n), ox(r)])

t = Table(table_data, colWidths=[31.5*mm, 20*mm, 49*mm, 44.5*mm, 7*mm, 8*mm, 7*mm, 7*mm, 6*mm],
          repeatRows=1)
style = [
    ("BACKGROUND", (0, 0), (-1, 0), DARK),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("LEFTPADDING", (0, 0), (-1, -1), 3.4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
]
for i in range(len(head)):
    if i >= 4:
        style.append(("LEFTPADDING", (i, 0), (i, -1), 0.5))
        style.append(("RIGHTPADDING", (i, 0), (i, -1), 0.5))
for i in range(1, len(table_data)):
    style.append(("LINEBELOW", (0, i), (-1, i), 0.4, LINE))
    if i % 2 == 0:
        style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafaf9")))
t.setStyle(TableStyle(style))
story.append(t)
story.append(Spacer(1, 2.5))
story.append(P("右5列=受け付ける入力 (○=対応): <b>感情</b>=感情・強度の指定 ／ <b>声質文</b>=文章で声を作る ／ <b>見本</b>=クローン元音声 ／ <b>非言語</b>=笑い・ため息等 ／ <b>読み</b>=漢字の読み指定。全モデル生成物商用可の条件で選定 (詳細はサイトのクレジット)。", small))

# ---- 3. 構造化台本 --------------------------------------------------------
sec3 = section("3", "台本は「構造化」して持つ", "これが全モデルへの入力の源泉になる")

SP = "&nbsp;&nbsp;"
code_lines = [
    "<font color='#a8a29e'># scenarios/village-morning.yaml (公開データの実物・抜粋)</font>",
    "<font color='#fbbf24'>characters:</font>",
    f"{SP}- <font color='#fbbf24'>id:</font> granny",
    f"{SP}{SP}<font color='#fbbf24'>gender:</font> female　<font color='#fbbf24'>age:</font> elderly <font color='#a8a29e'># 「声の印象」として指定</font>",
    f"{SP}{SP}<font color='#fbbf24'>voice:</font> しわがれた高めの声。ゆっくりとした丁寧な口調。 <font color='#a8a29e'># → 声質説明文・見本選びの材料</font>",
    "<font color='#fbbf24'>lines:</font>",
    f"{SP}- <font color='#fbbf24'>character:</font> granny",
    f"{SP}{SP}<font color='#fbbf24'>text:</font> 腰が痛うてかなわんわ…… <font color='#a8a29e'># セリフ本文</font>",
    f"{SP}{SP}<font color='#fbbf24'>emotion:</font> pain　<font color='#fbbf24'>intensity:</font> 2 <font color='#a8a29e'># 12感情×強さ1〜3 → 演技指示へ変換</font>",
    f"{SP}{SP}<font color='#fbbf24'>delivery:</font> 腰を叩きながらのぼやき。語尾が伸びて消える。 <font color='#a8a29e'># 演出意図</font>",
    f"{SP}{SP}<font color='#fbbf24'>final_intonation:</font> fall <font color='#a8a29e'># 語尾を上げない (カタコト化の検査に使用)</font>",
]
ctab = Table([[P(t, code)] for t in code_lines], colWidths=[180*mm])
ctab.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), DARK),
    ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ("TOPPADDING", (0, 0), (-1, -1), 1.2),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
    ("TOPPADDING", (0, 0), (0, 0), 5),
    ("BOTTOMPADDING", (0, -1), (0, -1), 5),
]))
story.append(KeepTogether(sec3 + [
    ctab,
    Spacer(1, 2.5),
    P("ポイント: 台本を最初からこの形式で書けば、<b>モデルを乗り換えても台本は書き直さない</b>。方式ごとの入力 (説明文・見本選択・演技指示) は各フィールドから機械的に組み立てられる。誤読しやすい語だけ読み仮名フィールドを足す。", small),
]))

# ---- 4. 量産パイプライン --------------------------------------------------
story.extend(section("4", "量産パイプライン", "5段で回す — 人手は最後だけ"))

steps = [
    ("1", "台本の構造化", "上記YAML形式。役柄・感情・演出をデータで持つ"),
    ("2", "役の声を固定", "クローン型は見本を役に割当。指示型は役ごとに<b>アンカー音声</b>を1つ確定し全セリフで使い回す — <b>感情ごとに変えると別人の声になる</b> (実測)"),
    ("3", "数打ち生成", "1セリフ3〜4テイクを別シードで。合格3本たまるまで追加"),
    ("4", "自動チェック", "音量統一 → 音声認識で読み合わせ (誤読検出) → 声の高さで性別検査"),
    ("5", "人手選抜", "疑義フラグ付きだけ聴く。全量聴取は不要 (161セリフ×9モデルで実証済み)"),
]
step_data = []
for num, name, desc in steps:
    step_data.append([
        P(f"<font color='white'><b>{num}</b></font>", ParagraphStyle(
            "num", fontName="JPB", fontSize=10.5, leading=12, alignment=1)),
        P(f"<b>{name}</b>", cell),
        P(desc, cell),
    ])
st = Table(step_data, colWidths=[8*mm, 27*mm, 145*mm])
sts = [
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 3.4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
]
for i in range(len(step_data)):
    sts.append(("BACKGROUND", (0, i), (0, i), ACCENT))
    sts.append(("LINEBELOW", (0, i), (-1, i), 0.4, LINE))
    if i % 2 == 1:
        sts.append(("BACKGROUND", (1, i), (-1, i), colors.HexColor("#fafaf9")))
st.setStyle(TableStyle(sts))
story.append(st)

# ---- 5. チューニングの勘所 ------------------------------------------------
story.extend(section("5", "チューニングの勘所", "実測で確定した運用則"))

story.append(LI("<b>クローン型は見本が品質の9割</b>。5〜10秒・雑音なし・目的の演技トーンに近い素材を。権利クリーンな公開素材は少なく、量産の本命は<b>声優と契約した自社収録</b> (収録指示書はモデル入力要件から自動生成できる設計済み)。"))
story.append(LI("<b>指示型の声質説明文は「短く・固定・必須情報のみ」</b>。良い例:<font color='#15803d'>「若い成人の男性。低く落ち着いた男性の声。」</font>／悪い例: 形容を重ねた長文 — 従わなくなり<font color='#b91c1c'>男性役が女性声になる</font>実測あり。セリフごとに変えるのは感情・演技の指示だけ。"))
story.append(LI("<b>誤読は曖昧語だけ読み仮名指定</b> (例: 辛い→「つらい」)。<b>全文を仮名化してはいけない</b> — アクセント情報が失われ、外国人風の平板な発音になる。"))
story.append(LI("<b>全テイクの生成条件 (シード・パラメータ・見本) を記録</b>し、当たりテイクを再現・差し替えできる状態を保つ。"))

# ---- 6. 注意 --------------------------------------------------------------
story.extend(section("6", "注意事項", "契約・納品の前に必ず確認"))

story.append(LI("<b>権利は「モデル・生成物・見本音声」の3層で別</b>。モデルが商用可でも見本の権利は別途必要。<b>他社TTSの出力を見本・学習に流用するのは規約違反の場合あり</b>。実在人物の声の無断模倣は全モデルで禁止 — 架空キャラ用途に限定。"))
story.append(LI("<b>一部モデル (Irodori系・Chatterbox) は不可聴の電子透かしを出力へ自動埋込</b>。納品要件・音声加工フローと事前に照合を。"))
story.append(LI("<b>「渡したつもり」を信用しない</b>。役柄条件が実際にモデルへ渡ったかは別問題 (本ベンチも公開後に伝達バグを2系統発見し全量再生成)。<b>実際に使われた条件の記録を成果物とセットで保存・検収</b>する。"))

story.append(Spacer(1, 5))
story.append(HRFlowable(width="100%", thickness=0.8, color=LINE, spaceAfter=2.5))
story.append(P("出典: gaya-bench 実測 (15場面161セリフ×9モデル)。品質注記は自動判定・人手確認は順次。根拠はリポジトリ docs/research/ と各Issueに記録。", small))

doc.build(story)
print("OK")
