"""
sheets_reporter.py
------------------
GEO モニタリング SaaS -- Google Sheets 出力モジュール (Sprint 3)

責務:
  - 言及分析結果を既存の Google Sheets KPI トラッキングフォーマットで書き出す
  - シート構成:
      [1] KPI サマリー  : AI別・月別の言及率推移（既存 Excel KPI シートに相当）
      [2] クエリ詳細    : クエリ×AI の全回答ログ（ドリルダウン用）
      [3] 誤情報アラート: misinformation_flag=True の行だけ抜粋

前提:
  - Google Sheets API を有効化済み
  - サービスアカウント JSON (GOOGLE_SERVICE_ACCOUNT_JSON) を環境変数に設定
  - スプレッドシートにサービスアカウントのメールを「編集者」で共有済み

使い方:
    reporter = SheetsReporter.from_env(spreadsheet_id="1ABC...")
    reporter.write_kpi_summary(mention_rate_by_provider, year=2026, month=7)
    reporter.write_query_detail(analysis_rows)
    reporter.write_misinformation_alerts(analysis_rows)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import gspread
from gspread.exceptions import WorksheetNotFound
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

# Google API スコープ
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# シート名
SHEET_KPI      = "KPIサマリー"
SHEET_DETAIL   = "クエリ詳細"
SHEET_ALERT    = "誤情報アラート"

# KPI サマリーシートの列ヘッダー
KPI_HEADERS = [
    "年月", "AIプロバイダー",
    "言及率 (%)", "言及クエリ数", "総クエリ数",
    "ポジティブ率 (%)", "ニュートラル率 (%)", "ネガティブ率 (%)",
    "更新日時",
]

# クエリ詳細シートの列ヘッダー
DETAIL_HEADERS = [
    "取得日時", "AIプロバイダー", "カテゴリ", "クエリ",
    "言及あり", "言及タイプ", "センチメント", "判定根拠",
    "引用URL", "競合言及", "誤情報フラグ", "誤情報詳細",
    "言及抜粋",
]

# 誤情報アラートシートの列ヘッダー
ALERT_HEADERS = [
    "検出日時", "AIプロバイダー", "カテゴリ", "クエリ",
    "誤情報詳細", "言及抜粋", "取得日時",
]


class SheetsReporter:
    """Google Sheets への GEO モニタリング結果書き出しクラス"""

    def __init__(self, gc: gspread.Client, spreadsheet_id: str):
        self.gc = gc
        self.spreadsheet_id = spreadsheet_id
        self._ss: gspread.Spreadsheet | None = None

    # ── ファクトリ ─────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, spreadsheet_id: str) -> "SheetsReporter":
        """
        環境変数 GOOGLE_SERVICE_ACCOUNT_JSON からサービスアカウントを読み込み
        SheetsReporter を生成する。

        Args:
            spreadsheet_id: Google Sheets の URL 末尾の ID
                            (例: "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms")
        """
        sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sa_json:
            raise EnvironmentError("GOOGLE_SERVICE_ACCOUNT_JSON が設定されていません")

        # ファイルパス形式（例: ./credentials/service_account.json）と
        # JSON 文字列形式（{"type":"service_account",...}）の両方に対応
        if sa_json.strip().startswith("{"):
            sa_info = json.loads(sa_json)
        else:
            with open(sa_json, encoding="utf-8") as f:
                sa_info = json.load(f)

        creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
        gc = gspread.authorize(creds)
        return cls(gc=gc, spreadsheet_id=spreadsheet_id)

    # ── パブリック API ─────────────────────────────────────────────────────────

    def write_kpi_summary(
        self,
        mention_rate_by_provider: dict[str, dict],
        year: int,
        month: int,
        sentiment_stats: dict[str, dict] | None = None,
    ) -> None:
        """
        KPI サマリーシートに月次言及率を追記する。

        Args:
            mention_rate_by_provider:
                MentionSaver.fetch_mention_rate() の戻り値
                {"chatgpt": {"mention_rate": 0.75, "total": 28, "mentioned": 21}, ...}
            year: 対象年
            month: 対象月
            sentiment_stats:
                {"chatgpt": {"positive": 10, "neutral": 8, "negative": 3}, ...}
                None の場合は空欄出力
        """
        ws = self._get_or_create_sheet(SHEET_KPI, KPI_HEADERS)

        period_label = f"{year}/{month:02d}"
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rows: list[list[Any]] = []

        for provider in ["chatgpt", "gemini", "perplexity", "ai_overview"]:
            stats = mention_rate_by_provider.get(provider, {})
            rate    = stats.get("mention_rate", 0.0)
            total   = stats.get("total", 0)
            mentioned = stats.get("mentioned", 0)

            sent = (sentiment_stats or {}).get(provider, {})
            pos_rate = _pct(sent.get("positive", 0), mentioned)
            neu_rate = _pct(sent.get("neutral", 0), mentioned)
            neg_rate = _pct(sent.get("negative", 0), mentioned)

            rows.append([
                period_label,
                _provider_label(provider),
                round(rate * 100, 1),
                mentioned,
                total,
                pos_rate,
                neu_rate,
                neg_rate,
                now_str,
            ])

        ws.append_rows(rows, value_input_option="USER_ENTERED")
        logger.info("KPIサマリー書き込み完了: %s (%d行)", period_label, len(rows))

    def write_query_detail(self, analysis_rows: list[dict]) -> None:
        """
        クエリ詳細シートに分析結果を全件書き出す（既存行を上書き）。

        Args:
            analysis_rows: MentionSaver.fetch_recent_analysis() の戻り値
        """
        ws = self._get_or_create_sheet(SHEET_DETAIL, DETAIL_HEADERS)
        ws.clear()
        ws.append_row(DETAIL_HEADERS, value_input_option="USER_ENTERED")

        rows: list[list[Any]] = []
        for row in analysis_rows:
            competitor_str = _format_competitors(row.get("competitor_mentions") or [])
            cited_str = ", ".join(row.get("cited_urls") or [])
            rows.append([
                _fmt_dt(row.get("retrieved_at")),
                _provider_label(row.get("ai_provider", "")),
                row.get("category", ""),
                row.get("prompt_text", ""),
                "あり" if row.get("is_mentioned") else "なし",
                row.get("mention_type", "none"),
                _sentiment_label(row.get("sentiment", "")),
                row.get("sentiment_reason", ""),
                cited_str,
                competitor_str,
                "⚠️ 要確認" if row.get("misinformation_flag") else "",
                row.get("misinformation_detail", ""),
                row.get("raw_mention_excerpt", ""),
            ])

        if rows:
            ws.append_rows(rows, value_input_option="USER_ENTERED")
        logger.info("クエリ詳細書き込み完了: %d 件", len(rows))

    def write_misinformation_alerts(self, analysis_rows: list[dict]) -> None:
        """
        誤情報アラートシートに misinformation_flag=True の行のみ書き出す。
        """
        ws = self._get_or_create_sheet(SHEET_ALERT, ALERT_HEADERS)
        ws.clear()
        ws.append_row(ALERT_HEADERS, value_input_option="USER_ENTERED")

        alert_rows = [r for r in analysis_rows if r.get("misinformation_flag")]
        rows: list[list[Any]] = []
        for row in alert_rows:
            rows.append([
                _fmt_dt(row.get("analyzed_at")),
                _provider_label(row.get("ai_provider", "")),
                row.get("category", ""),
                row.get("prompt_text", ""),
                row.get("misinformation_detail", ""),
                row.get("raw_mention_excerpt", ""),
                _fmt_dt(row.get("retrieved_at")),
            ])

        if rows:
            ws.append_rows(rows, value_input_option="USER_ENTERED")

        if alert_rows:
            logger.warning("誤情報アラート: %d 件を書き込みました", len(alert_rows))
        else:
            logger.info("誤情報アラート: 該当なし")

    def spreadsheet_url(self) -> str:
        """スプレッドシートの URL を返す"""
        return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"

    # ── private ──────────────────────────────────────────────────────────────

    def _spreadsheet(self) -> gspread.Spreadsheet:
        if self._ss is None:
            self._ss = self.gc.open_by_key(self.spreadsheet_id)
        return self._ss

    def _get_or_create_sheet(
        self, title: str, headers: list[str]
    ) -> gspread.Worksheet:
        """シートが存在しなければ作成してヘッダーを書き込む。"""
        ss = self._spreadsheet()
        try:
            ws = ss.worksheet(title)
        except WorksheetNotFound:
            ws = ss.add_worksheet(title=title, rows=1000, cols=len(headers))
            ws.append_row(headers, value_input_option="USER_ENTERED")
            logger.info("シート作成: %s", title)
        return ws


# ─────────────────────────────────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────────────────────────────────

def _pct(count: int, total: int) -> float:
    return round(count / total * 100, 1) if total else 0.0


def _fmt_dt(dt: Any) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)


def _provider_label(provider: str) -> str:
    return {
        "chatgpt":     "ChatGPT",
        "gemini":      "Gemini",
        "perplexity":  "Perplexity",
        "ai_overview": "AI Overview",
    }.get(provider, provider)


def _sentiment_label(sentiment: str) -> str:
    return {
        "positive":       "ポジティブ",
        "neutral":        "ニュートラル",
        "negative":       "ネガティブ",
        "not_applicable": "対象外",
    }.get(sentiment, sentiment)


def _format_competitors(competitor_mentions: list[dict]) -> str:
    mentioned = [
        cm["name"] for cm in competitor_mentions
        if isinstance(cm, dict) and cm.get("is_mentioned")
    ]
    return ", ".join(mentioned) if mentioned else ""
