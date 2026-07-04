"""
adapters/chatgpt.py — ChatGPT（OpenAI Chat Completions）アダプター

設計方針:
  - temperature=0 で再現性確保
  - max_tokens=1024（回答が長すぎてもKPI用途なので十分）
  - tenacity でレート制限エラー時の指数バックオフリトライ
"""
from __future__ import annotations

import os
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import openai
from openai import RateLimitError, APITimeoutError, APIConnectionError

from .base import BaseAIAdapter, AIResponse

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0

# システムプロンプト: 検索エンジンと同様に自然な回答を引き出す
_SYSTEM = (
    "あなたは役立つアシスタントです。"
    "質問に対して、一般ユーザーに向けた自然な日本語で回答してください。"
)


class ChatGPTAdapter(BaseAIAdapter):

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.client = openai.OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self.model = model
        self.max_tokens = max_tokens

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError)),
        reraise=True,
    )
    def fetch(self, query: str) -> AIResponse:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=DEFAULT_TEMPERATURE,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": query},
            ],
        )
        text = response.choices[0].message.content or ""
        return AIResponse(
            provider="chatgpt",
            query=query,
            response_text=text,
            retrieved_at=self._now(),
            model_version=response.model,
            detected=True,
            raw_data={
                "id": response.id,
                "usage": response.usage.model_dump() if response.usage else {},
            },
        )
