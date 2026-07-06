"""
mention_saver.py
----------------
GEO モニタリング SaaS -- mention_analysis テーブル保存モジュール (Sprint 3)

責務:
  - MentionDetector.analyze() の結果を mention_analysis テーブルに INSERT
  - ai_responses テーブルの ai_response_id を外部キーとして紐付け
  - バッチ処理対応（複数レコードを一括保存）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass

import psycopg2
from psycopg2.extras import Json, execute_values

from mention_detector import MentionResult

logger = logging.getLogger(__name__)


def _db_connect(database_url: str) -> psycopg2.extensions.connection:
    """URL を個別パラメーターに分解して接続する。
    %40 等の URL エンコードを正しくデコードし、Supabase の SSL にも対応。"""
    from urllib.parse import urlparse, unquote
    p = urlparse(database_url)
    is_supabase = "supabase.co" in database_url
    return psycopg2.connect(
        host=p.hostname,
        port=p.port or 5432,
        dbname=(p.path or "/postgres").lstrip("/"),
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        client_encoding="UTF8",
        sslmode="require" if is_supabase else "prefer",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 保存対象レコード
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AnalysisRecord:
    """DB に保存する 1 件分の言及分析結果"""
    ai_response_id: str          # ai_responses.id (UUID)
    result: MentionResult


# ─────────────────────────────────────────────────────────────────────────────
# MentionSaver
# ─────────────────────────────────────────────────────────────────────────────

class MentionSaver:
    """
    MentionResult を PostgreSQL の mention_analysis テーブルへ保存する。

    使い方:
        saver = MentionSaver(database_url)
        record_id = saver.save(ai_response_id, mention_result)
        ids = saver.save_batch([AnalysisRecord(...), ...])
    """

    def __init__(self, database_url: str):
        self.database_url = database_url

    # ── 単件保存 ─────────────────────────────────────────────────────────────

    def save(self, ai_response_id: str, result: MentionResult) -> str:
        """
        1 件の MentionResult を mention_analysis テーブルに INSERT し、
        生成された UUID (id) を返す。
        """
        sql = """
            INSERT INTO mention_analysis (
                ai_response_id,
                is_mentioned,
                mention_type,
                sentiment,
                sentiment_reason,
                cited_urls,
                competitor_mentions,
                misinformation_flag,
                misinformation_detail,
                raw_mention_excerpt,
                analyzed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id
        """
        row = self._to_db_row(ai_response_id, result)
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, row)
                record_id = str(cur.fetchone()[0])
        logger.debug("mention_analysis 保存: id=%s, ai_response_id=%s", record_id, ai_response_id)
        return record_id

    # ── バッチ保存 ────────────────────────────────────────────────────────────

    def save_batch(self, records: list[AnalysisRecord]) -> list[str]:
        """
        複数件を一括 INSERT（execute_values で高速化）。
        保存された UUID のリストを返す。
        """
        if not records:
            return []

        sql = """
            INSERT INTO mention_analysis (
                ai_response_id, is_mentioned, mention_type, sentiment,
                sentiment_reason, cited_urls, competitor_mentions,
                misinformation_flag, misinformation_detail,
                raw_mention_excerpt, analyzed_at
            ) VALUES %s
            RETURNING id
        """
        rows = [self._to_db_row(r.ai_response_id, r.result) for r in records]
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                execute_values(cur, sql, rows)
                ids = [str(row[0]) for row in cur.fetchall()]
        logger.info("mention_analysis バッチ保存: %d 件", len(ids))
        return ids

    # ── 集計クエリ ────────────────────────────────────────────────────────────

    def fetch_mention_rate(self, client_id: str, year: int, month: int) -> dict:
        """
        指定クライアント・年月の言及率サマリーを返す。
        v_mention_rate_monthly ビューを利用。

        Returns:
            {
                "chatgpt":     {"mention_rate": 0.75, "total": 28, "mentioned": 21},
                "gemini":      {...},
                "perplexity":  {...},
                "ai_overview": {...},
            }
        """
        period = f"{year}-{month:02d}"
        sql = """
            SELECT ai_provider, mention_rate_pct, total_queries, mentioned_count
            FROM v_mention_rate_monthly
            WHERE client_id = %s AND period = %s
        """
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (client_id, period))
                rows = cur.fetchall()

        result = {}
        for provider, rate, total, mentioned in rows:
            result[provider] = {
                "mention_rate": float(rate) / 100.0 if rate is not None else 0.0,
                "total": total,
                "mentioned": mentioned,
            }
        return result

    def fetch_all_brands_mention_rate(
        self, client_ids: list[str], year: int, month: int
    ) -> dict[str, dict]:
        """
        複数クライアントの言及率を一括取得する（競合比較用）。

        Returns:
            {
                client_id: {
                    "chatgpt": {"mention_rate": 0.75, "total": 28, "mentioned": 21},
                    ...
                },
                ...
            }
        """
        period = f"{year}-{month:02d}"
        placeholders = ",".join(["%s"] * len(client_ids))
        sql = f"""
            SELECT client_id, ai_provider, mention_rate_pct, total_queries, mentioned_count
            FROM v_mention_rate_monthly
            WHERE client_id IN ({placeholders}) AND period = %s
        """
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (*client_ids, period))
                rows = cur.fetchall()

        result: dict[str, dict] = {cid: {} for cid in client_ids}
        for cid, provider, rate, total, mentioned in rows:
            result[cid][provider] = {
                "mention_rate": float(rate) / 100.0 if rate is not None else 0.0,
                "total": total,
                "mentioned": mentioned,
            }
        return result

    def fetch_recent_analysis(
        self, client_id: str, limit: int = 100
    ) -> list[dict]:
        """
        最新の言及分析結果を返す（Sheets 出力・デバッグ用）。
        """
        sql = """
            SELECT
                ma.id,
                ma.ai_response_id,
                ar.ai_provider,
                qs.prompt_text,
                qs.category,
                ar.retrieved_at,
                ma.is_mentioned,
                ma.mention_type,
                ma.sentiment,
                ma.sentiment_reason,
                ma.cited_urls,
                ma.competitor_mentions,
                ma.misinformation_flag,
                ma.misinformation_detail,
                ma.raw_mention_excerpt,
                ma.analyzed_at
            FROM mention_analysis ma
            JOIN ai_responses ar ON ar.id = ma.ai_response_id
            JOIN query_sets qs   ON qs.id = ar.query_set_id
            WHERE qs.client_id = %s
            ORDER BY ma.analyzed_at DESC
            LIMIT %s
        """
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (client_id, limit))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ── private ──────────────────────────────────────────────────────────────

    @staticmethod
    def _to_db_row(ai_response_id: str, result: MentionResult) -> tuple:
        """MentionResult を DB INSERT 用のタプルに変換する。"""
        competitor_json = json.dumps(
            [cm.model_dump() for cm in result.competitor_mentions],
            ensure_ascii=False,
        )
        return (
            ai_response_id,
            result.is_mentioned,
            result.mention_type,
            result.sentiment,
            result.sentiment_reason,
            result.cited_urls,           # TEXT[] -> psycopg2 が自動変換
            Json(json.loads(competitor_json)),  # JSONB
            result.misinformation_flag,
            result.misinformation_detail,
            result.r