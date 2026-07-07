"""
mention_saver.py  --  GEO モニタリング SaaS
"""
from __future__ import annotations
import json, logging
from datetime import datetime, timezone
from dataclasses import dataclass
import psycopg2
from psycopg2.extras import Json, execute_values
from mention_detector import MentionResult

logger = logging.getLogger(__name__)


def _db_connect(database_url: str) -> psycopg2.extensions.connection:
    from urllib.parse import urlparse, unquote
    p = urlparse(database_url)
    is_supabase = "supabase.co" in database_url
    return psycopg2.connect(
        host=p.hostname, port=p.port or 5432,
        dbname=(p.path or "/postgres").lstrip("/"),
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        client_encoding="UTF8",
        sslmode="require" if is_supabase else "prefer",
        connect_timeout=15,
    )


@dataclass
class AnalysisRecord:
    ai_response_id: str
    result: MentionResult


class MentionSaver:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def save(self, ai_response_id: str, result: MentionResult) -> str:
        sql = """
            INSERT INTO mention_analysis (
                ai_response_id, is_mentioned, mention_type, sentiment,
                sentiment_reason, cited_urls, competitor_mentions,
                misinformation_flag, misinformation_detail,
                raw_mention_excerpt, analyzed_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """
        row = self._to_db_row(ai_response_id, result)
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, row)
                record_id = str(cur.fetchone()[0])
        return record_id

    def save_batch(self, records: list[AnalysisRecord]) -> list[str]:
        if not records:
            return []
        sql = """
            INSERT INTO mention_analysis (
                ai_response_id, is_mentioned, mention_type, sentiment,
                sentiment_reason, cited_urls, competitor_mentions,
                misinformation_flag, misinformation_detail,
                raw_mention_excerpt, analyzed_at
            ) VALUES %s RETURNING id
        """
        rows = [self._to_db_row(r.ai_response_id, r.result) for r in records]
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                result_rows = execute_values(cur, sql, rows, fetch=True)
                ids = [str(row[0]) for row in result_rows]
        logger.info("mention_analysis: %d saved", len(ids))
        return ids

    def fetch_mention_rate(self, client_id: str, year: int, month: int) -> dict:
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
                "total": total, "mentioned": mentioned,
            }
        return result

    def fetch_all_brands_mention_rate(
        self, client_ids: list[str], year: int, month: int
    ) -> dict:
        period = f"{year}-{month:02d}"
        placeholders = ",".join(["%s"] * len(client_ids))
        sql = (
            "SELECT client_id, ai_provider, mention_rate_pct, total_queries, mentioned_count "
            "FROM v_mention_rate_monthly "
            "WHERE client_id IN (" + placeholders + ") AND period = %s"
        )
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (*client_ids, period))
                rows = cur.fetchall()
        result = {cid: {} for cid in client_ids}
        for cid, provider, rate, total, mentioned in rows:
            result[cid][provider] = {
                "mention_rate": float(rate) / 100.0 if rate is not None else 0.0,
                "total": total, "mentioned": mentioned,
            }
        return result

    def fetch_recent_analysis(self, client_id: str, limit: int = 100) -> list:
        sql = """
            SELECT ma.id, ma.ai_response_id, ar.ai_provider, qs.prompt_text,
                   qs.category, ar.retrieved_at, ma.is_mentioned, ma.mention_type,
                   ma.sentiment, ma.sentiment_reason, ma.cited_urls,
                   ma.competitor_mentions, ma.misinformation_flag,
                   ma.misinformation_detail, ma.raw_mention_excerpt, ma.analyzed_at
            FROM mention_analysis ma
            JOIN ai_responses ar ON ar.id = ma.ai_response_id
            JOIN query_sets qs   ON qs.id = ar.query_set_id
            WHERE qs.client_id = %s
            ORDER BY ma.analyzed_at DESC LIMIT %s
        """
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (client_id, limit))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def fetch_mention_rate_months(self, client_id: str, periods: list) -> dict:
        """複数月の言及率を1クエリで一括取得。"""
        if not periods:
            return {}
        placeholders = ",".join(["%s"] * len(periods))
        sql = (
            "SELECT period, ai_provider, mention_rate_pct, total_queries, mentioned_count "
            "FROM v_mention_rate_monthly "
            "WHERE client_id = %s AND period IN (" + placeholders + ")"
        )
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (client_id, *periods))
                rows = cur.fetchall()
        result = {}
        for period, provider, rate, total, mentioned in rows:
            if period not in result:
                result[period] = {}
            result[period][provider] = {
                "mention_rate": float(rate) / 100.0 if rate is not None else 0.0,
                "total": total,
                "mentioned": mentioned,
            }
        return result

    def fetch_query_sets(self, client_id=None) -> list:
        """query_sets テーブルから質問リストを取得する。client_id=None で全件取得。"""
        if client_id:
            sql = """
                SELECT id, client_id, prompt_text, category, is_active, created_at
                FROM query_sets WHERE client_id = %s ORDER BY category, created_at
            """
            params = (client_id,)
        else:
            sql = """
                SELECT id, client_id, prompt_text, category, is_active, created_at
                FROM query_sets ORDER BY client_id, category, created_at
            """
            params = ()
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    @staticmethod
    def _to_db_row(ai_response_id: str, result: MentionResult) -> tuple:
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
            result.cited_urls,
            Json(json.loads(competitor_json)),
            result.misinformation_flag,
            result.misinformation_detail,
            result.raw_mention_excerpt,
            datetime.now(timezone.utc),
        )
