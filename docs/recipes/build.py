# -*- coding: utf-8 -*-
"""モデル別調理レシピ (A4×1枚×9モデル) を docs/recipes/ 用に生成する。

セットアップは公式配布物 (pip / GitHub / 公式アプリ) のみで完結する手順を記載。
数値 (VRAM・成績) は gaya-bench 公開manifestの実測値。
"""
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

body = ParagraphStyle("body", fontName="JP", fontSize=10.2, leading=15.8,
                      textColor=INK, spaceAfter=3, wordWrap="CJK")
small = ParagraphStyle("small", parent=body, fontSize=8.4, leading=11.8, textColor=SUB)
h1 = ParagraphStyle("h1", fontName="JPB", fontSize=19.5, leading=24,
                    textColor=colors.white, wordWrap="CJK")
h1sub = ParagraphStyle("h1sub", fontName="JP", fontSize=9.2, leading=12.8,
                       textColor=colors.HexColor("#fcd34d"), wordWrap="CJK")
h2 = ParagraphStyle("h2", fontName="JPB", fontSize=12.6, leading=16.5, textColor=INK,
                    wordWrap="CJK")
li = ParagraphStyle("li", parent=body, leftIndent=10, firstLineIndent=-10,
                    spaceAfter=4.5)
code = ParagraphStyle("code", fontName="JP", fontSize=8.8, leading=13,
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
    return [Spacer(1, 11), t, Spacer(1, 5.5)]

def codeblock(lines):
    tt = Table([[P(t, code)] for t in lines], colWidths=[180*mm])
    tt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
        ("TOPPADDING", (0, 0), (0, 0), 4),
        ("BOTTOMPADDING", (0, -1), (0, -1), 4),
    ]))
    return tt

G = "<font color='#a8a29e'>"   # コメント
K = "<font color='#fbbf24'>"   # 強調キー
E = "</font>"
S2 = "&nbsp;&nbsp;"
S4 = S2 + S2

