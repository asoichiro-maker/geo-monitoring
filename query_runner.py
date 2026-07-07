"""
query_runner.py — 共通クエリ実行エンジン（Sprint 2-5）

責務:
  - 4つのアダプターをまとめて実行
  - レート制限・エラーのハンドリング（アダプター間で独立して処理）
  - 実行結果を DB に保存
  - 進捗ログ出力
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import psycopg2
from psycopg2.extras import Json

from adapters import (
    BaseAIAdapter, AIResponse,
    ChatGPTAdapter, GeminiAdapter, PerplexityAdapter, AIOverviewAdapter,
)

logger = logging.getLogger(__name__)


def _db_connect(database_url: str) -> psycopg2.extensions.connection:
    """Windows 環境での文字コード問題を回避するため client_encoding を明示する。"""
    return psycopg2.connect(database_url, client_encoding="UTF8")


# ─────────────────────────────────────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RunnerConfig:
    """実行エンジンの設定"""
    inter_provider_delay: float = 1.0
    inter_query_delay: float = 0.5
    skip_on_error: bool = True
    enabled_providers: list[str] = field(default_factory=lambda: [
        "chatgpt", "gemini", "perplexity", "ai_overview"
    ])


# ─────────────────────────────────────────────────────────────────────────────
# QueryRunner
# ─────────────────────────────────────────────────────────────────────────────

class QueryRunner:
    """
    登録済みクエリを全アダプターで実行し、結果を DB に保存する。

    使い方:
        runner = QueryRunner.from_env(database_url, config)
        runner.run_client(client_id="a1b2c3d4-...")

    on_result コールバックのシグネチャ:
        def on_result(response: AIResponse, ai_response_id: str) -> None
        # ※ 第2引数は ai_responses テーブルの UUID（query_set_id ではない）
    """

    def __init__(
        self,
        database_url: str,
        adapters: dict[str, BaseAIAdapter],
        config: RunnerConfig | None = None,
        on_result: Callable[[AIResponse, str], None] | None = None,
    ):
        self.database_url = database_url
        self.adapters = adapters
        self.config = config or RunnerConfig()
        self.on_result = on_result

    @classmethod
    def from_env(cls, database_url: str, config: RunnerConfig | None = None) -> "QueryRunner":
        cfg = config or RunnerConfig()
        adapters: dict[str, BaseAIAdapter] = {}
        if "chatgpt"     in cfg.enabled_providers: adapters["chatgpt"]     = ChatGPTAdapter()
        if "gemini"      in cfg.enabled_providers: adapters["gemini"]      = GeminiAdapter()
        if "perplexity"  in cfg.enabled_providers: adapters["perplexity"]  = PerplexityAdapter()
        if "ai_overview" in cfg.enabled_providers: adapters["ai_overview"] = AIOverviewAdapter()
        return cls(database_url=database_url, adapters=adapters, config=cfg)

    def run_client(self, client_id: str) -> dict[str, int]:
        """
        指定クライアントのアクティブなクエリを全プロバイダーで実行し DB に保存。

        Returns:
            {"total": N, "success": N, "skipped": N, "error": N}
        """
        queries = self._load_active_queries(client_id)
        if not queries:
            logger.warning("クライアント %s のアクティブなクエリが見つかりません", client_id)
            return {"total": 0, "success": 0, "skipped": 0, "error": 0}

        stats = {"total": 0, "success": 0, "skipped": 0, "error": 0}
        providers = [p for p in self.config.enabled_providers if p in self.adapters]

        logger.info(
            "実行開始: client=%s, queries=%d, providers=%s",
            client_id, len(queries), providers,
        )

        for provider_name in providers:
            adapter = self.adapters[provider_name]
            logger.info("プロバイダー: %s", provider_name)

            for query_row in queries:
                query_set_id = query_row["id"]
                prompt_text  = query_row["prompt_text"]
                stats["total"] += 1

                try:
                    # 同月に同クエリ×プロバイダーが既に存在すればスキップ
                    if self._already_run_this_month(query_set_id, provider_name):
                        stats["skipped"] += 1
                        stats["total"] -= 1  # total はスキップ分を除く
                        logger.debug("[%s] スキップ（今月実行済み）: '%s...'",
                                     provider_name, prompt_text[:30])
                        continue

                    response = adapter.fetch(prompt_text)

                    # _save_response は ai_responses に INSERT して UUID を返す
                    # ★ バグ修正: 戻り値を ai_response_id として受け取り、
                    #   on_result に渡す（以前は query_set_id を誤って渡していた）
                    ai_response_id = self._save_response(query_set_id, response)
                    stats["success"] += 1

                    if self.on_result:
                        self.on_result(response, ai_response_id)

                    logger.debug(
                        "[%s] '%s...' -> detected=%s, chars=%d",
                        provider_name, prompt_text[:30],
                        response.detected, len(response.response_text),
                    )
                    time.sleep(self.config.inter_query_delay)

                except Exception as exc:
                    stats["error"] += 1
                    logger.error(
                        "[%s] '%s...' -> エラー: %s",
                        provider_name, prompt_text[:30], exc,
                    )
                    if not self.config.skip_on_error:
                        raise

            time.sleep(self.config.inter_provider_delay)

        logger.info("実行完了: %s", stats)
        return stats

    def _load_active_queries(self, client_id: str) -> list[dict]:
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, prompt_text, category, sort_order
                    FROM query_sets
                    WHERE client_id = %s AND is_active = TRUE
                    ORDER BY sort_order
                    """,
                    (client_id,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _already_run_this_month(self, query_set_id: str, provider: str) -> bool:
        """同月・同クエリ・同プロバイダーのレコードが既に存在するか確認。"""
        sql = """
            SELECT 1 FROM ai_responses
            WHERE query_set_id = %s
              AND ai_provider = %s
              AND DATE_TRUNC('month', retrieved_at) = DATE_TRUNC('month', NOW())
            LIMIT 1
        """
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (query_set_id, provider))
                return cur.fetchone() is not None

    def _save_response(self, query_set_id: str, response: AIResponse) -> str:
        """
        ai_responses テーブルに INSERT し、生成された UUID を返す。
        この UUID が mention_analysis の外部キー (ai_response_id) になる。
        """
        sql = """
            INSERT INTO ai_responses
                (query_set_id, ai_provider, response_text,
                 model_version, detected, retrieved_at, raw_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        with _db_connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    query_set_id,
                    response.provider,
                    response.response_text,
                    response.model_version,
                    response.detected,
                    response.retrieved_at,
                    Json(response.raw_data),
                ))
                return str(cur.fetchone()[0])


# ─────────────────────────────────────────────────────────────────────────────
# CLI エントリポイント（簡易実行用）
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os, sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    client_id    = sys.argv[1] if len(sys.argv) > 1 else "a1b2c3d4-0000-0000-0000-000000000001"
    database_url = os.environ["DATABASE_URL"]

    runner = QueryRunner.from_env(database_url)
    stats  = runner.run_client(client_id)
    print("完了:", stats)
