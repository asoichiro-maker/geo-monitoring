"""
adapters/base.py — 全アダプター共通インターフェース
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AIResponse:
    """全アダプターが返す統一レスポンス型"""
    provider: str           # "chatgpt" | "gemini" | "perplexity" | "ai_overview"
    query: str
    response_text: str      # 空文字 = 検出なし（AI Overview 未検出等）
    retrieved_at: datetime
    model_version: str
    detected: bool = True   # AI Overview 未検出時のみ False
    raw_data: dict = field(default_factory=dict)  # 生レスポンス全体（デバッグ用）

    def is_empty(self) -> bool:
        return not self.response_text.strip()


class BaseAIAdapter(ABC):
    """全アダプターが実装すべき共通インターフェース"""

    @abstractmethod
    def fetch(self, query: str) -> AIResponse:
        """クエリを投げて AIResponse を返す"""

    def fetch_batch(self, queries: list[str]) -> list[AIResponse]:
        """デフォルト実装: 1件ずつ順次実行（サブクラスでオーバーライド可）"""
        return [self.fetch(q) for q in queries]

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