MODELS = [
    dict(
        id="aivisspeech-kohaku", name="AivisSpeech コハク", method="プリセット",
        tagline="日本語アクセント最安定・読みを直接指定できる唯一のモデル。多役の書き分けは不可",
        stats=[("VRAM", "Engine側で軽量"), ("速度", "高速 (Engine処理)"), ("ライセンス", "ACML-1.0")],
        setup=[
            "公式サイト (aivis-project.com) から <b>AivisSpeechアプリ</b> をインストール。GUIだけで即生成できる",
            "アプリ内の <b>AivisHub</b> から話者モデル「コハク」を追加 (ライセンス条件も同画面に表示される)",
            "自動化するならアプリ同梱の <b>AivisSpeech Engine</b> (VOICEVOX互換 REST API、port 10101) を使う",
        ],
        setup_code=[
            f"{G}# GUI: アプリ起動 → コハクを選択 → テキスト入力 → 再生/保存 (コード不要){E}",
            f"{G}# REST APIの最小例 (PowerShell。speaker=スタイルID はアプリの話者一覧で確認){E}",
            "$u = \"http://127.0.0.1:10101\"; $t = \"いらっしゃいませ\"",
            "$q = Invoke-RestMethod -Method Post \"$u/audio_query?speaker=888753760&amp;text=$t\"",
            "Invoke-RestMethod -Method Post \"$u/synthesis?speaker=888753760\" `",
            f"{S2}-Body ($q | ConvertTo-Json -Depth 10) -ContentType application/json -OutFile out.wav",
        ],
        inputs=[
            "<b>話者とスタイルの選択が入力のすべて</b>。スタイル (ノーマル/あまあま/せつなめ/ねむたい) を感情へマッピングする (例: 悲しみ・痛み→せつなめ)",
            "<b>読み・アクセントは audio_query の accent_phrases を編集して直接指定できる</b> — 全モデル中唯一の完全アクセント制御。誤読・平板化はここで確実に直せる",
        ],
        tuning=[
            "強度は <b>intonationScale</b> (0.8〜1.2目安) と <b>tempoDynamicsScale</b> で付ける。上げすぎると不自然",
            "声が1話者のため、多役は「スタイル×パラメータ差」で疑似的に書き分けるか他モデルと併用",
            "決定論的 (同入力=同出力) なので数打ち不要。品質はほぼ入力設計で決まる",
        ],
        record=[
            "公開161/161行。読みが必要な25行はaccent_phrases経由で適用 — 読み起因のカタコト化なし",
            "差し替え・再現が最も容易で、量産の「確実な下地」役として機能した",
        ],
    ),
    dict(
        id="supertonic-3", name="Supertonic 3", method="プリセット",
        tagline="ONNXで動く超高速モデル。量産・リアルタイム向き、表現の幅は狭い",
        stats=[("VRAM", "軽量 (ONNX)"), ("速度", "最速クラス"), ("弱点", "日本語が早口")],
        setup=[
            "Python 3.10+ の仮想環境に公式パッケージを入れるだけ。重みは初回に自動DL (auto_download)",
            "CPUでも動く。GPU (onnxruntime-gpu) があれば更に高速",
            "公式ドキュメント: supertone-inc.github.io/supertonic-py",
        ],
        setup_code=[
            "pip install supertonic",
            f"{G}# Python{E}",
            "from supertonic import TTS",
            "tts = TTS(auto_download=True)",
            "style = tts.get_voice_style(voice_name=\"F3\")",
            "wav, dur = tts.synthesize(\"いらっしゃいませ！\", voice_style=style, lang=\"ja\", speed=1.05)",
            "tts.save_audio(wav, \"out.wav\")",
        ],
        inputs=[
            "<b>voice_name (プリセット声) の選択</b> + セリフ。声一覧は公式ドキュメントの voice list 参照",
            "読み対応 — 誤読はかな表記で修正 (曖昧語のみ。全文かな化はしない)",
        ],
        tuning=[
            "<b>speed</b> (0.7〜2.0、既定1.05) — ただし日本語の早口感はモデル側の癖で、1.0にしても約5%しか変わらない (実測)。ゆっくり聞かせたい台詞は0.9前後を試す",
            "total_steps 8 が既定。上げても品質向上は限定的で速度メリットが消える",
            "決定論的なので数打ち不要。合わない台詞は他モデルへ回すのが早い",
        ],
        record=[
            "公開161/161行。話速はadapter設定ではなくモデル側性質と切り分け済み (公式SDK・issueまで確認)",
            "生成が最速のため、台詞の当たり外れ確認の「最初の一周」に向く",
        ],
    ),
    dict(
        id="chatterbox-multilingual-v3", name="Chatterbox Multilingual V3", method="クローン",
        tagline="感情の誇張度を数値制御できるクローン型。日本語では荒れやすく選抜前提",
        stats=[("VRAM", "実測ピーク 4.0GB"), ("速度", "中速"), ("透かし", "PerTh自動埋込")],
        setup=[
            "公式pipパッケージ1つで完結。重み (ResembleAI/chatterbox) は初回自動DL",
            "見本音声 (5〜10秒・雑音なしのWAV) を1つ用意する",
        ],
        setup_code=[
            "pip install chatterbox-tts",
            f"{G}# Python{E}",
            "import torchaudio as ta",
            "from chatterbox.mtl_tts import ChatterboxMultilingualTTS",
            "m = ChatterboxMultilingualTTS.from_pretrained(device=\"cuda\", t3_model=\"v3\")",
            "wav = m.generate(\"いらっしゃいませ！\", language_id=\"ja\",",
            f"{S4}audio_prompt_path=\"voice.wav\", exaggeration=0.5, cfg_weight=0.5)",
            "ta.save(\"out.wav\", wav, m.sr)",
        ],
        inputs=[
            "<b>見本音声 (audio_prompt_path) が声質のすべて</b>。目的の演技トーンに近い5〜10秒を役ごとに1つ固定",
            "<b>language_id=\"ja\" を必ず明示</b> — 多言語モデルのため、落とすと英語訛りになる",
        ],
        tuning=[
            "<b>exaggeration</b> (感情誇張。控えめ0.4〜派手0.8目安) と <b>cfg_weight</b> (0.5) が主ノブ。上げるほど演技は派手だが崩壊率も上がる",
            "日本語では<b>声質崩壊・棒読み化・誤読が一定率で発生</b> (実測)。シードを変えた複数生成+聴き比べで選ぶ前提",
            "悪化時はパラメータより先に見本を疑う (無音・残響・トーン不一致)",
        ],
        record=[
            "公開161/161行 (自動選抜)。日本語での声線崩壊・誤読は診断継続中 — 公開版にも品質注記バッジあり",
            "同一見本でも台詞により崩れ方が変わることを確認。数打ち選抜の効果が最も大きいモデル",
        ],
    ),
    dict(
        id="cosyvoice3-0.5b-2512", name="CosyVoice 3 (0.5B)", method="クローン",
        tagline="指示文と読み指定を併用できるクローン型。平均点が安定、ただし非決定",
        stats=[("VRAM", "実測ピーク 5.3GB"), ("速度", "中速"), ("性質", "同条件でも毎回変わる")],
        setup=[
            "公式リポジトリをclone (--recursive必須) し requirements を導入 (公式はPython 3.10のconda/venv)",
            "重み (Fun-CosyVoice3-0.5B-2512) を HuggingFace / ModelScope からDL",
        ],
        setup_code=[
            "git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git; cd CosyVoice",
            "pip install -r requirements.txt",
            f"{G}# Python — 重みDL→ zero-shot クローン{E}",
            "from huggingface_hub import snapshot_download",
            "snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir='pretrained_models/cv3')",
            "from cosyvoice.cli.cosyvoice import AutoModel; import torchaudio",
            "cosy = AutoModel(model_dir='pretrained_models/cv3')",
            "for i, j in enumerate(cosy.inference_zero_shot('いらっしゃいませ！',",
            f"{S4}'見本音声の書き起こし&lt;|endofprompt|&gt;', 'voice.wav', stream=False)):",
            f"{S4}torchaudio.save(f'out_{{i}}.wav', j['tts_speech'], cosy.sample_rate)",
        ],
        inputs=[
            "<b>見本音声 + prompt文の二段構え</b>。prompt先頭の指示文 (「怒って強く」等の短い日本語) で感情・話し方を足せる (inference_instruct2)",
            "<b>読みはかな表記で明示可能</b> — 誤読しやすい語だけかな指定 (全文かな化はアクセント崩れの元)",
        ],
        tuning=[
            "<b>seedを固定しても出力が毎回変わる (非決定)</b> — 再現性は期待せず、複数生成から選抜する前提で回す",
            "speed引数あり。fp16で十分な品質",
            "指示文を長くしても効果は頭打ち。感情は「指示文+見本のトーン」の組合せで作る方が効く",
        ],
        record=[
            "公開161/161行。初回ランの生成失敗2行は再生成で解消",
            "同一seed・同一入力で出力が変わる非決定性を実測で確認済み (生成順の影響ではない)",
        ],
    ),
    dict(
        id="gpt-sovits-v2-pro-plus", name="GPT-SoVITS v2ProPlus", method="クローン",
        tagline="数分の音声で追加学習できる唯一の選択肢。自社収録と組む正式起用の本命",
        stats=[("VRAM", "実測ピーク 1.8GB"), ("速度", "中速"), ("強み", "few-shot追加学習")],
        setup=[
            "<b>公式Windows整合包 (ビルド済みzip)</b> が最速 — GitHub (RVC-Boss/GPT-SoVITS) のReleasesからDLして展開するだけ。Python環境構築が不要",
            "見本音声を用意: <b>3〜10秒のクリップ + その書き起こしテキスト</b> のペア",
        ],
        setup_code=[
            f"{G}# 公式Windows整合包での手順 (コード不要){E}",
            f"1. Releases から GPT-SoVITS-v2ProPlus のzipをDL・展開",
            f"2. {K}go-webui.bat{E} を実行 → ブラウザでWebUIが開く",
            f"3. 「1-GPT-SoVITS-TTS」→「1C-推論」→ 推論WebUIを起動",
            f"4. 参照音声 (3〜10秒) + その書き起こし + 生成したいテキストを入れて合成",
            f"{G}# Linux/Mac やAPI利用は公式README の install.sh / api_v2.py を参照{E}",
        ],
        inputs=[
            "<b>見本の切り出し品質が最重要</b>: 先頭末尾の無音を除去・1文で完結・書き起こしと完全一致。ここが雑だと全出力が濁る",
            "感情の専用入力はない — 感情は<b>見本自体の演技トーン</b>で与える (感情別の見本を役ごとに揃えると強い)",
        ],
        tuning=[
            "<b>temperature / top_k / top_p</b> が主ノブ。棒読み気味なら temperature を少し上げる",
            "<b>正式起用では few-shot 追加学習が本命</b>: WebUIの学習タブから数分〜数十分の収録で専用モデル化でき、ゼロショットより一貫性が大きく向上",
            "VRAM 2GB弱と最軽量クラス — 他モデルと並行運用しやすい",
        ],
        record=[
            "公開161/161行。見本の書き起こし精度が出力の明瞭さに直結することを確認",
            "全モデル中最軽量 (実測1.8GB)。ローカル量産の回転役として安定",
        ],
    ),
    dict(
        id="irodori-tts-600m-v3-voicedesign", name="Irodori-TTS v3 (600M VoiceDesign)", method="テキスト指示",
        tagline="日本語特化のテキスト指示型。声質の文章指定がよく効くが、指示文設計に規律が要る",
        stats=[("VRAM", "実測ピーク 6.0GB"), ("速度", "中速"), ("透かし", "SilentCipher自動埋込")],
        setup=[
            "公式リポジトリ (Aratako/Irodori-TTS) をcloneし、uv で環境同期 (CUDAバージョン別のextraあり)",
            "重み・日本語コーデックは初回に HuggingFace から自動DL",
        ],
        setup_code=[
            "git clone https://github.com/Aratako/Irodori-TTS.git; cd Irodori-TTS",
            f"uv sync --extra cu128  {G}# CUDA 12.8の場合。READMEのextra一覧参照{E}",
            "uv run --no-sync python infer.py `",
            f"{S2}--hf-checkpoint Aratako/Irodori-TTS-600M-v3-VoiceDesign `",
            f"{S2}--text \"いらっしゃいませ！\" --no-ref `",
            f"{S2}--caption \"若い女性。明るく張りのある声。\" --output-wav out.wav",
        ],
        inputs=[
            "<b>caption (声の説明文)</b>: 性別・年齢・声質を必須情報として短く列挙。<b>形容を盛った長文は禁止</b> — 男性役が女性声化する実測根因",
            "台詞ごとに変えるのは感情表現だけ。<b>役の声を固定したいときは、一度生成した音声を見本 (--ref-wav) にして使い回す</b>",
            "読み指定に対応。誤読語のみ最小指定",
        ],
        tuning=[
            "<b>cfg / num_steps</b> が品質ノブ。stepsを下げると速いが子音が甘くなる",
            "<b>演技過剰・母音伸びの傾向</b> (実測)。落ち着いた台詞はcaptionの感情表現を一段弱めると自然",
            "seed固定で決定論的 — 数打ちはseed違いで。崩れたらcaption短縮→見本固定の順で直す",
        ],
        record=[
            "公開161/161行。当初は長captionで男性27役中10役が女性声化 → <b>caption短縮で解決</b> (男性役median F0 180Hz超→110〜155Hz)",
            "演技過剰傾向は聴取で確認。全体設定の変更はブラインド比較で棄却し、台詞単位の調整で対応",
        ],
    ),
    dict(
        id="irodori-tts-v4-small", name="Irodori-TTS v4-Small (766M)", method="テキスト指示",
        tagline="caption生成とクローンの統一モデル。v3の男性声問題が改善、見本対応が最強 (計120秒)",
        stats=[("VRAM", "実測ピーク 4.0GB"), ("速度", "実測RTF 0.60"), ("透かし", "SilentCipher自動埋込")],
        setup=[
            "セットアップはv3と同じ公式リポジトリ。checkpoint名を v4-Small に変えるだけ",
            "Windowsは <b>FFmpeg共有ライブラリ版</b> が必要 (依存のtorchcodecが要求。winget: Gyan.FFmpeg.Shared → binをPATHへ)",
        ],
        setup_code=[
            "git clone https://github.com/Aratako/Irodori-TTS.git; cd Irodori-TTS",
            "uv sync --extra cu128",
            "uv run --no-sync python infer.py `",
            f"{S2}--hf-checkpoint Aratako/Irodori-TTS-v4-Small `",
            f"{S2}--text \"いらっしゃいませ！\" --ref-wav voice.wav `",
            f"{S2}--caption \"明るく張りのある接客の声。\" --output-wav out.wav",
            f"{G}# 見本なし生成は --ref-wav の代わりに --no-ref{E}",
        ],
        inputs=[
            "<b>captionはv3と同じ規律</b> (短く・固定・必須情報のみ)。v4は男性役の遵守が大きく改善 (実測53役全て機械検査合格)",
            "<b>見本音声は複数クリップを合計120秒まで連結可能</b> — 長い見本ほどクローンが安定する設計。単発5秒だけならv3系より弱い",
        ],
        tuning=[
            "<b>num_steps 40 / cfg</b> (caption・text別スケール) が既定で安定",
            "クローン品質を上げたいときは<b>見本を足して長くする</b>のが第一手 (パラメータ調整より効く)",
            "読み指定は非対応 — 誤読はセリフの表記側で回避 (かな交じり表記など)",
        ],
        record=[
            "公開161/161行・品質注記0件。役の声53件が修正済み検査で<b>一発全役合格</b> (v3は26役が要再試行だった)",
            "644テイクの量産ランを完走 (音声時間加重RTF 0.596)。現時点で総合最有力の一角",
        ],
    ),
    dict(
        id="qwen3-tts-12hz-1.7b", name="Qwen3-TTS (12Hz 1.7B)", method="テキスト指示",
        tagline="説明文で話し方まで細かく書ける多言語モデル。声の固定運用が生命線",
        stats=[("VRAM", "実測ピーク 5.8GB"), ("速度", "中速"), ("構成", "VoiceDesign+Baseの2段")],
        setup=[
            "公式pipパッケージ1つで完結。VoiceDesign (声を作る) と Base (見本でクローン) の2モデル構成",
            "重みは初回に HuggingFace から自動DL",
        ],
        setup_code=[
            "pip install -U qwen-tts",
            f"{G}# Python — ①説明文で声を生成 → ②その音声を見本にBaseで量産{E}",
            "import torch, soundfile as sf",
            "from qwen_tts import Qwen3TTSModel",
            "m = Qwen3TTSModel.from_pretrained(\"Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign\",",
            f"{S4}device_map=\"cuda:0\", dtype=torch.bfloat16)",
            "wavs, sr = m.generate_voice_design(text=\"いらっしゃいませ！\", language=\"Japanese\",",
            f"{S4}instruct=\"若い女性。明るく張りのある接客の声。\")",
            "sf.write(\"anchor.wav\", wavs[0], sr)",
            f"{G}# ②: Base model の generate_voice_clone(ref_audio=\"anchor.wav\", ...) で全セリフ生成{E}",
        ],
        inputs=[
            "<b>VoiceDesignで役の声を1回だけ作り、その音声をBaseの見本 (ref_audio+ref_text) にして全セリフを生成</b>する2段運用が必須。台詞ごとに声を作り直すと別人化する (実測で確定した失敗パターン)",
            "声の説明文 (instruct) には<b>性別・年齢・役柄を必ず入れる</b>。感情・話し方は台詞ごとの指示で変える",
        ],
        tuning=[
            "<b>感情ごとに見本を変えない</b> — 話者同一性が崩れる。感情は指示文だけで付ける",
            "sampling系は既定で安定。荒れたらまず見本 (anchor) の質を疑い、作り直す",
            "多言語混在の台詞に強い — 外国語訛りの役はこのモデルが第一候補",
        ],
        record=[
            "公開161/161行。旧実装に「見本を渡したつもりで破棄」のバグがあり、生成条件の記録検証で発見→修正した経緯あり",
            "2段運用の確立後は話者同一性が安定。感情別見本の旧方式はブラインド評価で不採用",
        ],
    ),
    dict(
        id="voxcpm2", name="VoxCPM 2", method="テキスト指示",
        tagline="人外 (精霊・ゴブリン・機械) の声設計が得意。VRAM食いで音質ばらつき大",
        stats=[("VRAM", "実測ピーク 11.0GB"), ("速度", "中速"), ("性質", "非決定・数打ち前提")],
        setup=[
            "公式pipパッケージ1つで完結。重み (openbmb/VoxCPM2) は初回自動DL",
            "<b>VRAMを12GB近くまで使う</b> (実測11GB) — 他のGPUアプリを閉じてから実行",
        ],
        setup_code=[
            "pip install voxcpm",
            f"{G}# Python{E}",
            "import soundfile as sf",
            "from voxcpm import VoxCPM",
            "m = VoxCPM.from_pretrained(\"openbmb/VoxCPM2\", load_denoiser=False)",
            "wav = m.generate(text=\"いらっしゃいませ！\", cfg_value=2.0,",
            f"{S4}inference_timesteps=10, seed=42)",
            "sf.write(\"out.wav\", wav, m.tts_model.sample_rate)",
            f"{G}# クローンは prompt_wav_path=\"voice.wav\", prompt_text=\"見本の書き起こし\" を追加{E}",
        ],
        inputs=[
            "<b>2通りの声の作り方</b>: ①見本なし生成 (テキストの文体・指示から声を設計) ②見本音声+書き起こしでクローン。人外役は①、人間役は②が安定",
            "感情・話し方は英語の短い命令形で付ける (例: \"Speak strongly shouting and loud.\")",
        ],
        tuning=[
            "<b>cfg_value 2.0 / inference_timesteps 10</b> が既定。timestepsを上げると音質は締まるが遅くなる",
            "<b>非決定 + 音質ばらつき大</b> — 複数生成からの選抜が前提。retry_badcase を有効にしておく",
            "囁き・息声・超低音などの極端な声は全モデル中で最も出せる。まず人外役で試すと強みが分かる",
        ],
        record=[
            "公開161/161行。精霊・ゴブリン等の人外役の極端な声域再現は全モデル中最良クラス",
            "VRAM実測11GBで12GB機の上限に近い。量産時は単独実行が前提",
        ],
    ),
]

FOOT = ("VRAM・成績などの数値は gaya-bench (gaya-bench.pages.dev) の公開1,449音声とその生成記録による実測値。"
        "量産パイプライン全体と権利・透かしの注意は docs/production-adoption-guide.pdf を参照。")

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
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(stats)

    story.extend(section("1", "0からのセットアップと試し生成", "公式配布物のみで完結"))
    for t in mspec["setup"]:
        story.append(LI(t))
    story.append(Spacer(1, 2))
    story.append(codeblock(mspec["setup_code"]))

    story.extend(section("2", "入力の用意", "このモデルに何を渡すか"))
    for t in mspec["inputs"]:
        story.append(LI(t))

    story.extend(section("3", "最適化アドバイス", "実測で効いた順"))
    for t in mspec["tuning"]:
        story.append(LI(t))

    story.extend(section("4", "本ベンチでの成績", "gaya-bench.pages.dev で全音声を試聴可"))
    for t in mspec["record"]:
        story.append(LI(t))

    story.append(Spacer(1, 9))
    story.append(P(FOOT, small))
    doc.build(story)
    print("OK", fname)
