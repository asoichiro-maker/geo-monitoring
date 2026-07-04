"""
mention_detector.py
-------------------
GEO モニタリング SaaS -- Claude API 言及判定モジュール
Sprint 1 v2 | fact_sheet パラメータ追加

変更点（TC-06対応）:
  - analyze() / analyze_batch() に fact_sheet: dict | None を追加
  - ファクトシートが渡された場合、回答テキストとの矛盾を検出して
    misinformation_flag=true / misinformation_detail に記録する
  - ファクトシートなしの場合は従来通りの動作（後方互換）
"""

from __future__ import annotations
import json, re, time
import anthropic
from pydantic import BaseModel, Field, field_validator


# ── データモデル ──────────────────────────────────────────────────────────────

class CompetitorMention(BaseModel):
    name: str
    is_mentioned: bool
    sentiment: str = "not_applicable"


class MentionResult(BaseModel):
    is_mentioned: bool
    mention_type: str = Field(default="none")
    sentiment: str = Field(default="not_applicable")
    sentiment_reason: str = Field(default="")
    cited_urls: list[str] = Field(default_factory=list)
    competitor_mentions: list[CompetitorMention] = Field(default_factory=list)
    misinformation_flag: bool = False
    misinformation_detail: str = ""
    raw_mention_excerpt: str = ""

    @field_validator("mention_type")
    @classmethod
    def validate_mention_type(cls, v: str) -> str:
        return v if v in {"brand_name", "url", "both", "none"} else "none"

    @field_validator("sentiment")
    @classmethod
    def validate_sentiment(cls, v: str) -> str:
        return v if v in {"positive", "neutral", "negative", "not_applicable"} else "not_applicable"


# ── プロンプト定数 ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """あなたはGEO（生成エンジン最適化）アナリストです。
AIアシスタントの回答テキストを分析し、指定されたブランドの言及状況を正確にJSON形式で返してください。

