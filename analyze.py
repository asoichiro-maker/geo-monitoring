#!/usr/bin/env python3
"""
analyze.py — GEO モニタリング 改善コンサルテーションスクリプト

使い方:
    python analyze.py                        # 今月のデータで分析
    python analyze.py --month 2026-07        # 指定月で分析
    python analyze.py --client dears         # 特定クライアントのみ
    python analyze.py --no-report            # レポート生成をスキップ
"""
from __future__ import annotations
import os, sys, argparse, json
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

try:
    import psycopg2
    import anthropic
    from dotenv import load_dotenv
    load_dotenv()
except ImportError as e:
    print(f"依存ライブラリが不足しています: {e}")
    print("pip install psycopg2-binary anthropic python-dotenv")
    sys.exit(1)

# ─── クライアント定義 ────────────────────────────────────────────────────────

CLIENTS = {
    "dears":  ("a1b2c3d4-0000-0000-0000-000000000001", "Dears Wedding RESORT"),
    "arluis": ("a1b2c3d4-0000-0000-0000-000000000002", "アールイズ・ウエディング"),
    "watabe": ("a1b2c3d4-0000-0000-0000-000000000003", "ワタベウェディング"),
}
ALL_CLIENT_IDS = [v[0] for v in CLIENTS.values()]
ID_TO_NAME = {v[0]: v[1] for v in CLIENTS.values()}

PROVIDERS = ["chatgpt", "gemini", "perplexity", "ai_overview"]
PROVIDER_LABELS = {
    "chatgpt": "ChatGPT", "gemini": "Gemini",
    "perplexity": "Perplexity", "ai_overview": "AI Overview",
}


# ─── DB接続 ─────────────────────────────────────────────────────────────────

def _connect():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("エラー: DATABASE_URL が設定されていません")
        sys.exit(1)
    p = urlparse(db_url)
    return psycopg2.connect(
        host=p.hostname, port=p.port or 5432,
        dbname=(p.path or "/postgres").lstrip("/"),
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        sslmode="require", connect_timeout=15, client_encoding="UTF8",
    )


# ─── データ取得 ──────────────────────────────────────────────────────────────

def fetch_monthly_rates(period: str) -> dict:
    """全クライアントの月次言及率を取得。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT client_id, ai_provider,
                       SUM(total_queries) AS total,
                       SUM(mentioned_count) AS mentioned,
                       ROUND(SUM(mentioned_count)::numeric
                             / NULLIF(SUM(total_queries),0)::numeric * 100, 1) AS rate
                FROM v_mention_rate_monthly
                WHERE period = %s
                GROUP BY client_id, ai_provider
                ORDER BY client_id, ai_provider
            """, (period,))
            rows = cur.fetchall()
    result = {}
    for cid, prov, total, mentioned, rate in rows:
        if cid not in result:
            result[cid] = {}
        result[cid][prov] = {
            "total": int(total or 0),
            "mentioned": int(mentioned or 0),
            "rate": float(rate or 0),
        }
    return result


def fetch_query_performance(client_id: str, period: str, limit: int = 10) -> list:
    """言及率が低いクエリを取得（AI回答テキスト付き）。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    qs.prompt_text,
                    qs.category,
                    ar.ai_provider,
                    ma.is_mentioned,
                    ma.sentiment,
                    ma.competitor_mentions,
                    ar.response_text,
                    ma.raw_mention_excerpt
                FROM mention_analysis ma
                JOIN ai_responses ar ON ar.id = ma.ai_response_id
                JOIN query_sets qs   ON qs.id = ar.query_set_id
                WHERE qs.client_id = %s
                  AND DATE_TRUNC('month', ar.retrieved_at) = DATE_TRUNC('month', %s::date)
                ORDER BY ma.is_mentioned ASC, ar.ai_provider
                LIMIT %s
            """, (client_id, period + "-01", limit * 4))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_competitor_frequency(client_id: str, period: str) -> list:
    """競合ブランドの言及頻度を集計。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ma.competitor_mentions,
                    ar.ai_provider
                FROM mention_analysis ma
                JOIN ai_responses ar ON ar.id = ma.ai_response_id
                JOIN query_sets qs   ON qs.id = ar.query_set_id
                WHERE qs.client_id = %s
                  AND DATE_TRUNC('month', ar.retrieved_at) = DATE_TRUNC('month', %s::date)
                  AND ma.competitor_mentions IS NOT NULL
            """, (client_id, period + "-01"))
            return cur.fetchall()


