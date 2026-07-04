"""
test_mention_detector_real.py
------------------------------
【Sprint 3 着手前ゲート】claude-haiku-4-5 実 API 検証スクリプト
v2 | fact_sheet 追加・TC-06 再修正版

実行方法:
    export ANTHROPIC_API_KEY=sk-ant-...
    python test_mention_detector_real.py

全ケースで PASS すれば Sprint 3（本番接続）に進んでよい。
"""
from __future__ import annotations

import json
import os
import sys
import time

from mention_detector import MentionDetector, MentionResult

# -------------------------------------------------------------------------
# 共通設定
# -------------------------------------------------------------------------

BRAND_KEYWORDS = [
    "Dears Wedding", "DEARS WEDDING", "dears wedding",
    "ディアーズウェディング", "ディアーズ ウェディング",
    "Arluis", "ARLUIS", "arluis",
    "アルイス", "アルイスウェディング",
    "Arluis Wedding", "ARLUIS WEDDING",
    "Dears", "ディアーズ",
]
BRAND_DOMAIN = "dears-wedding.jp"
COMPETITOR_KEYWORDS = [
    "モノグラムチャペル", "ザ・グランドティアラ", "ラ・ヴィータ",
    "ハーモニーガーデン", "アニバーサリー",
]

# ファクトシート: AIが誤情報を書きがちな基本情報を列挙する
# → TC-06 の misinformation_flag 判定に使用
# → 本番運用では clients テーブルまたは設定ファイルで管理する
DEARS_WEDDING_FACT_SHEET = {
    "営業状況":     "2026年7月現在、通常営業中。予約受付中",
    "対応エリア":   "東京都内（ガーデンウェディング・少人数婚専門）",
    "ブランド正式名称": "Dears Wedding（運営ブランド名: Arluis）",
    "運営会社":     "株式会社マークエッジ関連会社が運営",
    "専門領域":     "少人数ウェディング（20名以下）専門。大規模披露宴は非対応",
    "公式サイト":   "https://dears-wedding.jp / https://www.arluis.jp",
}

# -------------------------------------------------------------------------
# テストケース定義
# -------------------------------------------------------------------------

TEST_CASES = [
    # -- ケース 1〜3: 前回代替出力ケースの実 API 再検証 ----------------------
    {
        "id": "TC-01",
        "label": "[再検証] カタカナ表記・ニュートラル",
        "input": "ディアーズウェディングは少人数専門の会場で、費用は一般的な式場より抑えられます。",
        "fact_sheet": DEARS_WEDDING_FACT_SHEET,
        "expect": {
            "is_mentioned": True,
            "sentiment": "neutral",
        },
    },
    {
        "id": "TC-02",
        "label": "[再検証] 英語表記・ポジティブ",
        "input": "東京の少人数婚なら Dears Wedding が人気です。スタッフの対応が丁寧で口コミも高評価です。",
        "fact_sheet": DEARS_WEDDING_FACT_SHEET,
        "expect": {
            "is_mentioned": True,
            "sentiment": "positive",
        },
    },
    {
        "id": "TC-03",
        "label": "[再検証] 言及なし（競合のみ）",
        "input": "東京でのガーデンウェディングにはモノグラムチャペルやハーモニーガーデンが人気です。",
        "fact_sheet": DEARS_WEDDING_FACT_SHEET,
        "expect": {
            "is_mentioned": False,
            "sentiment": "not_applicable",
            "competitor_names_mentioned": ["モノグラムチャペル", "ハーモニーガーデン"],
        },
    },

    # -- ケース 4〜6: 境界事例 -----------------------------------------------
    {
        "id": "TC-04",
        "label": "[境界] URL のみでの言及（ブランド名は本文に出ない）",
        "input": (
            "東京の少人数ウェディングには複数の選択肢があります。"
            "詳細はこちらの式場比較サイトをご覧ください: "
            "https://dears-wedding.jp/plan/ や https://www.arluis.jp/gallery/ など"
            "が参考になるでしょう。"
        ),
        "fact_sheet": DEARS_WEDDING_FACT_SHEET,
        "expect": {
            "is_mentioned": True,
            "mention_type": "url",
        },
        "note": "PoC ID1 相当。旧プロンプトでは is_mentioned=false になる可能性があった。",
    },
    {
        "id": "TC-05",
        "label": "[境界] 大文字・小文字混在表記",
        "input": "DEARS WEDDINGは少人数婚の先駆けとして知られており、ARLUISブランドでも展開しています。",
        "fact_sheet": DEARS_WEDDING_FACT_SHEET,
        "expect": {
            "is_mentioned": True,
            "sentiment": "positive",
        },
        "note": "全大文字表記で brand_keywords に含まれるかテスト。",
    },
    {
        "id": "TC-06",
        "label": "[境界] 誤情報を含む言及（ファクトシートで矛盾検出）",
        "input": (
            "Dears Wedding は2024年に閉業したと報告されています。"
            "現在は予約を受け付けていないため、他の会場を検討することをお勧めします。"
        ),
        "fact_sheet": DEARS_WEDDING_FACT_SHEET,  # <- v2 で追加: これがないと検出不可
        "expect": {
            "is_mentioned": True,
            "misinformation_flag": True,   # ファクトシート「通常営業中」と矛盾
        },
        "note": (
            "v1 では fact_sheet がなかったため misinformation_flag=False で FAIL。"
            "v2 で DEARS_WEDDING_FACT_SHEET を渡すことで「閉業」が誤情報と検出できるはず。"
        ),
    },
]

