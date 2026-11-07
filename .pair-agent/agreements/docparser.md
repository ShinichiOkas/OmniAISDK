---
sprint_id: 8a2cf451-7289-424c-bf8f-03f975585a05
version: 2
status: reviewing
created_at: 2026-11-08T02:00:00+09:00
updated_at: 2026-11-08T02:00:00+09:00
domain_tags: [python-sdk, document-parsing, rag, local]
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

DocumentParserCapability を Mnemo の標準（PDF は pypdf、Office / RTF は markitdown）で実装し、
「ファイル → テキスト → 埋め込み → 再順位付け」をキーなしで通す。

## 師匠の指示（原文 2026-11-08）

- 「次へ進んでください」→ おまかせで DocParser を選択（Mnemo の標準: markitdown[docx,xlsx,pptx,pdf]・pypdf）

## タスク

- [x] PyPDFDocumentParser（"pypdf"）・MarkItDownDocumentParser（"markitdown"）・MIME で振り分ける部品
- [x] 取り出せなかった理由を返す（Mnemo と同じ方針）・CJK 字形の正規化
- [x] テスト（実物の文書ファイル）・live・ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| D-01 | 次の領域に DocParser を選んだ | Mnemo に標準があり、RAG の入口がキーなしでそろう |
| D-02 | エンジンごとにアダプターを分け、MIME で振り分ける部品を足した | 命名ルール（エンジン名）。結果に実際のエンジン名が出る |
| D-03 | 取り出せない場合は例外でなく empty_reason | Mnemo の方針 |
| D-04 | CJK 字形の正規化を Mnemo と同じ範囲で取り込んだ | PDF の部首字形問題 |
| D-05 | mime_type の既定を None（拡張子から判定）に変更 | v3.0 の既定 "application/pdf" は Office で誤る |
| D-06 | CI に [docs] を入れた | 入れないとテストが全部スキップされる |
| D-07 | テスト用文書は生成スクリプトごとコミット | 再生成でき、中身が分かる |

## 変更ログ

- v1 [2026-11-08]: 初版
- v2 [2026-11-08]: 実装・確認（ローカル文書 RAG を live で）