# ─── 競合頻度集計 ────────────────────────────────────────────────────────────

def aggregate_competitors(raw_rows: list) -> dict:
    """competitor_mentions JSONから競合ブランドの言及頻度を集計。"""
    freq = {}
    for (comp_json, provider) in raw_rows:
        if not comp_json:
            continue
        items = comp_json if isinstance(comp_json, list) else json.loads(comp_json)
        for item in items:
            if item.get("is_mentioned"):
                name = item.get("name", "不明")
                freq[name] = freq.get(name, 0) + 1
    return dict(sorted(freq.items(), key=lambda x: -x[1]))


# ─── Claude分析 ─────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """
あなたはGEO（Generative Engine Optimization）の専門コンサルタントです。
以下のデータをもとに、{brand_name}のAI言及率改善に向けた分析と具体的な作業指示を行ってください。

## 月次パフォーマンスデータ（{period}）

### 言及率サマリー
{rate_summary}

### 競合言及頻度
{competitor_summary}

### 言及されなかったクエリのAI回答サンプル（最大10件）
{unmentioned_samples}

## 分析・提案の構成

以下の構成でJSON形式で回答してください：

{{
  "executive_summary": "エグゼクティブサマリー（3〜5文）",
  "strengths": ["現状の強み1", "強み2", ...],
  "issues": [
    {{
      "title": "課題タイトル",
      "detail": "詳細説明",
      "evidence": "根拠となるデータ・回答例"
    }}
  ],
  "recommendations": [
    {{
      "priority": "高/中/低",
      "task": "作業タイトル（20文字以内、動詞で始める）",
      "steps": [
        "手順1: 具体的な実作業（ツール名・URL・ファイル名を明記）",
        "手順2: ...",
        "手順3: ..."
      ],
      "owner": "技術担当 / コンテンツ担当 / SEO担当 / マーケ担当",
      "expected_result": "期待される変化（AI言及率+X%など具体的に）",
      "timeline": "2週間以内 / 翌月まで / 3ヶ月以内"
    }}
  ],
  "next_month_focus": "来月重点的に追跡すべきKPI・観点"
}}

重要制約（トークン超過防止）:
- recommendations は最大4件まで
- steps は各アクション最大4手順まで（1手順60文字以内）
- issues は最大3件まで
- 各フィールドは簡潔に。JSON全体が6000トークン以内に収まること

