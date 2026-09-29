# 会計管理ダッシュボード

案件管理表・売上見込・請求書の自動読込・月次の売上実績確定を 1 つにまとめたツール。
データはローカルの SQLite（既定 `data/accounting.db`）に保存する。

## できること

| 機能 | 内容 |
|---|---|
| 自然言語での操作 | 「ABC商事のサイト改修 300万で受注、11月納品」「XYZは失注、予算凍結」などを話しかけると案件管理表が更新される。ダッシュボードの「アシスタント」タブ、`python -m accounting.cli ask "..."`、または Claude Code に直接話しかける（`CLAUDE.md` の手順で CLI を実行する） |
| 案件管理表 | 見込 / 提案中 / 受注 / 失注 / 完了。一括・月額（×月数）、計上開始月、確度、担当、失注理由。表上で直接編集できる |
| 請求書の自動読込 | `INVOICE_DIR` の PDF / Excel / txt を読み、請求先・請求日・対象月・税抜/消費税/税込を抽出して案件に自動で紐付ける。取込済みファイルはスキップ |
| 月次確定 | 対象月の請求書を集計して売上実績を確定・ロック。確定前に「要確認の請求書」「請求漏れ候補（計上予定があるのに請求書がない受注案件）」をチェックする。最終月まで請求済みの受注案件は自動で「完了」にする |
| 自動実行 | 毎月 3 日に「請求書取込 → 前月確定」を自動実行（Windows タスクスケジューラ） |
| 概要 | 前月確定売上・当月見込・年度累計・パイプライン、月次推移（確定実績 / 受注見込 / 加重パイプライン / 目標） |

## セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env   # INVOICE_DIR と ANTHROPIC_API_KEY を設定
streamlit run accounting_dashboard.py
```

月次の自動確定を登録する（Windows）:

```bat
setup_accounting_scheduler.bat
```

`run_monthly_close.bat` が毎月 3 日 9:00 に `python -m accounting.cli monthly` を実行し、
結果は `logs/monthly_close.log` に出力される。要確認の請求書があるときは確定せずに終了する
（ダッシュボードの「請求書」タブで修正 →「月次確定」タブで確定）。

## 請求書の読み取り

- `ANTHROPIC_API_KEY` がある場合は Claude が PDF をそのまま読む（スキャン PDF も可）。
  ない場合・API エラー時は正規表現で読み取る（テキスト PDF / Excel / txt）。
- 計上月: 請求書に「◯月分」があればその月。なければ `INVOICE_BILLING_MONTH_RULE`
  （`issue_month` = 請求日の月、`previous_month` = 請求日の前月）。
- 請求先の会社名（株式会社などを除いて比較）で案件を突合する。案件がなくても売上には含まれる。
- 金額・計上月・請求先が読めなかった請求書は「要確認」になり、修正するまで確定できない。

## CLI

```bash
python -m accounting.cli deal list
python -m accounting.cli deal add --client "ABC商事" --title "サイト改修" --amount 3000000 --start 2026-11
python -m accounting.cli deal won D-2026-001
python -m accounting.cli deal lost D-2026-002 --reason "予算凍結"
python -m accounting.cli invoices scan
python -m accounting.cli status 2026-09     # 確定前チェック
python -m accounting.cli close 2026-09      # 確定
python -m accounting.cli reopen 2026-09 --reason "請求書差し替え"
python -m accounting.cli monthly            # 取込 + 前月確定
python -m accounting.cli ask "みらいのSEO支援 月額25万×6ヶ月 10月開始で受注"
```
