"""
accounting_dashboard.py  --  会計管理ダッシュボード (Streamlit)

起動方法:
    streamlit run accounting_dashboard.py

タブ:
    概要         : 前月確定売上・当月見込・年度累計・パイプライン、月次推移グラフ
    案件管理表   : 案件の一覧・編集・新規登録
    請求書       : 請求書フォルダの取込・要確認の修正
    月次確定     : 確定前チェック（請求漏れ・要確認）→ 確定 / 取消
    アシスタント : 自然言語で受注・失注・確定などを指示
    操作ログ     : 全操作の履歴
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

import streamlit as st

st.set_page_config(page_title="会計管理ダッシュボード", page_icon="📒", layout="wide")

import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent))
from accounting import closing, deals, invoices
from accounting.db import session

# ─── 定数 ────────────────────────────────────────────────────────────────────
# 色は役割で固定（系列の順位で変えない）。暗色背景向けステップ・検証済み。
C_ACTUAL   = "#3987e5"  # 確定実績
C_WON      = "#d95926"  # 受注見込（未確定月）
C_WEIGHTED = "#199e70"  # 加重パイプライン（見込・提案中 × 確度）
C_TARGET   = "#9aa0a6"  # 目標（中立色の破線）
C_SURFACE  = "#0E1117"
FISCAL_START = int(os.environ.get("ACCOUNTING_FISCAL_START_MONTH", "4"))


def _secret(key: str) -> str | None:
    try:
        v = st.secrets.get(key)
        if v:
            return str(v)
    except Exception:
        pass
    return os.environ.get(key)


for k in ("ANTHROPIC_API_KEY", "INVOICE_DIR", "ACCOUNTING_DB_PATH"):
    if not os.environ.get(k) and _secret(k):
        os.environ[k] = _secret(k)


def yen(v) -> str:
    return "—" if v is None or pd.isna(v) else f"¥{int(v):,}"


def man(v) -> str:
    return "—" if v is None else f"{v / 10000:,.0f}万円"


def fiscal_year_months(today: date | None = None) -> list[str]:
    t = today or date.today()
    y = t.year if t.month >= FISCAL_START else t.year - 1
    return closing.fiscal_months(f"{y:04d}-{FISCAL_START:02d}", 12)


# ─── データ取得 ──────────────────────────────────────────────────────────────

def load_all() -> dict:
    with session() as conn:
        months = fiscal_year_months()
        return {
            "months": months,
            "deals": deals.list_deals(conn),
            "invoices": invoices.list_invoices(conn),
            "actuals": closing.actuals_by_month(conn),
            "forecast": deals.forecast(conn, months),
            "targets": {r["month"]: r["target"] for r in conn.execute("SELECT * FROM monthly_targets")},
            "log": [dict(r) for r in conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT 300")],
        }


data = load_all()
cur_m, prev_m = deals.this_month(), deals.prev_month()

st.title("📒 会計管理ダッシュボード")
cap = [f"年度: {data['months'][0]} 〜 {data['months'][-1]}"]
cap.append(f"請求書フォルダ: {os.environ.get('INVOICE_DIR') or '未設定（INVOICE_DIR）'}")
cap.append("Claude: " + ("有効" if os.environ.get("ANTHROPIC_API_KEY") else "未設定（正規表現で抽出）"))
st.caption(" ｜ ".join(cap))

tab_overview, tab_deals, tab_inv, tab_close, tab_chat, tab_log = st.tabs(
    ["概要", "案件管理表", "請求書", "月次確定", "アシスタント", "操作ログ"])

# ─── 概要 ────────────────────────────────────────────────────────────────────
with tab_overview:
    acts, fc, tg = data["actuals"], data["forecast"], data["targets"]
    prev_actual = acts.get(prev_m)
    prev_target = tg.get(prev_m)
    cur_fc = fc.get(cur_m, {"confirmed": 0, "weighted": 0})
    ytd = sum(v for m, v in acts.items() if m in data["months"])
    ytd_target = sum(tg.get(m, 0) for m in data["months"] if m in acts)
    pipeline = sum(deals.contract_value(d) for d in data["deals"] if d["status"] in deals.OPEN_STATUSES)
    pipeline_w = sum(round(deals.contract_value(d) * d["probability"] / 100)
                     for d in data["deals"] if d["status"] in deals.OPEN_STATUSES)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"前月売上（{prev_m}）", man(prev_actual) if prev_actual is not None else "未確定",
              delta=(f"目標比 {prev_actual / prev_target:.0%}" if prev_actual is not None and prev_target else None),
              delta_color="off")
    c2.metric(f"当月見込（{cur_m}）", man(cur_fc["confirmed"]),
              delta=f"+加重パイプライン {man(cur_fc['weighted'])}", delta_color="off")
    c3.metric("年度累計（確定分）", man(ytd),
              delta=(f"目標比 {ytd / ytd_target:.0%}" if ytd_target else None), delta_color="off")
    c4.metric("パイプライン（見込・提案中）", man(pipeline), delta=f"確度加重 {man(pipeline_w)}", delta_color="off")

    # 月次推移: 確定月は実績、未確定月は受注見込＋加重パイプラインを積み上げ。目標は破線。
    months = data["months"]
    labels = [f"{int(m[5:])}月" for m in months]
    actual_y = [acts.get(m) for m in months]
    won_y = [None if m in acts else fc[m]["confirmed"] for m in months]
    w_y = [None if m in acts else fc[m]["weighted"] for m in months]
    target_y = [tg.get(m) for m in months]

    fig = go.Figure()
    bar = dict(marker_line_color=C_SURFACE, marker_line_width=2)
    fig.add_bar(name="確定実績", x=labels, y=actual_y, marker_color=C_ACTUAL, **bar,
                hovertemplate="確定実績 ¥%{y:,.0f}<extra></extra>")
    fig.add_bar(name="受注見込", x=labels, y=won_y, marker_color=C_WON, **bar,
                hovertemplate="受注見込 ¥%{y:,.0f}<extra></extra>")
    fig.add_bar(name="加重パイプライン", x=labels, y=w_y, marker_color=C_WEIGHTED, **bar,
                hovertemplate="加重パイプライン ¥%{y:,.0f}<extra></extra>")
    if any(t is not None for t in target_y):
        fig.add_scatter(name="目標", x=labels, y=target_y, mode="lines+markers",
                        line=dict(color=C_TARGET, width=2, dash="dash"), marker=dict(size=8),
                        hovertemplate="目標 ¥%{y:,.0f}<extra></extra>")
    fig.update_layout(
        barmode="stack", bargap=0.35, height=380, hovermode="x unified",
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=1.12, x=0, traceorder="normal"),
        yaxis=dict(title="売上（税抜・円）", tickformat=",.0f", gridcolor="rgba(255,255,255,0.08)"),
        xaxis=dict(showgrid=False),
    )
    st.subheader("月次売上推移（年度）")
    st.plotly_chart(fig, use_container_width=True)

    table = pd.DataFrame({
        "月": months,
        "状態": ["確定" if m in acts else "未確定" for m in months],
        "確定実績": actual_y,
        "受注見込": [fc[m]["confirmed"] for m in months],
        "加重パイプライン": [fc[m]["weighted"] for m in months],
        "目標": target_y,
    })
    table["目標差"] = [(a - t) if a is not None and t else None for a, t in zip(actual_y, target_y)]
    with st.expander("月次推移（表）"):
        st.dataframe(table, hide_index=True, use_container_width=True,
                     column_config={c: st.column_config.NumberColumn(format="yen")
                                    for c in ["確定実績", "受注見込", "加重パイプライン", "目標", "目標差"]})

    with st.expander("月次目標を設定"):
        with st.form("target"):
            tc1, tc2 = st.columns(2)
            t_month = tc1.selectbox("月", months, index=months.index(cur_m) if cur_m in months else 0)
            t_amt = tc2.number_input("目標（税抜・円）", min_value=0, step=100000, value=int(tg.get(t_month) or 0))
            if st.form_submit_button("保存"):
                with session() as conn:
                    deals.set_target(conn, t_month, t_amt)
                st.rerun()

# ─── 案件管理表 ──────────────────────────────────────────────────────────────
with tab_deals:
    df = pd.DataFrame(data["deals"])
    s1, s2 = st.columns([3, 1])
    status_filter = s1.multiselect("ステータス", deals.STATUSES, default=["見込", "提案中", "受注"])
    s2.metric("受注率（受注＋完了 / 決着済み）",
              (lambda won, lost: f"{won / (won + lost):.0%}" if won + lost else "—")(
                  sum(1 for d in data["deals"] if d["status"] in ("受注", "完了")),
                  sum(1 for d in data["deals"] if d["status"] == "失注")))

    if df.empty:
        st.info("案件がありません。下のフォームかアシスタントタブから登録してください。")
    else:
        df["契約総額"] = [deals.contract_value(d) for d in data["deals"]]
        view = df[df["status"].isin(status_filter)].copy()
        cols = ["code", "client_name", "title", "status", "billing_type", "amount", "months", "契約総額",
                "start_month", "probability", "owner", "won_date", "lost_date", "lost_reason", "notes"]
        edited = st.data_editor(
            view[cols], hide_index=True, use_container_width=True, key="deal_editor",
            disabled=["code", "契約総額", "won_date", "lost_date"],
            column_config={
                "code": "案件コード", "client_name": "顧客", "title": "案件名",
                "status": st.column_config.SelectboxColumn("ステータス", options=deals.STATUSES, required=True),
                "billing_type": st.column_config.SelectboxColumn("請求形態", options=deals.BILLING_TYPES, required=True),
                "amount": st.column_config.NumberColumn("金額(税抜)", format="yen", help="月額は1ヶ月分"),
                "months": st.column_config.NumberColumn("月数", min_value=1),
                "契約総額": st.column_config.NumberColumn(format="yen"),
                "start_month": st.column_config.TextColumn("計上開始月", help="YYYY-MM"),
                "probability": st.column_config.NumberColumn("確度%", min_value=0, max_value=100),
                "owner": "担当", "won_date": "受注日", "lost_date": "失注日",
                "lost_reason": "失注理由", "notes": "メモ",
            },
        )
        if st.button("変更を保存", type="primary"):
            editable = ["client_name", "title", "status", "billing_type", "amount", "months",
                        "start_month", "probability", "owner", "lost_reason", "notes"]
            orig = view.set_index("code")
            n, errs = 0, []
            with session() as conn:
                for _, row in edited.iterrows():
                    before = orig.loc[row["code"]]
                    diff = {c: (None if pd.isna(row[c]) else row[c]) for c in editable
                            if not (pd.isna(row[c]) and pd.isna(before[c])) and row[c] != before[c]}
                    if not diff:
                        continue
                    try:
                        st_new = diff.pop("status", None)
                        if st_new == "受注":
                            deals.mark_won(conn, row["code"], **diff)
                        elif st_new == "失注":
                            deals.mark_lost(conn, row["code"], reason=diff.pop("lost_reason", None))
                            if diff:
                                deals.update_deal(conn, row["code"], **diff)
                        else:
                            if st_new:
                                diff["status"] = st_new
                            deals.update_deal(conn, row["code"], **diff)
                        n += 1
                    except deals.DealError as e:
                        errs.append(f"{row['code']}: {e}")
            for e in errs:
                st.error(e)
            if n:
                st.success(f"{n} 件更新しました")
                st.rerun()

    with st.expander("＋ 新規案件を登録"):
        with st.form("new_deal", clear_on_submit=True):
            f1, f2, f3 = st.columns(3)
            client = f1.text_input("顧客名*")
            title = f2.text_input("案件名*")
            status = f3.selectbox("ステータス", deals.STATUSES, index=1)
            g1, g2, g3, g4 = st.columns(4)
            btype = g1.selectbox("請求形態", deals.BILLING_TYPES)
            amount = g2.number_input("金額（税抜・円／月額は1ヶ月分）", min_value=0, step=10000)
            months_n = g3.number_input("月数（月額のみ）", min_value=1, value=1)
            start = g4.text_input("計上開始月 (YYYY-MM)", value=cur_m)
            h1, h2, h3 = st.columns(3)
            prob = h1.slider("確度%", 0, 100, 50)
            owner = h2.text_input("担当")
            notes = h3.text_input("メモ")
            if st.form_submit_button("登録", type="primary"):
                try:
                    with session() as conn:
                        d = deals.create_deal(conn, client, title, amount=amount, billing_type=btype,
                                              months=months_n, start_month=start or None, status=status,
                                              probability=None if status in ("受注", "失注", "完了") else prob,
                                              owner=owner or None, notes=notes or None)
                    st.success(f"{d['code']} を登録しました")
                    st.rerun()
                except deals.DealError as e:
                    st.error(str(e))

# ─── 請求書 ──────────────────────────────────────────────────────────────────
with tab_inv:
    b1, b2 = st.columns([1, 3])
    if b1.button("📂 請求書フォルダを読み込む", type="primary"):
        try:
            with st.spinner("請求書を読み込んでいます…"):
                with session() as conn:
                    r = invoices.scan_folder(conn)
            st.success(f"新規 {len(r['added'])} 件 / 取込済み {r['skipped']} 件")
            for e in r["errors"]:
                st.error(e)
            for i in r["needs_review"]:
                st.warning(f"要確認: {Path(i['file_path']).name} — {i['review_note']}")
        except (ValueError, FileNotFoundError) as e:
            st.error(str(e))

    inv = pd.DataFrame(data["invoices"])
    if inv.empty:
        st.info("請求書はまだ取り込まれていません。")
    else:
        closed = set(data["actuals"])
        month_opts = sorted(inv["billing_month"].dropna().unique(), reverse=True)
        sel = b2.selectbox("計上月", ["すべて"] + list(month_opts))
        view = inv if sel == "すべて" else inv[inv["billing_month"] == sel]
        view = view.assign(ファイル=view["file_path"].map(lambda p: Path(p).name),
                           確定=view["billing_month"].isin(closed))
        deal_opts = {d["id"]: f"{d['code']} {d['client_name']} / {d['title']}" for d in data["deals"]}
        cols = ["id", "確定", "ファイル", "billing_month", "client_name", "invoice_no", "issue_date",
                "subtotal", "tax", "total", "deal_id", "needs_review", "review_note", "extracted_by"]
        view = view[cols].astype({"needs_review": bool})
        ed = st.data_editor(
            view, hide_index=True, use_container_width=True, key="inv_editor",
            disabled=["id", "確定", "ファイル", "extracted_by"],
            column_config={
                "billing_month": st.column_config.TextColumn("計上月"),
                "client_name": "請求先", "invoice_no": "請求書番号", "issue_date": "請求日",
                "subtotal": st.column_config.NumberColumn("税抜", format="yen"),
                "tax": st.column_config.NumberColumn("消費税", format="yen"),
                "total": st.column_config.NumberColumn("税込", format="yen"),
                "deal_id": st.column_config.SelectboxColumn(
                    "案件", options=list(deal_opts), format_func=lambda i: deal_opts.get(i, "—")),
                "needs_review": st.column_config.CheckboxColumn("要確認"),
                "review_note": "確認メモ", "extracted_by": "抽出",
            },
        )
        if st.button("請求書の修正を保存"):
            fields = ["billing_month", "client_name", "invoice_no", "issue_date", "subtotal", "tax",
                      "total", "deal_id", "needs_review", "review_note"]
            orig = view.set_index("id")
            n, errs = 0, []
            with session() as conn:
                for _, row in ed.iterrows():
                    before = orig.loc[row["id"]]
                    diff = {}
                    for c in fields:
                        a, b = row[c], before[c]
                        if (pd.isna(a) and pd.isna(b)) or a == b:
                            continue
                        if pd.isna(a):
                            a = None
                        elif c in ("subtotal", "tax", "total", "deal_id", "needs_review"):
                            a = int(a)
                        diff[c] = a
                    if diff:
                        try:
                            invoices.update_invoice(conn, int(row["id"]), **diff)
                            n += 1
                        except ValueError as e:
                            errs.append(f"#{row['id']}: {e}")
            for e in errs:
                st.error(e)
            if n:
                st.success(f"{n} 件更新しました")
                st.rerun()

# ─── 月次確定 ────────────────────────────────────────────────────────────────
with tab_close:
    opts = sorted({prev_m, cur_m, *data["months"], *data["actuals"]}, reverse=True)
    month = st.selectbox("対象月", opts, index=opts.index(prev_m))
    with session() as conn:
        chk = closing.precheck(conn, month)

    if chk["closed"]:
        st.success(f"✅ {month} は確定済みです")
    else:
        st.info(f"🕒 {month} は未確定です")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("売上（税抜）", yen(chk["revenue"]))
    m2.metric("売上（税込）", yen(chk["revenue_incl_tax"]))
    m3.metric("請求書", f"{chk['invoice_count']} 件")
    m4.metric("受注ベース見込", yen(chk["forecast_confirmed"]),
              delta=(f"差 {yen(chk['revenue'] - chk['forecast_confirmed'])}"), delta_color="off")

    if chk["needs_review"]:
        st.warning("⚠ 要確認の請求書があります（請求書タブで修正してください）")
        st.dataframe(pd.DataFrame(chk["needs_review"])[["id", "client_name", "subtotal", "review_note"]].rename(columns={"client_name": "請求先", "subtotal": "税抜", "review_note": "確認内容"}),
                     hide_index=True)
    if chk["unbilled_deals"]:
        st.warning("⚠ 請求漏れ候補（計上予定があるのに請求書がない受注案件）")
        st.dataframe(pd.DataFrame(chk["unbilled_deals"])[["code", "client_name", "title", "planned"]].rename(columns={"code": "案件コード", "client_name": "顧客", "title": "案件名"}),
                     hide_index=True, column_config={"planned": st.column_config.NumberColumn("予定", format="yen")})
    if chk["unmatched_invoices"]:
        st.caption(f"案件に紐付いていない請求書: {len(chk['unmatched_invoices'])} 件（売上には含まれます）")

    if not chk["closed"]:
        allow = st.checkbox("要確認の請求書が残っていても確定する", value=False) if chk["needs_review"] else False
        if st.button(f"{month} の売上実績を確定する", type="primary"):
            try:
                with session() as conn:
                    r = closing.close_month(conn, month, allow_review=allow)
                st.success(f"{month} を確定しました（{yen(r['revenue'])}）"
                           + (f" 完了にした案件: {', '.join(r['completed_deals'])}" if r["completed_deals"] else ""))
                st.rerun()
            except closing.ClosingError as e:
                st.error(str(e))
    else:
        reason = st.text_input("取消理由")
        if st.button(f"{month} の確定を取り消す"):
            with session() as conn:
                closing.reopen_month(conn, month, reason)
            st.rerun()

# ─── アシスタント ────────────────────────────────────────────────────────────
with tab_chat:
    st.caption("例: 「ABC商事のサイト改修 300万で受注、11月納品」「XYZの広告運用は失注、予算凍結」"
               "「みらいのSEO支援 月額25万×6ヶ月 10月開始で受注」「先月分を確定して」")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.warning("ANTHROPIC_API_KEY が未設定のためアシスタントは使えません（.env か secrets.toml に設定）")
    st.session_state.setdefault("chat_log", [])
    st.session_state.setdefault("chat_history", [])
    for role, text in st.session_state["chat_log"]:
        st.chat_message(role).write(text)
    prompt = st.chat_input("受注・失注・金額変更・確定などを話しかけてください")
    if prompt:
        st.chat_message("user").write(prompt)
        from accounting.assistant import ask
        with st.spinner("処理中…"):
            try:
                with session() as conn:
                    reply, hist = ask(conn, prompt, st.session_state["chat_history"])
                st.session_state["chat_history"] = hist
            except Exception as e:
                reply = f"エラー: {e}"
        st.session_state["chat_log"] += [("user", prompt), ("assistant", reply)]
        st.rerun()

# ─── 操作ログ ────────────────────────────────────────────────────────────────
with tab_log:
    if data["log"]:
        st.dataframe(pd.DataFrame(data["log"])[["at", "action", "detail"]], hide_index=True,
                     use_container_width=True, column_config={"at": "日時", "action": "操作", "detail": "内容"})
    else:
        st.info("操作履歴はまだありません。")