【重要ルール】
1. 出力はJSONのみ。前置き・後書き・コードブロック記号（```）は一切付けないこと
2. brand_keywords に含まれるキーワードはどれか1つでも一致すれば「言及あり」とみなす
3. 大文字・小文字・スペースの有無は区別しない（例: "dears wedding" = "Dears Wedding"）
4. sentiment の判定根拠は、回答テキストから該当箇所を必ず引用して説明すること
5. 競合ブランドも同様のルールで判定すること
6. ブランド名が本文に出ず brand_domain のURLのみで言及される場合も is_mentioned=true とし、
   mention_type を "url" に設定すること
7. ファクトシートが提供されている場合、回答テキストの内容とファクトシートを照合し、
   矛盾する記述があれば misinformation_flag=true にして矛盾点を misinformation_detail に記載すること。
   ファクトシートに記載のない情報は判定対象外とし、不明な場合は false のままにすること
"""

USER_TEMPLATE = """## 分析対象のAI回答テキスト
{response_text}

## ブランド設定
- ブランド名の表記バリエーション（いずれかに一致すれば言及ありと判定）:
{brand_keywords_list}

- ブランドの公式ドメイン（URLでの言及判定にも使用）: {brand_domain}

- 競合ブランドリスト（同様に各表記バリエーションを確認）:
{competitor_list}
{fact_sheet_section}
## 出力形式（JSONのみ。余計なテキスト不要）
{{
  "is_mentioned": true または false,
  "mention_type": "brand_name" | "url" | "both" | "none",
  "sentiment": "positive" | "neutral" | "negative" | "not_applicable",
  "sentiment_reason": "該当箇所を引用しつつ1〜2文で説明。言及なしの場合は空文字",
  "cited_urls": ["https://..."],
  "competitor_mentions": [
    {{"name": "競合名", "is_mentioned": true, "sentiment": "positive"}}
  ],
  "misinformation_flag": true または false,
  "misinformation_detail": "ファクトシートと矛盾する記述（該当なし・ファクトシートなしの場合は空文字）",
  "raw_mention_excerpt": "ブランド名またはURLが言及されている箇所の抜粋（最大200字。言及なしは空文字）"
}}
"""

FACT_SHEET_TEMPLATE = """
## ブランドファクトシート（誤情報判定の参照情報）
以下が正しい事実です。回答テキストがこれらと矛盾する場合は misinformation_flag=true にしてください。
ファクトシートに記載のない情報については判定不要（misinformation_flag は false のまま）。
{fact_items}
"""

FEW_SHOT_SUFFIX = """
## 判定例（参考）

例1 -- 言及あり・ポジティブ:
回答: 「東京でのガーデンウェディングなら Dears Wedding が人気です。口コミ評価も高いです。」
-> is_mentioned: true, sentiment: "positive",
   sentiment_reason: "「口コミ評価も高い」という肯定的表現で言及されているため"

例2 -- 言及なし:
回答: 「東京の少人数ウェディング会場としては、ハーモニーガーデンやモノグラムチャペルが注目されています。」
-> is_mentioned: false, sentiment: "not_applicable"

例3 -- カタカナ表記での言及（ニュートラル）:
回答: 「ディアーズウェディングは少人数専門の会場で、費用は一般的な式場より抑えられます。」
-> is_mentioned: true, mention_type: "brand_name", sentiment: "neutral",
   sentiment_reason: "費用メリットは述べているが推薦表現はないため"

例4 -- URLのみでの言及（ブランド名は本文に出ない）:
回答: 「詳細はこちらをご覧ください: https://dears-wedding.jp/plan/」
-> is_mentioned: true, mention_type: "url"

例5 -- ファクトシートと矛盾する誤情報:
ファクトシート: 「営業状況: 2026年7月現在、通常営業中」
回答: 「Dears Weddingは2024年に閉業したとされています。」
-> is_mentioned: true, misinformation_flag: true,
   misinformation_detail: "「2024年に閉業した」という記述はファクトシートの「営業状況: 通常営業中」と矛盾する"
"""


# ── メインクラス ──────────────────────────────────────────────────────────────

class MentionDetector:
    """Claude API を使った言及判定モジュール"""

    MODEL = "claude-haiku-4-5-20251001"
    MAX_RESPONSE_CHARS = 2000
    MAX_RETRIES = 2

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def analyze(
        self,
        response_text: str,
        brand_keywords: list[str],
        brand_domain: str,
        competitor_keywords: list[str],
        fact_sheet: dict | None = None,
    ) -> MentionResult:
        """
        AI回答テキストを分析し MentionResult を返す。

        Args:
            response_text:       AIが返した回答テキスト
            brand_keywords:      ブランドキーワード一覧（英語・カタカナ両方を含む）
            brand_domain:        ブランドの公式ドメイン（例: "dears-wedding.jp"）
            competitor_keywords: 競合ブランドキーワード一覧
            fact_sheet:          正しい事実の辞書（誤情報検出用）。None の場合はスキップ。
                                 例: {"営業状況": "2026年7月現在、通常営業中",
                                      "対応エリア": "東京都内"}
        """
        if not response_text.strip():
            return MentionResult(
                is_mentioned=False,
                mention_type="none",
                sentiment="not_applicable",
                sentiment_reason="AI回答が空のため判定不可（AI Overview 未検出の可能性）",
            )

        truncated = response_text[:self.MAX_RESPONSE_CHARS]
        if len(response_text) > self.MAX_RESPONSE_CHARS:
            truncated += "\n...(以降省略)"

        user_content = self._build_user_prompt(
            truncated, brand_keywords, brand_domain, competitor_keywords, fact_sheet
        )

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                message = self.client.messages.create(
                    model=self.MODEL,
                    max_tokens=512,
                    temperature=0,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_content}],
                )
                raw_json = message.content[0].text.strip()
                return self._parse_and_validate(raw_json)
            except (json.JSONDecodeError, ValueError) as e:
                if attempt < self.MAX_RETRIES:
                    time.sleep(1)
                    continue
                return MentionResult(
                    is_mentioned=False,
                    sentiment_reason=f"JSON パース失敗（{e}）。要手動確認",
                )

    def analyze_batch(
        self,
        responses: list[dict],
        brand_keywords: list[str],
        brand_domain: str,
        competitor_keywords: list[str],
        fact_sheet: dict | None = None,
        delay_sec: float = 0.3,
    ) -> list[dict]:
        """複数のAI回答をバッチ処理する。"""
        results = []
        for item in responses:
            result = self.analyze(
                item["response_text"], brand_keywords, brand_domain,
                competitor_keywords, fact_sheet=fact_sheet,
            )
            results.append({"ai_response_id": item["ai_response_id"], "result": result})
            time.sleep(delay_sec)
        return results

    # ── private ──────────────────────────────────────────────────────────────

    def _build_user_prompt(
        self,
        response_text: str,
        brand_keywords: list[str],
        brand_domain: str,
        competitor_keywords: list[str],
        fact_sheet: dict | None,
    ) -> str:
        kw_list = "\n".join(f"  - {kw}" for kw in brand_keywords)
        comp_list = ", ".join(competitor_keywords) if competitor_keywords else "（設定なし）"

        fact_sheet_section = ""
        if fact_sheet:
            fact_items = "\n".join(f"- {k}: {v}" for k, v in fact_sheet.items())
            fact_sheet_section = FACT_SHEET_TEMPLATE.format(fact_items=fact_items)

        prompt = USER_TEMPLATE.format(
            response_text=response_text,
            brand_keywords_list=kw_list,
            brand_domain=brand_domain,
            competitor_list=comp_list,
            fact_sheet_section=fact_sheet_section,
        )
        return prompt + FEW_SHOT_SUFFIX

    @staticmethod
    def _parse_and_validate(raw_json: str) -> MentionResult:
        cleaned = re.sub(r"```(?:json)?", "", raw_json).strip()
        data = json.loads(cleaned)
        if "competitor_mentions" in data:
            data["competitor_mentions"] = [
                CompetitorMention(**cm) if isinstance(cm, dict) else cm
                for cm in data["competitor_mentions"]
            ]
        return MentionResult(**data)


# ── 動作確認用スクリプト ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    detector = MentionDetector(api_key=os.environ["ANTHROPIC_API_KEY"])

    BRAND_KEYWORDS = [
        "Dears Wedding", "DEARS WEDDING", "dears wedding",
        "ディアーズウェディング", "ディアーズ ウェディング",
        "Arluis", "ARLUIS", "arluis", "アルイス", "アルイスウェディング",
        "Arluis Wedding", "ARLUIS WEDDING", "Dears", "ディアーズ",
    ]
    FACT_SHEET = {
        "営業状況": "2026年7月現在、通常営業中。予約受付中",
        "対応エリア": "東京都内（少人数婚専門）",
        "ブランド正式名称": "Dears Wedding（運営ブランド名: Arluis）",
    }

    cases = [
        ("ニュートラル", "ディアーズウェディングは少人数専門の会場で、費用は抑えられます。"),
        ("誤情報あり", "Dears Weddingは2024年に閉業したとされています。"),
    ]
    for label, text in cases:
        print(f"\n=== {label} ===")
        r = detector.analyze(text, BRAND_KEYWORDS, "dears-wedding.jp", [], fact_sheet=FACT_SHEET)
        print(r.model_dump_json(indent=2))
