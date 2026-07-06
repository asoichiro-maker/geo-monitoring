"""
dashboard.py  --  GEO モニタリング ダッシュボード (Streamlit)

起動方法:
    streamlit run dashboard.py

クライアントへの共有:
    起動後に表示される Network URL (例: http://192.168.x.x:8501) をそのまま共有する。
    同一ネットワーク内であれば誰でもアクセス可能。
"""

from __future__ import annotations
import os, sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

def _get_secret(key: str) -> str | None:
    """Streamlit Cloud の st.secrets → ローカル環境変数 の順で取得。"""
    try:
        import streamlit as st
        val = st.secrets.get(key)
        if val:
            return str(val)
    except Exception:
        pass
    return os.environ.get(key)

import streamlit as st

# ─── ページ設定（最初に呼ぶ） ────────────────────────────────────────────────
st.set_page_config(
    page_title="GEO モニタリング | Dears Wedding",
    page_icon="💍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

import plotly.graph_objects as go
import plotly.express as px

sys.path.insert(0, str(Path(__file__).parent))
from mention_saver import MentionSaver

# ─── 定数 ────────────────────────────────────────────────────────────────────
DEFAULT_CLIENT_ID = "a1b2c3d4-0000-0000-0000-000000000001"

# ── 競合クライアント設定 ──────────────────────────────────────────────────────
COMPETITOR_CLIENTS = {
    "a1b2c3d4-0000-0000-0000-000000000001": "Dears Wedding",
    "a1b2c3d4-0000-0000-0000-000000000002": "Arluis",
    "a1b2c3d4-0000-0000-0000-000000000003": "ワタベウエディング",
}
ALL_CLIENT_IDS = list(COMPETITOR_CLIENTS.keys())
BRAND_COLORS = {
    "a1b2c3d4-0000-0000-0000-000000000001": "#C9A84C",  # Dears Wedding (gold)
    "a1b2c3d4-0000-0000-0000-000000000002": "#4A90D9",  # Arluis (blue)
    "a1b2c3d4-0000-0000-0000-000000000003": "#7B68EE",  # ワタベ (purple)
}
PROVIDERS   = ["chatgpt", "gemini", "perplexity", "ai_overview"]
LABELS      = {"chatgpt":"ChatGPT","gemini":"Gemini",
               "perplexity":"Perplexity","ai_overview":"AI Overview"}
ICONS       = {"chatgpt":"🤖","gemini":"✨","perplexity":"🔍","ai_overview":"🌐"}
COLOR_PRI   = "#1A3A5C"
COLOR_ACC   = "#C9A84C"
COLOR_RED   = "#C00000"
COLOR_GREEN = "#2E8B57"
PROVIDER_COLORS = {
    "chatgpt":"#10A37F","gemini":"#4285F4",
    "perplexity":"#6366F1","ai_overview":"#EA4335",
}

SPREADSHEET_ID = _get_secret("GEO_SPREADSHEET_ID") or ""
SHEETS_URL = (f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
              if SPREADSHEET_ID else "")

# ─── スタイル ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .metric-card {
    background: white; border-radius: 12px; padding: 20px 24px;
    border-left: 5px solid #C9A84C;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }
  .metric-label { font-size: 13px; color: #666; font-weight: 600; margin-bottom: 4px; }
  .metric-value { font-size: 36px; font-weight: 800; color: #1A3A5C; line-height: 1.1; }
  .metric-sub   { font-size: 12px; color: #999; margin-top: 4px; }
  .alert-box    { background:#FFF5F5; border-left:4px solid #C00000;
                  border-radius:8px; padding:12px 16px; margin:4px 0; }
  .ok-box       { background:#F0FFF4; border-left:4px solid #2E8B57;
                  border-radius:8px; padding:12px 16px; }
  h1,h2,h3 { color: #1A3A5C !important; }
</style>
""", unsafe_allow_html=True)


# ─── データ取得（キャッシュ: 5分） ───────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def load_mention_rate(db_url, client_id, year, month):
    saver = MentionSaver(db_url)
    return saver.fetch_mention_rate(client_id, year, month)

@st.cache_data(ttl=300, show_spinner=False)
def load_analysis(db_url, client_id, limit=500):
    saver = MentionSaver(db_url)
    return saver.fetch_recent_analysis(client_id, limit=limit)

@st.cache_data(ttl=300, show_spinner=False)
def load_all_brands_mention_rate(db_url, year, month):
    """全クライアントの言及率を取得（競合比較用）"""
    saver = MentionSaver(db_url)
    return saver.fetch_all_brands_mention_rate(ALL_CLIENT_IDS, year, month)

@st.cache_data(ttl=300, show_spinner=False)
def load_monthly_trend(db_url, client_id):
    """過去6ヶ月分の言及率を取得"""
    saver = MentionSaver(db_url)
    now = datetime.now(timezone.utc)
    result = []
    for delta in range(5, -1, -1):
        month = now.month - delta
        year  = now.year
        while month <= 0:
            month += 12; year -= 1
        mr = saver.fetch_mention_rate(client_id, year, month)
        for p in PROVIDERS:
            stats = mr.get(p, {})
            if stats.get("total", 0) > 0:
                result.append({
                    "period": f"{year}-{month:02d}",
                    "provider": LABELS[p],
                    "mention_rate": round(stats.get("mention_rate", 0) * 100, 1),
                    "total": stats.get("total", 0),
                })
    return result


# ─── メイン ──────────────────────────────────────────────────────────────────
def main():
    db_url = _get_secret("DATABASE_URL")
    if not db_url:
        st.error("DATABASE_URL が設定されていません。.env ファイルまたは Streamlit Cloud の Secrets を確認してください。")
        st.stop()

    # ── ヘッダー ──
    col_logo, col_title, col_link = st.columns([1, 6, 2])
    with col_title:
        st.markdown(f"<h1 style='margin-bottom:0'>💍 GEO モニタリング ダッシュボード</h1>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:#666;margin-top:4px'>Dears Wedding | AI言及率・誤情報 リアルタイム監視 ＋ 競合比較（Arluis / ワタベウエディング）</p>", unsafe_allow_html=True)
    with col_link:
        st.markdown("<br>", unsafe_allow_html=True)
        if SHEETS_URL:
            st.link_button("📊 詳細データ (Sheets)", SHEETS_URL, use_container_width=True)

    st.divider()

    # ── 年月セレクター ──
    now = datetime.now(timezone.utc)
    sel_col1, sel_col2, _, refresh_col = st.columns([1.5, 1.5, 5, 1.5])
    with sel_col1:
        year = st.selectbox("年", list(range(now.year - 1, now.year + 1)), index=1)
    with sel_col2:
        month = st.selectbox("月", list(range(1, 13)), index=now.month - 1)
    with refresh_col:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 更新", use_container_width=True):
            st.cache_data.clear()

    period_label = f"{year}年{month}月"

    # ── データ取得 ──
    with st.spinner("データ取得中..."):
        mention_rate = load_mention_rate(db_url, DEFAULT_CLIENT_ID, year, month)
        analysis     = load_analysis(db_url, DEFAULT_CLIENT_ID, limit=1000)
        trend_data   = load_monthly_trend(db_url, DEFAULT_CLIENT_ID)

    # 当月の分析データ絞り込み
    pfx = f"{year}-{month:02d}"
    month_rows = [r for r in analysis
                  if str(r.get("responded_at","")).startswith(pfx)
                  or str(r.get("created_at","")).startswith(pfx)] or analysis
    misinfo_rows = [r for r in month_rows if r.get("misinformation_flag")]

    # 集計
    total_q = sum(mention_rate.get(p,{}).get("total",0)    for p in PROVIDERS)
    total_m = sum(mention_rate.get(p,{}).get("mentioned",0) for p in PROVIDERS)
    overall = total_m / total_q * 100 if total_q else 0

    # ══════════════════════════════════════════
    # KPI カード（4プロバイダー + 総合）
    # ══════════════════════════════════════════
    st.markdown(f"### 📈 言及率サマリー（{period_label}）")
    cols = st.columns(5)

    # 総合
    with cols[0]:
        st.markdown(f"""
        <div class="metric-card" style="border-left-color:{COLOR_PRI}">
          <div class="metric-label">🏆 総合言及率</div>
          <div class="metric-value" style="color:{COLOR_PRI}">{overall:.1f}%</div>
          <div class="metric-sub">{total_m} / {total_q} クエリ</div>
        </div>""", unsafe_allow_html=True)

    for i, p in enumerate(PROVIDERS):
        stats = mention_rate.get(p, {})
        rate  = stats.get("mention_rate", 0) * 100
        total = stats.get("total", 0)
        mentioned = stats.get("mentioned", 0)
        bar_color = PROVIDER_COLORS[p]
        with cols[i + 1]:
            st.markdown(f"""
            <div class="metric-card" style="border-left-color:{bar_color}">
              <div class="metric-label">{ICONS[p]} {LABELS[p]}</div>
              <div class="metric-value" style="color:{bar_color}">{rate:.1f}%</div>
              <div class="metric-sub">{mentioned} / {total} クエリ</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════
    # グラフ 2列
    # ══════════════════════════════════════════
    chart_col1, chart_col2 = st.columns([3, 2])

    # ── トレンド折れ線 ──
    with chart_col1:
        st.markdown("### 📉 言及率トレンド（直近6ヶ月）")
        if trend_data:
            import pandas as pd
            df = pd.DataFrame(trend_data)
            fig = px.line(df, x="period", y="mention_rate", color="provider",
                          markers=True, labels={"period":"期間","mention_rate":"言及率 (%)","provider":"プロバイダー"},
                          color_discrete_map={LABELS[p]: PROVIDER_COLORS[p] for p in PROVIDERS})
            fig.update_layout(
                plot_bgcolor="white", paper_bgcolor="white",
                font_color="#333", legend_title="",
                yaxis=dict(range=[0, 100], ticksuffix="%", gridcolor="#eee"),
                xaxis=dict(gridcolor="#eee"),
                margin=dict(l=10, r=10, t=10, b=10),
                height=280,
            )
            fig.update_traces(line_width=2.5, marker_size=7)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("トレンドデータがありません。")

    # ── プロバイダー比較バー ──
    with chart_col2:
        st.markdown("### 🏅 プロバイダー比較")
        rates = [(LABELS[p], mention_rate.get(p,{}).get("mention_rate",0)*100,
                  PROVIDER_COLORS[p]) for p in PROVIDERS]
        rates.sort(key=lambda x: -x[1])
        fig2 = go.Figure(go.Bar(
            x=[r[1] for r in rates],
            y=[r[0] for r in rates],
            orientation="h",
            marker_color=[r[2] for r in rates],
            text=[f"{r[1]:.1f}%" for r in rates],
            textposition="outside",
        ))
        fig2.update_layout(
            plot_bgcolor="white", paper_bgcolor="white",
            font_color="#333",
            xaxis=dict(range=[0, 105], ticksuffix="%", gridcolor="#eee"),
            yaxis=dict(autorange="reversed"),
            margin=dict(l=10, r=40, t=10, b=10),
            height=280,
            showlegend=False,
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ══════════════════════════════════════════
    # 誤情報アラート
    # ══════════════════════════════════════════
    st.markdown("### ⚠️ 誤情報アラート")
    if misinfo_rows:
        st.markdown(f"""
        <div class="alert-box">
          <strong>🚨 {len(misinfo_rows)}件の誤情報が検出されています。早急な対策を推奨します。</strong>
        </div>
        <br>""", unsafe_allow_html=True)

        import pandas as pd
        df_mis = pd.DataFrame([{
            "AI": LABELS.get(r.get("ai_provider",""), r.get("ai_provider","")),
            "クエリ": (r.get("prompt_text",""))[:60],
            "検出内容": (r.get("misinformation_detail",""))[:120],
        } for r in misinfo_rows])
        st.dataframe(df_mis, use_container_width=True, hide_index=True,
                     column_config={
                         "AI":     st.column_config.TextColumn(width="small"),
                         "クエリ": st.column_config.TextColumn(width="medium"),
                         "検出内容":st.column_config.TextColumn(width="large"),
                     })
    else:
        st.markdown("""
        <div class="ok-box">✅ 今月の誤情報検出はありません。</div>
        """, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════
    # 最近のクエリ詳細（折りたたみ）
    # ══════════════════════════════════════════
    with st.expander(f"🔍 クエリ詳細データ（{len(month_rows)}件）"):
        import pandas as pd
        df_det = pd.DataFrame([{
            "AI":      LABELS.get(r.get("ai_provider",""), r.get("ai_provider","")),
            "クエリ":  (r.get("prompt_text",""))[:80],
            "言及":    "✅" if r.get("brand_mentioned") else "❌",
            "誤情報":  "⚠️" if r.get("misinformation_flag") else "—",
            "信頼スコア": r.get("confidence_score",""),
        } for r in month_rows])
        st.dataframe(df_det, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════
    # 競合比較セクション
    # ══════════════════════════════════════════
    st.divider()
    st.markdown("### 🏆 競合比較 — AI言及率 ブランド別")
    st.caption("同一クエリセットを3社に適用し、AIがどのブランドを推薦するかを比較します。")

    with st.spinner("競合データ取得中..."):
        all_brands_data = load_all_brands_mention_rate(db_url, year, month)

    import pandas as pd

    # ── 総合言及率 比較カード ──
    brand_cols = st.columns(len(COMPETITOR_CLIENTS))
    for col, (cid, brand_name) in zip(brand_cols, COMPETITOR_CLIENTS.items()):
        brand_data = all_brands_data.get(cid, {})
        b_total = sum(brand_data.get(p, {}).get("total", 0) for p in PROVIDERS)
        b_mentioned = sum(brand_data.get(p, {}).get("mentioned", 0) for p in PROVIDERS)
        b_overall = b_mentioned / b_total * 100 if b_total else 0
        color = BRAND_COLORS[cid]
        is_self = (cid == DEFAULT_CLIENT_ID)
        with col:
            st.markdown(f"""
            <div class="metric-card" style="border-left-color:{color}">
              <div class="metric-label">{'⭐ ' if is_self else ''}{brand_name}</div>
              <div class="metric-value" style="color:{color}">{b_overall:.1f}%</div>
              <div class="metric-sub">{b_mentioned} / {b_total} クエリ {'← 自社' if is_self else ''}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── プロバイダー別 グループドバーチャート ──
    comp_chart_col1, comp_chart_col2 = st.columns([3, 2])

    with comp_chart_col1:
        st.markdown("#### プロバイダー別 × ブランド別 言及率")
        rows = []
        for cid, brand_name in COMPETITOR_CLIENTS.items():
            brand_data = all_brands_data.get(cid, {})
            for p in PROVIDERS:
                rate = brand_data.get(p, {}).get("mention_rate", 0) * 100
                rows.append({"ブランド": brand_name, "AI": LABELS[p], "言及率": round(rate, 1)})
        if rows:
            df_comp = pd.DataFrame(rows)
            brand_color_map = {v: BRAND_COLORS[k] for k, v in COMPETITOR_CLIENTS.items()}
            fig_comp = px.bar(
                df_comp, x="AI", y="言及率", color="ブランド", barmode="group",
                color_discrete_map=brand_color_map,
                labels={"言及率": "言及率 (%)"},
                text_auto=".1f",
            )
            fig_comp.update_layout(
                plot_bgcolor="white", paper_bgcolor="white",
                font_color="#333", legend_title="ブランド",
                yaxis=dict(range=[0, 105], ticksuffix="%", gridcolor="#eee"),
                xaxis=dict(gridcolor="#eee"),
                margin=dict(l=10, r=10, t=10, b=10),
                height=300,
            )
            fig_comp.update_traces(textposition="outside")
            st.plotly_chart(fig_comp, use_container_width=True)
        else:
            st.info("競合データがありません。先にパイプラインを実行してください。")

    with comp_chart_col2:
        st.markdown("#### 総合言及率 ランキング")
        rank_rows = []
        for cid, brand_name in COMPETITOR_CLIENTS.items():
            brand_data = all_brands_data.get(cid, {})
            b_total = sum(brand_data.get(p, {}).get("total", 0) for p in PROVIDERS)
            b_mentioned = sum(brand_data.get(p, {}).get("mentioned", 0) for p in PROVIDERS)
            b_overall = b_mentioned / b_total * 100 if b_total else 0
            rank_rows.append({"brand": brand_name, "rate": round(b_overall, 1), "color": BRAND_COLORS[cid]})
        rank_rows.sort(key=lambda x: -x["rate"])

        fig_rank = go.Figure(go.Bar(
            x=[r["rate"] for r in rank_rows],
            y=[r["brand"] for r in rank_rows],
            orientation="h",
            marker_color=[r["color"] for r in rank_rows],
            text=[f"{r['rate']:.1f}%" for r in rank_rows],
            textposition="outside",
        ))
        fig_rank.update_layout(
            plot_bgcolor="white", paper_bgcolor="white",
            font_color="#333",
            xaxis=dict(range=[0, 105], ticksuffix="%", gridcolor="#eee"),
            yaxis=dict(autorange="reversed"),
            margin=dict(l=10, r=40, t=10, b=10),
            height=300,
            showlegend=False,
        )
        st.plotly_chart(fig_rank, use_container_width=True)

    # ── フッター ──
    st.divider()
    st.markdown(
        f"<p style='text-align:center;color:#aaa;font-size:12px'>"
        f"GEO モニタリング SaaS | Dears Wedding | "
        f"最終更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
