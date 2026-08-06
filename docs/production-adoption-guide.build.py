# -*- coding: utf-8 -*-
"""gaya-bench 実戦投入ガイド PDF (A4×2枚) を生成する。"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("JP", r"C:\Windows\Fonts\meiryo.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("JPB", r"C:\Windows\Fonts\meiryob.ttc", subfontIndex=0))

ACCENT = colors.HexColor("#b45309")
INK = colors.HexColor("#1c1917")
SUB = colors.HexColor("#57534e")
LINE = colors.HexColor("#d6d3d1")
BGHEAD = colors.HexColor("#fef3c7")

body = ParagraphStyle("body", fontName="JP", fontSize=9.6, leading=14.6,
                      textColor=INK, spaceAfter=3)
small = ParagraphStyle("small", parent=body, fontSize=8.2, leading=11.6, textColor=SUB)
h1 = ParagraphStyle("h1", fontName="JPB", fontSize=17, leading=21, textColor=INK,
                    spaceAfter=2)
h2 = ParagraphStyle("h2", fontName="JPB", fontSize=12, leading=16, textColor=ACCENT,
                    spaceBefore=10, spaceAfter=4)
cell = ParagraphStyle("cell", parent=body, fontSize=8.6, leading=11.8, spaceAfter=0)
cellb = ParagraphStyle("cellb", parent=cell, fontName="JPB")
li = ParagraphStyle("li", parent=body, leftIndent=10, firstLineIndent=-10, spaceAfter=3.5)

def P(t, s=body):
    return Paragraph(t, s)

def LI(t):
    return Paragraph(f"<font color='#b45309'>■</font> {t}", li)

doc = SimpleDocTemplate(
    "production-adoption-guide.pdf", pagesize=A4,
    leftMargin=14*mm, rightMargin=14*mm, topMargin=12*mm, bottomMargin=11*mm,
    title="ガヤボイスTTS 実戦投入ガイド", author="gaya-bench (Director: Claude)",
)

story = []
story.append(P("ガヤボイスTTS 実戦投入ガイド", h1))
story.append(P("9モデル・1,449音声の実測ベンチ (gaya-bench) から要点だけをまとめた資料 — 2026-08 / 聴き比べ: <b>gaya-bench.pages.dev</b>", small))
story.append(HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=5))

story.append(P("1. 声の作り方は3方式 — まずここだけ押さえる", h2))
story.append(LI("<b>プリセット話者型</b>: 用意された声から選ぶ。安定・高速だが、声の種類はモデル任せで増やせない。"))
story.append(LI("<b>参照音声クローン型</b>: 数秒〜十数秒の見本音声から声質を写し取る。<b>見本の品質と権利がすべて</b>。"))
story.append(LI("<b>テキスト指示型</b>: 「低く落ち着いた中年男性の声」のような文章から声を生成。見本音声が不要な代わりに、指示への従い方にモデルごとの癖がある。"))

story.append(P("2. モデル別の特性早見表 (2026-08時点の実測所感)", h2))

def rows(data):
    return [[P(a, cellb), P(b, cell), P(c, cell), P(d, cell)] for a, b, c, d in data]

table_data = [[P("モデル", cellb), P("方式", cellb), P("強み", cellb), P("弱み・注意", cellb)]] + rows([
    ("AivisSpeech コハク", "プリセット",
     "日本語アクセントが最安定。読み(アクセント)を直接指定できる唯一のモデル。軽量",
     "1話者+スタイル数種のみ。多数の役の書き分けは不可"),
    ("Supertonic 3", "プリセット",
     "圧倒的に高速 (量産・リアルタイム向き)",
     "日本語が早口になる癖 (モデル側の性質でパラメータでは直しにくい)。表現の幅は狭い"),
    ("Chatterbox Multi. V3", "クローン",
     "感情の誇張度を数値で制御でき、起伏は大きい",
     "日本語で声質崩壊・棒読み化・誤読が出やすい (要選抜前提)"),
    ("CosyVoice 3", "クローン",
     "指示文と読み指定を併用可能。平均点が安定",
     "同一条件でも毎回結果が変わる (非決定)。数打ち選抜が必須"),
    ("GPT-SoVITS v2ProPlus", "クローン",
     "<b>数分の音声で追加学習できる</b> — 自社収録と組む正式起用の本命候補",
     "参照クリップの切り出し方 (長さ・無音・ノイズ) に敏感"),
    ("Irodori-TTS v3", "テキスト指示",
     "日本語特化。声質の文章指定がよく効く",
     "演技過剰・母音が伸びる傾向。<b>指示文が長いと男性役が女性声化</b>する実測あり"),
    ("Irodori-TTS v4", "指示+クローン",
     "v3の男性声問題が改善 (53役全て機械検査合格)。参照音声は計120秒まで連結可",
     "公開直後 (2026年)。単発の短い見本でのクローンはv3比でやや弱い"),
    ("Qwen3-TTS", "テキスト指示",
     "感情・話し方の指示語彙が豊富。多言語",
     "話者の同一性が揺れやすい。<b>役ごとに「アンカー音声」を固定する運用が必須</b>"),
    ("VoxCPM2", "テキスト指示",
     "人外 (精霊・ゴブリン・機械) の声設計が得意",
     "取れ高の音質ばらつきが大きい。非決定"),
])

t = Table(table_data, colWidths=[32*mm, 21*mm, 62*mm, 67*mm], repeatRows=1)
t.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), BGHEAD),
    ("GRID", (0, 0), (-1, -1), 0.4, LINE),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
]))
story.append(t)
story.append(Spacer(1, 3))
story.append(P("共通: 全モデル完全ローカル動作 (GPU 12GB 1枚で全て動作確認済み)・生成物が商用利用可能な条件で選定。個別のライセンス詳細はサイトのクレジットページ参照。", small))

story.append(P("3. 生成パイプライン — 正式起用時もこの5段構成", h2))
story.append(LI("<b>① 台本の構造化</b>: セリフごとに役柄情報 (性別・年齢・人間/人外・声質・感情・強度) をデータとして持たせる。ここが品質の土台。"))
story.append(LI("<b>② 役の声を先に固定</b>: クローン型は参照音声を役に割当 (性別一致が最優先)。テキスト指示型は役ごとに「アンカー音声」を1つ生成・確定し、全セリフで同じものを使い回す。<b>感情ごとに参照を変えると別人の声になる</b> (実測で確認済みの失敗パターン)。"))
story.append(LI("<b>③ 数打ち生成</b>: 1セリフにつき別シードで3〜4テイク。当たり率はモデルとセリフ難度で大きく変わるため、テイク数は固定せず「合格3本たまるまで追加」が効率的。"))
story.append(LI("<b>④ 自動チェックで足切り</b>: 音量の統一 → 音声認識での読み合わせ (誤読・脱落の検出) → 声の高さによる性別検査。ここまでは機械で回る。"))
story.append(LI("<b>⑤ 人手は最後だけ</b>: 機械通過分から演技の良し悪しを聴いて選ぶ。全量聴取は不要 — 疑義フラグが付いたものだけ聴く運用で十分回る (161セリフ×9モデルを実証済み)。"))

story.append(P("4. チューニングのコツ (方式別)", h2))
story.append(LI("<b>クローン型は参照音声が品質の9割</b>。5〜10秒・無音や雑音なし・目的の演技トーンに近い素材を使う。権利がクリーンな公開素材は少なく (現状5素材で58役を近似カバー、性別+年齢が完全一致する役は18のみ)、量産の本命は<b>声優と契約した自社収録</b>。収録指示書は各モデルの入力要件から機械的に作れる設計を整備済み。"))
story.append(LI("<b>テキスト指示型の指示文は「短く・固定・必須情報のみ」</b>。形容を盛るほど指示に従わなくなる (男性声化けの根本原因だった)。声質の記述は役ごとに固定し、セリフごとに変えるのは感情・演技指示だけにする。"))
story.append(LI("<b>誤読対策は最小限の読み仮名指定</b>。「辛(つら)い」のような曖昧語だけを指定する。<b>全文を仮名化するとアクセント情報が失われ、外国人風の平板な発音 (カタコト化) になる</b>。"))
story.append(LI("<b>全テイクの生成条件 (シード・パラメータ・使った参照音声) を記録する</b>。後述の検収にも直結し、当たりテイクの再現・差し替えができる。"))

story.append(P("5. 注意事項 — これだけは外さない", h2))
story.append(LI("<b>権利は「モデル」「生成物」「参照音声」の3層で別</b>。モデルが商用可でも、参照に使う音声素材の権利は別途必要。<b>他社TTSの出力を学習・参照素材に流用するのは規約違反になる場合がある</b> (利用規約を個別確認)。実在人物の声の無断模倣は全モデルの規約で禁止 — 架空キャラ用途に限定する。"))
story.append(LI("<b>一部モデルは出力へ不可聴の電子透かしを自動埋込</b> (Irodori系・Chatterbox)。納品要件・音声加工フローと事前に照合する。"))
story.append(LI("<b>「渡したつもり」を信用しない</b>。役柄条件がモデルに実際へ渡ったかは別問題で、本ベンチでも公開後に条件伝達バグを2系統発見し全量再生成した。<b>実際に消費された条件のレシートを成果物と一緒に保存し、機械検証できる形で検収する</b> — 原因究明と差し替えのコストが桁違いに下がる。"))
story.append(LI("<b>長時間の量産ランはメモリ管理に注意</b> (Windowsでは生成のたびにGPUメモリ予約が積み上がりPCごと不安定化しうる。テイク境界での解放処理で解決済み)。運用手順は docs/model-onboarding.md 参照。"))

story.append(Spacer(1, 4))
story.append(HRFlowable(width="100%", thickness=0.8, color=LINE, spaceAfter=3))
story.append(P("出典: gaya-bench プロジェクト実測 (15場面161セリフ×9モデル、公開1,449音声+検証用数千テイク)。品質注記は自動判定・人手確認は順次。詳細な根拠はリポジトリの docs/research/ と各Issueに記録。", small))

doc.build(story)
print("OK")
