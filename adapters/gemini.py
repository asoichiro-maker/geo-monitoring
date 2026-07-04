"""
adapters/gemini.py — Gemini アダプター（google.genai SDK）

設計方針:
  - safety_settings を OFF に設定
    → ブランド比較・競合言及を含むクエリで SAFETY_BLOCK になるケースを防ぐ
  - finish_reason が SAFETY の場合は response_text を空にして記録
    （判定側で「回答ブロック」として扱えるようにする）

SDK 移行:
  - 旧: google.generativeai (google-generativeai) → サポート終了
  - 新: google.genai (google-genai >= 1.0)

モデル:
  - 旧: gemini-1.5-flash → 廃止
  - 旧: gemini-2.0-flash → Shut down (2026-06-01)
  - 新: gemini-2.5-flash → Stable GA ✅ (2026-07 確認済み)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential

from google import genai
from google.genai import types

from .base import BaseAIAdapter, AIResponse

DEFAULT_MODEL = "gemini-2.5-flash"

_SAFETY_OFF = [
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",        threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="OFF"),
]


class GeminiAdapter(BaseAIAdapter):

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
    ):
        self._client = genai.Client(api_key=api_key or os.environ["GOOGLE_API_KEY"])
        self.model_name = model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        reraise=True,
    )
    def fetch(self, query: str) -> AIResponse:
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=query,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "あなたは役立つアシスタントです。"
                    "質問に対して、一般ユーザーに向けた自然な日本語で回答してください。"
                ),
                temperature=0.0,
                max_output_tokens=1024,
                safety_settings=_SAFETY_OFF,
            ),
        )

        blocked = False
        text = ""
        finish_reason = None
        try:
            finish_reason = str(response.candidates[0].finish_reason)
            if response.candidates[0].finish_reason == types.FinishReason.SAFETY:
                blocked = True
            else:
                text = response.text or ""
        except (IndexError, AttributeError, ValueError):
            blocked = True

        return AIResponse(
            provider="gemini",
            query=query,
            response_text=text,
            retrieved_at=self._now(),
            model_version=self.model_name,
            detected=not blocked,
            raw_data={
                "finish_reason": finish_reason,
                "safety_blocked": blocked,
            },
        )
