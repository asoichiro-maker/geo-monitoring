"""
accounting/closing.py
---------------------
月次締め（売上実績の確定）。

close_month() は対象月の請求書を集計して monthly_closings / revenue_actuals に
スナップショットを保存し、その月をロックする（以後その月の請求書は修正不可）。
確定前に以下をチェックする:
  - 要確認（金額・計上月が読めない等）の請求書が残っていないか
  - 計上予定があるのに請求書が無い受注案件（請求漏れ候補）
"""

from __future__ import annotations

import sqlite3

from .db import log
from .deals import add_months, forecast, list_deals, planned_revenue, prev_month, validate_month
from .invoices import list_invoices, scan_folder


class ClosingError(RuntimeError):
    pass


def is_closed(conn: sqlite3.Connection, month: str) -> bool:
    return conn.execute("SELECT 1 FROM monthly_closings WHERE month = ?", (month,)).fetchone() is not None


def precheck(conn: sqlite3.Connection, month: str) -> dict:
    """確定前チェック。確定しなくても呼べる（ダッシュボードのプレビュー用）。"""
    validate_month(month)
    invs = list_invoices(conn, month)
    review = [i for i in invs if i["needs_review"]]
    billed_deals = {i["deal_id"] for i in invs if i["deal_id"]}
    unbilled = []
    for d in list_deals(conn):
        if d["status"] not in ("受注", "完了"):
            continue
        plan = planned_revenue(d)
        if month in plan and d["id"] not in billed_deals:
            unbilled.append({**d, "planned": plan[month]})
    revenue = sum(i["subtotal"] or 0 for i in invs)
    revenue_incl = sum(i["total"] or 0 for i in invs)
    fc = forecast(conn, [month])[month]
    tgt = conn.execute("SELECT target FROM monthly_targets WHERE month = ?", (month,)).fetchone()
    return {
        "month": month,
        "closed": is_closed(conn, month),
        "invoice_count": len(invs),
        "revenue": revenue,
        "revenue_incl_tax": revenue_incl,
        "forecast_confirmed": fc["confirmed"],
        "target": tgt["target"] if tgt else None,
        "needs_review": review,
        "unbilled_deals": unbilled,
        "unmatched_invoices": [i for i in invs if not i["deal_id"]],
    }


def close_month(conn: sqlite3.Connection, month: str, *, allow_review: bool = False,
                note: str | None = None) -> dict:
    """対象月の売上実績を確定する。"""
    chk = precheck(conn, month)
    if chk["closed"]:
        raise ClosingError(f"{month} は確定済みです。修正する場合は reopen_month で取り消してください")
    if chk["needs_review"] and not allow_review:
        names = ", ".join(f"#{i['id']} {i['file_path'].split('/')[-1]}（{i['review_note']}）"
                          for i in chk["needs_review"])
        raise ClosingError(f"要確認の請求書があるため確定できません: {names}")

    conn.execute(
        "INSERT INTO monthly_closings(month, revenue, revenue_incl_tax, invoice_count, note) VALUES (?,?,?,?,?)",
        (month, chk["revenue"], chk["revenue_incl_tax"], chk["invoice_count"], note),
    )
    for i in list_invoices(conn, month):
        conn.execute(
            "INSERT INTO revenue_actuals(month, deal_id, client_name, invoice_id, amount, amount_incl_tax) "
            "VALUES (?,?,?,?,?,?)",
            (month, i["deal_id"], i["client_name"], i["id"], i["subtotal"] or 0, i["total"] or 0),
        )

    # 最終計上月まで請求が済んだ受注案件は「完了」にする
    completed = []
    billed = {r["deal_id"] for r in conn.execute(
        "SELECT DISTINCT deal_id FROM invoices WHERE deal_id IS NOT NULL")}
    for d in list_deals(conn, "受注"):
        plan = planned_revenue(d)
        if plan and max(plan) <= month and d["id"] in billed:
            conn.execute("UPDATE deals SET status='完了', updated_at=datetime('now','localtime') WHERE id=?",
                         (d["id"],))
            completed.append(d["code"])

    log(conn, "月次確定", f"{month} 売上 {chk['revenue']:,} 円 / 請求書 {chk['invoice_count']} 件"
        + (f" / 完了: {', '.join(completed)}" if completed else ""))
    chk["closed"] = True
    chk["completed_deals"] = completed
    return chk


def reopen_month(conn: sqlite3.Connection, month: str, reason: str | None = None) -> None:
    if not is_closed(conn, month):
        raise ClosingError(f"{month} は確定されていません")
    conn.execute("DELETE FROM monthly_closings WHERE month = ?", (month,))  # actuals は CASCADE
    log(conn, "月次確定取消", f"{month} {reason or ''}".strip())


def run_monthly(conn: sqlite3.Connection, month: str | None = None, *,
                folder: str | None = None, allow_review: bool = False) -> dict:
    """請求書フォルダを取り込んでから、対象月（既定: 前月）を確定する。"""
    month = month or prev_month()
    scan = scan_folder(conn, folder)
    result = {"month": month, "scan": scan}
    try:
        result["closing"] = close_month(conn, month, allow_review=allow_review)
    except ClosingError as e:
        result["error"] = str(e)
        result["closing"] = precheck(conn, month)
    return result


def actuals_by_month(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["month"]: r["revenue"] for r in conn.execute("SELECT month, revenue FROM monthly_closings")}


def fiscal_months(start_month: str, n: int = 12) -> list[str]:
    return [add_months(start_month, i) for i in range(n)]
