"""
run_pipeline.py
---------------
GEO モニタリング SaaS -- フルパイプライン実行スクリプト (Sprint 3)

フロー:
    1. QueryRunner  : DB からクエリを取得し、4つのAI APIに投げて ai_responses に保存
    2. MentionDetector : Claude API で言及判定
    3. MentionSaver : 判定結果を mention_analysis テーブルに保存
    4. SheetsReporter  : Google Sheets にKPIサマリー・詳細・誤情報アラートを書き出す

実行方法:
    # .env ファイルに環境変数を記載 (load_dotenv で自動読み込み)
    python run_pipeline.py

    # クライアント指定:
    python run_pipeline.py --client-id a1b2c3d4-0000-0000-0000-000000000001

    # プロバイダー絞り込み（テスト用）:
    python run_pipeline.py --providers chatgpt gemini

    # Sheets 出力をスキップ:
    python run_pipeline.py --no-sheets

.env ファイル例:
    ANTHROPIC_API_KEY=sk-ant-...
    OPENAI_API_KEY=sk-...
    GEMINI_API_KEY=AIza...
    PERPLEXITY_API_KEY=pplx-...
    SERPAPI_KEY=...
    DATABASE_URL=postgresql://user:pass@localhost:5432/geo_saas
    GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
    GEO_SPREADSHEET_ID=1BxiMVs0XRA...
"""

from __future__ import annotations

# .env を最初に読み込む（他の import より前に実行）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv 未インストール時は環境変数を直接参照

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

import urllib.request
import json as _json

from query_runner import QueryRunner, RunnerConfig
from mention_detector import MentionDetector
from mention_saver import MentionSaver, AnalysisRecord
from sheets_reporter import SheetsReporter


