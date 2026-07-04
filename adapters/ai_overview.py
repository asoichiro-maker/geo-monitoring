"""
adapters/ai_overview.py — Google AI Overview（SerpAPI経由）アダプター

設計方針（PoC 0-A の知見を反映）:
  - 検出率 80%（4/5）を確認済み → GO 判定で本採用
  - 2ステップ取得: Step1で page_token が返ったら即 Step2 で全文取得
  - page_token の有効期限は 4 分以内 → Step2 は 1 秒以内に実行
  - 未検出（detected=False）の場合も記録し、レポートで「N/A」として扱う
  - SerpAPI コスト: $25/1,000件 → 月次28クエリ = $0.70未満
"""
from __future__ import annotations

import os
import time
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

import requests
from requests.exceptions import Timeout, ConnectionError as ReqConnectionError

from .base import BaseAIAdapter, AIResponse

SERPAPI_BASE = "https://serpapi.com/search"
DEFAULT_TIMEOUT = 20  # 秒


class AIOverviewAdapter(BaseAIAdapter):

    def __init__(
        self,
        api_key: str | None = None,
        hl: str = "ja",
        gl: str = "jp",
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key or os.environ["SERPAPI_KEY"]
        self.hl = hl
        self.gl = gl
        self.timeout = timeout

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(5),
        retry=retry_if_exception_type((Timeout, ReqConnectionError)),
        reraise=True,
    )
    def fetch(self, query: str) -> AIResponse:
        # ── Step 1: 通常検索 ───────────────────────────────────────────────
        step1_params = {
            "engine":  "google",
            "q":       query,
            "api_key": self.api_key,
            "hl":      self.hl,
            "gl":      self.gl,
            "num":     1,   # 検索結果本体は不要なので最小限に絞りコストを抑制
        }
        step1_resp = requests.get(SERPAPI_BASE, params=step1_params, timeout=self.timeout)
        step1_resp.raise_for_status()
        step1_data = step1_resp.json()

        ai_ov = step1_data.get("ai_overview", {})
        text = ai_ov.get("reconstructed_markdown", "")
        has_text_blocks = bool(ai_ov.get("text_blocks"))

        # ── Step 2: page_token があれば即座に展開リクエスト ────────────────
        if "page_token" in ai_ov and not (text or has_text_blocks):
            # page_token の有効期限は 4 分 → 遅延を最小限に
            time.sleep(0.5)
            step2_params = {
                "engine":     "google_ai_overview",
                "page_token": ai_ov["page_token"],
                "api_key":    self.api_key,
            }
            step2_resp = requests.get(SERPAPI_BASE, params=step2_params, timeout=self.timeout)
            step2_resp.raise_for_status()
            step2_data = step2_resp.json()
            ai_ov = step2_data.get("ai_overview", ai_ov)
            text = ai_ov.get("reconstructed_markdown", "")

        detected = bool(text or ai_ov.get("text_blocks"))

        # text_blocks が存在するが reconstructed_markdown がない場合のフォールバック
        if not text and ai_ov.get("text_blocks"):
            text = self._extract_text_from_blocks(ai_ov["text_blocks"])

        return AIResponse(
            provider="ai_overview",
            query=query,
            response_text=text,
            retrieved_at=self._now(),
            model_version="google-ai-overview",
            detected=detected,
            raw_data={
                "has_page_token": "page_token" in step1_data.get("ai_overview", {}),
                "detected": detected,
                "references": [
                    r.get("link") for r in ai_ov.get("references", []) if r.get("link")
                ],
                # デバッグ用: Step1 の ai_overview オブジェクト全体
                "raw_ai_overview": ai_ov,
            },
        )

    # ── private ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text_from_blocks(text_blocks: list[dict]) -> str:
        """
        text_blocks から平文テキストを再構成する。
        reconstructed_markdown が存在しない場合のフォールバック。
        """
        lines: list[str] = []
        for block in text_blocks:
            block_type = block.get("type", "")
            if block_type == "heading":
                lines.append(f"# {block.get('snippet', '')}")
            elif block_type in ("paragraph", "expandable"):
                lines.append(block.get("snippet", ""))
            elif block_type == "list":
                for item in block.get("list", []):
                    lines.append(f"- {item.get('snippet', '')}")
            elif block_type == "comparison":
                # 比較表形式は簡易テキスト化
                for item in block.get("list", []):
                    lines.append(item.get("snippet", ""))
        return "\n".join(filter(None, lines))