重要: steps は実際の担当者が迷わず実行できる粒度で記述してください。
抽象的な内容（例:「SEOを改善する」）は不可。具体的な操作を明記してください。
"""


def run_claude_analysis(brand_name: str, period: str,
                        rates: dict, competitor_freq: dict,
                        unmentioned: list) -> dict:
    """Claudeでデータを分析し、改善提案を生成。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("エラー: ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # レート集計テキスト
    rate_lines = []
    total_q = total_m = 0
    for prov, d in rates.items():
        rate_lines.append(
            f"- {PROVIDER_LABELS.get(prov, prov)}: {d['rate']}% "
            f"（{d['mentioned']}/{d['total']}件）"
        )
        total_q += d["total"]; total_m += d["mentioned"]
    overall = round(total_m / total_q * 100, 1) if total_q else 0
    rate_summary = f"総合言及率: {overall}% （{total_m}/{total_q}件）\n" + "\n".join(rate_lines)

    # 競合集計テキスト
    if competitor_freq:
        comp_lines = [f"- {n}: {c}回" for n, c in list(competitor_freq.items())[:5]]
        competitor_summary = "\n".join(comp_lines)
    else:
        competitor_summary = "競合言及なし"

    # 非言及サンプル
    samples = []
    seen = set()
    for row in unmentioned:
        if not row["is_mentioned"] and row["prompt_text"] not in seen:
            seen.add(row["prompt_text"])
            resp_preview = (row["response_text"] or "")[:300].replace("\n", " ")
            samples.append(
                f"クエリ: {row['prompt_text']}\n"
                f"プロバイダー: {PROVIDER_LABELS.get(row['ai_provider'], row['ai_provider'])}\n"
                f"AI回答（抜粋）: {resp_preview}..."
            )
        if len(samples) >= 10:
            break

    unmentioned_text = "\n\n---\n".join(samples) if samples else "非言及サンプルなし"

    prompt = ANALYSIS_PROMPT.format(
        brand_name=brand_name,
        period=period,
        rate_summary=rate_summary,
        competitor_summary=competitor_summary,
        unmentioned_samples=unmentioned_text,
    )

    print(f"  Claude分析中...")
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # JSONブロック抽出
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("  ⚠️ JSON解析失敗。生テキストを返します。")
        return {"raw": raw}


# ─── コンソール表示 ──────────────────────────────────────────────────────────

def print_analysis(brand_name: str, period: str, analysis: dict):
    print(f"\n{'='*60}")
    print(f"  {brand_name} — GEO分析レポート ({period})")
    print(f"{'='*60}")

    if "raw" in analysis:
        print(analysis["raw"])
        return

    print(f"\n【エグゼクティブサマリー】\n{analysis.get('executive_summary','')}")

    if analysis.get("strengths"):
        print("\n【現状の強み】")
        for s in analysis["strengths"]:
            print(f"  ✅ {s}")

    if analysis.get("issues"):
        print("\n【課題】")
        for i, issue in enumerate(analysis["issues"], 1):
            print(f"  {i}. {issue['title']}")
            print(f"     {issue['detail']}")

    if analysis.get("recommendations"):
        print("\n【改善アクション】")
        for rec in analysis["recommendations"]:
            pri = rec.get("priority","")
            icon = {"高":"🔴","中":"🟡","低":"🟢"}.get(pri,"•")
            task = rec.get("task") or rec.get("action","")
            print(f"  {icon} [{pri}] {task}")
            for step in rec.get("steps", []):
                print(f"     {step}")
            print(f"     担当: {rec.get('owner','')} | 期限: {rec.get('timeline','')} | 効果: {rec.get('expected_result','')}")

    if analysis.get("next_month_focus"):
        print(f"\n【来月の注目KPI】\n  {analysis['next_month_focus']}")


# ─── メイン ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GEO改善コンサルテーション分析")
    parser.add_argument("--month", help="分析対象月（例: 2026-07）。省略時は今月")
    parser.add_argument("--client", choices=["dears","arluis","watabe"],
                        help="分析対象クライアント（省略時は全社）")
    parser.add_argument("--no-report", action="store_true",
                        help="レポート生成をスキップ")
    parser.add_argument("--format", choices=["pptx", "word"], default="pptx",
                        help="出力形式（デフォルト: pptx）")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    period = args.month or f"{now.year}-{now.month:02d}"

    client_ids = (
        [CLIENTS[args.client][0]] if args.client
        else ALL_CLIENT_IDS
    )

    print(f"\n📊 GEO改善コンサルテーション分析")
    print(f"   対象月: {period}")
    print(f"   対象ブランド: {', '.join(ID_TO_NAME[c] for c in client_ids)}")

    # 月次レート（全社）
    all_rates = fetch_monthly_rates(period)

    all_results = []
    for cid in client_ids:
        brand_name = ID_TO_NAME[cid]
        print(f"\n🔍 {brand_name} を分析中...")

        rates = all_rates.get(cid, {})
        if not rates:
            print(f"  ⚠️ {period} のデータがありません。スキップします。")
            continue

        # 非言及クエリのAI回答取得
        query_rows = fetch_query_performance(cid, period, limit=10)

        # 競合頻度集計
        comp_raw = fetch_competitor_frequency(cid, period)
        comp_freq = aggregate_competitors(comp_raw)

        # Claude分析
        analysis = run_claude_analysis(
            brand_name, period, rates, comp_freq, query_rows
        )

        # コンソール表示
        print_analysis(brand_name, period, analysis)

        all_results.append({
            "client_id": cid,
            "brand_name": brand_name,
            "rates": rates,
            "comp_freq": comp_freq,
            "analysis": analysis,
        })

    # レポート生成
    if not args.no_report and all_results:
        fmt = args.format
        print(f"\n📄 {'PowerPoint' if fmt == 'pptx' else 'Word'}レポート生成中...")
        try:
            if fmt == "pptx":
                from consultation_slides import generate_slides
                output_dir = os.path.dirname(os.path.abspath(__file__))
                output_path = generate_slides(period, all_results, output_dir)
            else:
                from consultation_report import generate_report
                output_path = generate_report(period, all_results)
            print(f"✅ レポート生成完了: {output_path}")
        except ImportError as e:
            print(f"  ⚠️ モジュールが見つかりません: {e}")
        except Exception as e:
            print(f"  ❌ レポート生成エラー: {e}")

    print(f"\n✅ 分析完了")


if __name__ == "__main__":
    main()
