# CLAUDE.md

このリポジトリには 2 つのツールがある。

- GEO モニタリング（`run_pipeline.py`, `dashboard.py` ほか）
- 会計管理ダッシュボード（`accounting/`, `accounting_dashboard.py`）— 詳細は `ACCOUNTING.md`

## 会計管理: 自然言語での依頼の処理

ユーザーが受注・失注・案件登録・金額変更・請求書取込・月次確定などを話しかけてきたら、
`python -m accounting.cli` を実行して DB を更新する（DB 直接編集・SQL 手書きはしない）。

1. まず `python -m accounting.cli --json deal list` で既存案件を確認し、対象を案件コードで特定する。
   - 顧客名が一致する進行中の案件が 1 件ならそれを使う。該当なしなら `deal add` で新規登録
     （受注報告なら `--status 受注`、失注報告なら `--status 失注`）。
   - 候補が複数で決められないときは実行せずユーザーに確認する。
2. 金額は税抜・円の整数。「300万」→ `3000000`。税込と明示されたら 1.1 で割る。
   「月額25万×6ヶ月」→ `--type 月額 --amount 250000 --months 6`。
3. 開始月・納品月は `--start YYYY-MM`（計上開始月）。「来月」などは今日の日付から計算する。
4. 実行後、案件コード・顧客・金額・ステータスを 1〜3 行で報告する。

| 依頼の例 | コマンド |
|---|---|
| ABC商事のサイト改修 300万で受注、11月納品 | `deal won D-2026-001 --amount 3000000 --start 2026-11`（既存）/ `deal add --client ABC商事 --title サイト改修 --amount 3000000 --start 2026-11 --status 受注`（新規） |
| XYZの広告運用は失注、予算凍結 | `deal lost D-2026-002 --reason 予算凍結` |
| みらいのSEO 確度70%に | `deal update D-2026-003 --probability 70` |
| 請求書を読み込んで | `invoices scan` |
| 先月の売上を確定して | `status YYYY-MM` で請求漏れ・要確認を確認して報告 → `close YYYY-MM` |
| 10月の目標は500万 | `target 2026-10 5000000` |

- `close` は要確認の請求書が残っていると失敗する。勝手に `--allow-review` を付けず、内容を報告してユーザーに判断を仰ぐ。
- 確定済みの月を直すときは `reopen YYYY-MM --reason ...` → 修正 → `close`。
- ANTHROPIC_API_KEY がある環境では `ask "<依頼文>"` でも同じ処理ができる（ダッシュボードのアシスタントタブと同じ）。