def _send_slack(webhook_url: str, summary: dict) -> None:
    """パイプライン実行サマリーをSlackに送信する。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    misinfo = summary.get("misinformation_count", 0)
    sheets_url = summary.get("spreadsheet_url", "")

    misinfo_line = f"⚠️ 誤情報検出: *{misinfo}件*" if misinfo > 0 else "✅ 誤情報: なし"

    text = (
        f"*📊 GEO モニタリング 実行完了* ({now})\n"
        f"• クエリ実行: {summary.get('queries_run', 0)}件 / 保存: {summary.get('responses_saved', 0)}件\n"
        f"• 言及分析: {summary.get('analyses_saved', 0)}件\n"
        f"• {misinfo_line}\n"
        f"• Sheets: {sheets_url}"
    )

    payload = _json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Slack通知送信完了")
            else:
                logger.warning("Slack通知失敗: status=%s", resp.status)
    except Exception as e:
        logger.warning("Slack通知エラー: %s", e)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# デフォルト設定（Dears Wedding）
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CLIENT_ID = "a1b2c3d4-0000-0000-0000-000000000001"

# ── クライアント別ブランド設定 ─────────────────────────────────────────────────
CLIENT_CONFIGS = {
    # Dears Wedding（メインクライアント）
    "a1b2c3d4-0000-0000-0000-000000000001": {
        "brand_keywords": [
            "Dears Wedding", "DEARS WEDDING", "dears wedding",
            "ディアーズウェディング", "ディアーズ ウェディング",
            "Dears", "ディアーズ", "dears-wedding.jp",
        ],
        "brand_domain": "dears-wedding.jp",
        "competitor_keywords": [
            "Arluis", "アルイス", "ワタベウエディング", "Watabe Wedding",
            "モノグラムチャペル", "ザ・グランドティアラ",
        ],
        "fact_sheet": {
            "ブランド正式名称": "Dears Wedding",
            "営業状況":        "通常営業中。予約受付中",
            "対応エリア":      "東京都内（ガーデンウェディング・少人数婚専門）",
            "専門領域":        "少人数ウェディング（20名以下）専門。大規模披露宴は非対応",
            "公式サイト":      "https://dears-wedding.jp",
        },
    },
    # Arluis（競合①）
    "a1b2c3d4-0000-0000-0000-000000000002": {
        "brand_keywords": [
            "Arluis", "ARLUIS", "arluis",
            "アルイス", "アルイスウェディング",
            "Arluis Wedding", "arluis.jp",
        ],
        "brand_domain": "arluis.jp",
        "competitor_keywords": [
            "Dears Wedding", "ディアーズウェディング",
            "ワタベウエディング", "Watabe Wedding",
        ],
        "fact_sheet": {
            "ブランド正式名称": "Arluis（アルイス）",
            "対応エリア":      "東京都内",
            "公式サイト":      "https://www.arluis.jp",
        },
    },
    # ワタベウエディング（競合②）
    "a1b2c3d4-0000-0000-0000-000000000003": {
        "brand_keywords": [
            "ワタベウエディング", "Watabe Wedding", "WATABE WEDDING",
            "watabe wedding", "ワタベ", "watabe-wedding.co.jp",
        ],
        "brand_domain": "watabe-wedding.co.jp",
        "competitor_keywords": [
            "Dears Wedding", "ディアーズウェディング",
            "Arluis", "アルイス",
        ],
        "fact_sheet": {
            "ブランド正式名称": "ワタベウエディング（Watabe Wedding）",
            "公式サイト":      "https://www.watabe-wedding.co.jp",
        },
    },
}

# 後方互換性のためのデフォルト値（旧コード参照用）
_default = CLIENT_CONFIGS[DEFAULT_CLIENT_ID]
BRAND_KEYWORDS       = _default["brand_keywords"]
BRAND_DOMAIN         = _default["brand_domain"]
COMPETITOR_KEYWORDS  = _default["competitor_keywords"]
FACT_SHEET           = _default["fact_sheet"]

ALL_CLIENT_IDS = list(CLIENT_CONFIGS.keys())


# ─────────────────────────────────────────────────────────────────────────────
# メインパイプライン
# ─────────────────────────────────────────────────────────────────────────────

def run(
    client_id: str,
    database_url: str,
    anthropic_api_key: str,
    spreadsheet_id: str | None,
    providers: list[str] | None = None,
    skip_sheets: bool = False,
) -> dict:
    """
    フルパイプラインを実行し、実行サマリーを返す。

    Returns:
        {
            "queries_run": N,
            "responses_saved": N,
            "analyses_saved": N,
            "misinformation_count": N,
            "sheets_updated": bool,
            "spreadsheet_url": str | None,
        }
    """
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    summary = {
        "queries_run": 0,
        "responses_saved": 0,
        "analyses_saved": 0,
        "misinformation_count": 0,
        "sheets_updated": False,
        "spreadsheet_url": None,
    }

    # ── Step 1: AI API へクエリ投入・回答を ai_responses に保存 ──────────────
    logger.info("=== Step 1: AI クエリ実行 ===")
    config = RunnerConfig(
        enabled_providers=providers or ["chatgpt", "gemini", "perplexity", "ai_overview"],
    )
    runner = QueryRunner.from_env(database_url, config)

    # on_result コールバックで取得した ai_response_id を収集する
    collected_responses: list[dict] = []

    def on_result(response, query_set_id: str) -> None:
        collected_responses.append({
            "ai_response_id": query_set_id,
            "response_text": response.response_text,
            "provider": response.provider,
        })

    runner.on_result = on_result
    stats = runner.run_client(client_id)
    summary["queries_run"] = stats.get("total", 0)
    summary["responses_saved"] = stats.get("success", 0)
    logger.info("Step 1 完了: %s", stats)

    if not collected_responses:
        logger.warning("取得できた回答が 0 件です。パイプラインを終了します。")
        return summary

    # ── Step 2: Claude API で言及判定 ────────────────────────────────────────
    logger.info("=== Step 2: 言及判定（Claude %s）===", MentionDetector.MODEL)
    detector = MentionDetector(api_key=anthropic_api_key)
    analysis_records: list[AnalysisRecord] = []

    # クライアント別ブランド設定を取得
    cfg = CLIENT_CONFIGS.get(client_id, CLIENT_CONFIGS[DEFAULT_CLIENT_ID])
    client_brand_keywords      = cfg["brand_keywords"]
    client_brand_domain        = cfg["brand_domain"]
    client_competitor_keywords = cfg["competitor_keywords"]
    client_fact_sheet          = cfg["fact_sheet"]

    for i, resp in enumerate(collected_responses, 1):
        logger.debug("[%d/%d] 判定中: provider=%s", i, len(collected_responses), resp["provider"])
        result = detector.analyze(
            response_text=resp["response_text"],
            brand_keywords=client_brand_keywords,
            brand_domain=client_brand_domain,
            competitor_keywords=client_competitor_keywords,
            fact_sheet=client_fact_sheet,
        )
        analysis_records.append(AnalysisRecord(
            ai_response_id=resp["ai_response_id"],
            result=result,
        ))
        if result.misinformation_flag:
            logger.warning(
                "⚠️ 誤情報検出 [%s]: %s", resp["provider"], result.misinformation_detail
            )
        time.sleep(0.3)

    logger.info("Step 2 完了: %d 件判定", len(analysis_records))

    # ── Step 3: 判定結果を mention_analysis テーブルへ保存 ───────────────────
    logger.info("=== Step 3: DB 保存 ===")
    saver = MentionSaver(database_url)
    saved_ids = saver.save_batch(analysis_records)
    summary["analyses_saved"] = len(saved_ids)
    summary["misinformation_count"] = sum(
        1 for r in analysis_records if r.result.misinformation_flag
    )
    logger.info("Step 3 完了: %d 件保存", len(saved_ids))

    # ── Step 4: Google Sheets に結果を書き出す ────────────────────────────────
    if skip_sheets or not spreadsheet_id:
        logger.info("Step 4: Sheets 出力スキップ")
        return summary

    logger.info("=== Step 4: Google Sheets 出力 ===")
    try:
        reporter = SheetsReporter.from_env(spreadsheet_id)

        mention_rate = saver.fetch_mention_rate(client_id, year, month)
        reporter.write_kpi_summary(mention_rate, year, month)

        detail_rows = saver.fetch_recent_analysis(client_id, limit=500)
        reporter.write_query_detail(detail_rows)
        reporter.write_misinformation_alerts(detail_rows)

        summary["sheets_updated"] = True
        summary["spreadsheet_url"] = reporter.spreadsheet_url()
        logger.info("Step 4 完了: %s", summary["spreadsheet_url"])

    except Exception as e:
        logger.error("Step 4 エラー（Sheets 出力失敗）: %s", e)

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI エントリポイント
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GEO モニタリング フルパイプライン")
    parser.add_argument(
        "--client-id", default=DEFAULT_CLIENT_ID,
        help="実行対象のクライアント ID（デフォルト: Dears Wedding）",
    )
    parser.add_argument(
        "--providers", nargs="+",
        choices=["chatgpt", "gemini", "perplexity", "ai_overview"],
        help="実行するプロバイダー（省略時は全4社）",
    )
    parser.add_argument(
        "--no-sheets", action="store_true",
        help="Google Sheets 出力をスキップ",
    )
    parser.add_argument(
        "--all-clients", action="store_true",
        help="全クライアント（Dears Wedding + 競合2社）を順番に実行",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    required_env = ["ANTHROPIC_API_KEY", "DATABASE_URL"]
    missing = [k for k in required_env if not os.environ.get(k)]
    if missing:
        print(f"エラー: 以下の環境変数が設定されていません: {', '.join(missing)}")
        print("  .env ファイルに記載するか、export コマンドで設定してください。")
        sys.exit(1)

    spreadsheet_id = os.environ.get("GEO_SPREADSHEET_ID")
    if not args.no_sheets and not spreadsheet_id:
        print("警告: GEO_SPREADSHEET_ID が未設定のため Sheets 出力をスキップします。")

    target_ids = ALL_CLIENT_IDS if args.all_clients else [args.client_id]
    any_success = False

    for cid in target_ids:
        brand_name = CLIENT_CONFIGS[cid]["fact_sheet"].get("ブランド正式名称", cid)
        logger.info("▶ クライアント: %s (%s)", brand_name, cid)

        result = run(
            client_id=cid,
            database_url=os.environ["DATABASE_URL"],
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            spreadsheet_id=spreadsheet_id,
            providers=args.providers,
            skip_sheets=args.no_sheets,
        )

        print("\n" + "=" * 55)
        print(f"  パイプライン実行完了: {brand_name}")
        print("=" * 55)
        for k, v in result.items():
            print(f"  {k}: {v}")
        if result.get("spreadsheet_url"):
            print(f"\n  Sheets: {result['spreadsheet_url']}")
        print("=" * 55)

        # Slack 通知
        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if slack_url:
            _send_slack(slack_url, {**result, "client": brand_name})

        if result["analyses_saved"] > 0:
            any_success = True

    sys.exit(0 if any_success else 1)