# -------------------------------------------------------------------------
# テスト実行
# -------------------------------------------------------------------------

def run_tests(api_key: str) -> bool:
    detector = MentionDetector(api_key=api_key)
    results: list[dict] = []
    all_passed = True

    print(f"\n{'='*65}")
    print(f"  claude-haiku-4-5 実 API 検証 v2（{len(TEST_CASES)} ケース）")
    print(f"  モデル: {MentionDetector.MODEL}")
    print(f"  変更点: fact_sheet パラメータを全ケースに渡す（TC-06 再挑戦）")
    print(f"{'='*65}\n")

    for tc in TEST_CASES:
        print(f"▶ {tc['id']} {tc['label']}")
        if tc.get("note"):
            print(f"  注: {tc['note']}")
        print(f"  入力: {tc['input'][:80]}{'...' if len(tc['input']) > 80 else ''}")

        result: MentionResult = detector.analyze(
            response_text=tc["input"],
            brand_keywords=BRAND_KEYWORDS,
            brand_domain=BRAND_DOMAIN,
            competitor_keywords=COMPETITOR_KEYWORDS,
            fact_sheet=tc.get("fact_sheet"),   # ケースごとに fact_sheet を渡す
        )
        time.sleep(0.5)

        # -- 期待値チェック --------------------------------------------------
        expect = tc["expect"]
        failures: list[str] = []

        if "is_mentioned" in expect and result.is_mentioned != expect["is_mentioned"]:
            failures.append(
                f"is_mentioned: got={result.is_mentioned}, expected={expect['is_mentioned']}"
            )
        if "sentiment" in expect and result.sentiment != expect["sentiment"]:
            failures.append(
                f"sentiment: got={result.sentiment!r}, expected={expect['sentiment']!r}"
            )
        if "mention_type" in expect and result.mention_type != expect["mention_type"]:
            failures.append(
                f"mention_type: got={result.mention_type!r}, expected={expect['mention_type']!r}"
            )
        if "misinformation_flag" in expect and result.misinformation_flag != expect["misinformation_flag"]:
            failures.append(
                f"misinformation_flag: got={result.misinformation_flag}, "
                f"expected={expect['misinformation_flag']}"
            )
        if "competitor_names_mentioned" in expect:
            detected_names = {cm.name for cm in result.competitor_mentions if cm.is_mentioned}
            for name in expect["competitor_names_mentioned"]:
                if name not in detected_names:
                    failures.append(f"競合未検出: {name!r}")

        # -- 結果出力 --------------------------------------------------------
        status = "PASS" if not failures else "FAIL"
        print(f"  結果: {status}")
        print(f"  出力: {result.model_dump_json(indent=None)}")
        if result.sentiment_reason:
            print(f"  判定根拠: {result.sentiment_reason}")
        if result.misinformation_detail:
            print(f"  誤情報詳細: {result.misinformation_detail}")
        if failures:
            for f in failures:
                print(f"  x {f}")
            all_passed = False
        print()

        results.append({
            "id": tc["id"],
            "label": tc["label"],
            "passed": not failures,
            "failures": failures,
            "output": result.model_dump(),
        })

    # -- サマリー ------------------------------------------------------------
    passed_count = sum(1 for r in results if r["passed"])
    print(f"{'='*65}")
    print(f"  結果サマリー: {passed_count}/{len(results)} PASS")
    if all_passed:
        print("  全ケース PASS -- Sprint 3 着手を承認")
    else:
        print("  FAIL あり -- プロンプトを修正してから再実行してください")
    print(f"{'='*65}\n")

    log_path = "test_results_real_api_v2.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"  詳細ログ: {log_path}")

    return all_passed


# -------------------------------------------------------------------------
# エントリポイント
# -------------------------------------------------------------------------

if __name__ == "__main__":
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("エラー: ANTHROPIC_API_KEY が設定されていません。")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    ok = run_tests(api_key)
    sys.exit(0 if ok else 1)
