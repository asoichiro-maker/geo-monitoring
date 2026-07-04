# Sprint 0-A PoC: SerpAPI経由 Google AI Overview取得検証
#
# 使い方:
# 1. pip install requests --break-system-packages
# 2. serpapi.com で無料トライアルキーを取得し、SERPAPI_KEY に設定
# 3. 実行すると28クエリ中の検出率・レスポンス内容・引用URLを確認できる

import requests
import time
import json
import os

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "YOUR_API_KEY")


def fetch_ai_overview(query: str) -> dict:
    # Step 1: 通常検索
    r = requests.get("https://serpapi.com/search", params={
        "engine": "google", "q": query, "api_key": SERPAPI_KEY,
        "hl": "ja", "gl": "jp"
    }, timeout=15)
    data = r.json()
    ai_ov = data.get("ai_overview", {})

    # Step 2: page_tokenがあれば即座に2回目リクエスト（4分以内に叩く必要あり）
    if "page_token" in ai_ov and not ai_ov.get("reconstructed_markdown"):
        time.sleep(0.5)
        r2 = requests.get("https://serpapi.com/search", params={
            "engine": "google_ai_overview",
            "page_token": ai_ov["page_token"],
            "api_key": SERPAPI_KEY
        }, timeout=15)
        ai_ov = r2.json().get("ai_overview", ai_ov)

    return {
        "query": query,
        "detected": bool(ai_ov.get("text_blocks") or ai_ov.get("reconstructed_markdown")),
        "text": ai_ov.get("reconstructed_markdown", ""),
        "references": [ref.get("link") for ref in ai_ov.get("references", [])],
    }


# 本番28クエリの中から代表的な5件でまず試す（診断チェックリートより抜粋）
TEST_QUERIES = [
    "沖縄でリゾートウェディングを考えています。おすすめの会場を教えてください",
    "Arluisという沖縄の結婚式場の評判はどうですか？",
    "沖縄でリゾートウェディングを挙げる場合、費用はだいたいいくらくらいかかりますか？",
    "沖縄で結婚式を挙げる場合、ゲストは平均何人くらい呼びますか？",
    "沖縄で結婚式を挙げて後悔した人はいますか？デメリットも知りたいです",
]


def main():
    if SERPAPI_KEY == "YOUR_API_KEY":
        print("SERPAPI_KEY を環境変数にセットしてから実行してください。")
        print('例: export SERPAPI_KEY="xxxxxxxx"')
        return

    results = []
    for q in TEST_QUERIES:
        try:
            res = fetch_ai_overview(q)
        except Exception as e:
            res = {"query": q, "detected": False, "text": "", "references": [], "error": str(e)}
        results.append(res)
        status = "検出" if res["detected"] else "未検出"
        print(f"[{status}] {q}")

    detected = sum(1 for r in results if r["detected"])
    n = len(results)
    rate = detected / n * 100

    print(f"\n=== 検出率: {detected}/{n} ({rate:.0f}%) ===")
    print("\n--- Go/No-Go判定基準 ---")
    print("GO       : 5クエリ中3件以上でAI Overviewを取得")
    print("条件付GO : 5クエリ中1〜2件のみ取得")
    print("NO-GO    : 5クエリ中0件または連続タイムアウト → 3AI構成に切替、AI Overviewは手入力併用")

    if detected >= 3:
        verdict = "GO"
    elif detected >= 1:
        verdict = "条件付GO"
    else:
        verdict = "NO-GO"
    print(f"\n>>> 判定: {verdict}")

    with open("sprint0a_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n詳細結果を sprint0a_results.json に保存しました")


if __name__ == "__main__":
    main()
