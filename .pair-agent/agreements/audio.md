---
sprint_id: 9b97d282-a35d-4036-b0d0-086bf2e1e4de
version: 2
status: completed
created_at: 2026-11-08T05:00:00+09:00
updated_at: 2026-11-08T05:00:00+09:00
domain_tags: [python-sdk, audio, tts, stt]
change_count_premise: 1
change_count_improvement: 0
---

# スプリントゴール

AudioCapability を、音声合成は edge-tts、音声認識は faster-whisper で実装する。

## 師匠の指示（原文 2026-11-08）

- 「OK.進めてください」→ 調査結果をもとに Audio を提案
- 構成の選択: 「合成: Gemini TTS (Recommended)」「認識: faster-whisper (Recommended)」
- 直後に訂正: 「取り消し、TTSはMnemoと同じEdgeTTSにしてほしい。mnemo-slideで採用している。」（前提の変更: change_count_premise=1）
- 形: 「edge-tts ライブラリを直接 (Recommended)」（mnemo-slide は別プロジェクト EdgeTTS の MCP 経由だが、この Mac には無い）

## Mnemo（mnemo-slide）から揃えるもの（読んだだけ）

- 既定の声 ja-JP-NanamiNeural、話速 -50〜+100%、声の高さ -50〜+50Hz
- 認識: faster-whisper の medium・CPU・int8（Mnemo の core/transcribe.py）

## タスク

- [x] EdgeTTSAudioAdapter（"edge-tts"）・FasterWhisperAudioAdapter（"faster-whisper"）・束ねる部品
- [x] テスト・live・ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| A-01 | エンジンごとにアダプターを分け、AudioRouter で束ねる | 命名ルール（エンジン名） |
| A-02 | 合成は MP3 のみ（他の形式は Unsupported） | 実測で edge-tts は MP3。変換には ffmpeg が要る |
| A-03 | speak の voice の既定を None に | "alloy" は OpenAI 固有 |
| A-04 | 結果に segments / language_probability を追加 | faster-whisper が返す有用な情報 |
| A-05 | CI には [audio] を入れない（単体テストは偽物で行う） | ctranslate2 / av のバイナリと 1.5GB のモデルを CI で避ける |
| A-06 | 前スプリントの MCP のコメントに残っていた「Mnemo」を削除 | confirmed ルール違反だった |

## 変更ログ

- v1 [2026-11-08]: 初版
- v2 [2026-11-08]: 実装・live 確認（日本語の合成 → 認識の往復）
- 師匠（原文）: 「了解。Ollamaの調査から開始してください」→ スプリント完了
