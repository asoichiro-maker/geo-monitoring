"""
adapters/gemini.py
------------------
Gemini アダプター — google.genai SDK (google-genai >= 1.0)

MODEL = "gemini-2.5-flash"  ← Stable GA (2026-07 確認済み)

廃止モデル:
  - gemini-1.5-flash : 廃止済み
  - gemini-2.0-flash : Shut down 2026-06-01
  - gemini-2.5-flash : ✅ 現行推奨（Stable GA）
  公式: https://ai.google.dev/gemini-api/docs/models
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from google import genai
from google.genai import types

from .base import BaseAIAdapter, AIResponse

_SAFETY_OFF = [
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",        threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="OFF"),
]


class GeminiAdapter(BaseAIAdapter):
    """
    Google Gemini API アダプター。

    環境変数:
        GEMINI_API_KEY: Google AI Studio の API キー
    """

    # ★ gemini-2.5-flash = Stable GA / 高コスパ・低レイテンシ
    MODEL = "gemini-2.5-flash"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ["GEMINI_API_KEY"]
        self._client = genai.Client(api_key=self._api_key)

    def fetch(self, query: str) -> AIResponse:
        response = self._client.models.generate_content(
            model=self.MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                safety_settings=_SAFETY_OFF,
            ),
        )

        if not response.candidates:
            return AIResponse(
                provider="gemini",
                query=query,
                response_text="",
                retrieved_at=datetime.now(timezone.utc),
                model_version=self.MODEL,
                detected=False,
                raw_data={"finish_reason": "NO_CANDIDATES"},
            )

        candidate = response.candidates[0]
        finish_reason = candidate.finish_reason

        if finish_reason == types.FinishReason.SAFETY:
            return AIResponse(
                provider="gemini",
                query=query,
                response_text="",
                retrieved_at=datetime.now(timezone.utc),
                model_version=self.MODEL,
                detected=False,
                raw_data={"finish_reason": "SAFETY"},
            )

        return AIResponse(
            provider="gemini",
            query=query,
            response_text=response.text or "",
            retrieved_at=datetime.now(timezone.utc),
            model_version=self.MODEL,
            detected=True,
            raw_data={
                "finish_reason": str(finish_reason),
                "model": self.MODEL,
            },
        )
