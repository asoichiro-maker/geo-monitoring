"""
adapters/perplexity.py — Perplexity（sonar-pro）アダプター

設計方針:
  - Perplexity API は OpenAI 互換エンドポイント（https://api.perplexity.ai）
  - search_domain_filter で自社ドメインを除外しバイアスを防ぐ
    （自社サイトが検索上位にあるとそれが回答に混入し、言及率が水増しされる）
  - citations フィールドから引用元 URL を取得し raw_data に保存
    → mention_analysis の cited_urls と突合できる

注意:
  - API版の回答はブラウザ版 Perplexity と多少異なる場合がある
  - 初期は並行してブラウザ版もサンプルチェックすること（計画書記載）
"""
from __future__ import annotations

import os
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import openai  # Perplexity は OpenAI 互換 SDK で呼び出し可能
from openai import RateLimitError, APITimeoutError

from .base import BaseAIAdapter, AIResponse

DEFAULT_MODEL = "sonar-pro"
PERPLEXITY_BASE_URL = "https://api.perplexity.ai"

# 自社ドメインを除外することで「自社サイトを参照してブランドを言及」という
# バイアスが排除され、純粋にAIのベース知識から言及されるかを計測できる
_EXCLUDE_DOMAINS = ["dears-wedding.jp", "arluis.jp"]

_SYSTEM = (
    "あなたは役立つアシスタントです。"
    "質問に対して、一般ユーザーに向けた自然な日本語で回答してください。"
)


class PerplexityAdapter(BaseAIAdapter):

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        exclude_domains: list[str] | None = None,
    ):
        self.client = openai.OpenAI(
            api_key=api_key or os.environ["PERPLEXITY_API_KEY"],
            base_url=PERPLEXITY_BASE_URL,
        )
        self.model = model
        # exclude_domains が指定されていれば上書き
        self.exclude_domains = exclude_domains if exclude_domains is not None else _EXCLUDE_DOMAINS

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError)),
        reraise=True,
    )
    def fetch(self, query: str) -> AIResponse:
        extra_body: dict = {}
        if self.exclude_domains:
            extra_body["search_domain_filter"] = [
                f"-{d}" for d in self.exclude_domains  # Perplexity API: "-domain" で除外
            ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": query},
            ],
            temperature=0,
            max_tokens=1024,
            extra_body=extra_body if extra_body else None,
        )

        text = response.choices[0].message.content or ""

        # citations: Perplexity API 固有フィールド（引用元 URL 一覧）
        citations: list[str] = []
        if hasattr(response, "citations"):
            citations = response.citations or []

        return AIResponse(
            provider="perplexity",
            query=query,
            response_text=text,
            retrieved_at=self._now(),
            model_version=self.model,
            detected=True,
            raw_data={
                "id": response.id,
                "citations": citations,          # mention_analysis.cited_urls と突合用
                "usage": response.usage.model_dump() if response.usage else {},
            },
        )
