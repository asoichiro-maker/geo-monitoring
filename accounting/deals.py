"""
accounting/deals.py
-------------------
案件管理（登録・受注・失注・更新）と売上見込の計算。

自然言語アシスタント（assistant.py）・CLI（cli.py）・ダッシュボードの
全てがこのモジュールの関数を経由して DB を更新する。
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date

from .db import log

STATUSES = ["見込", "提案中", "受注", "失注", "完了"]
OPEN_STATUSES = ["見込", "提案中"]
BILLING_TYPES = ["一括", "月額"]
EDITABLE_FIELDS = {
    "client_name", "title", "status", "billing_type", "amount", "months",
    "start_month", "probability", "owner", "notes", "lost_reason",
    "won_date", "lost_date",
}


class DealError(ValueError):
    pass


# ─── 月ユーティリティ ────────────────────────────────────────────────────────

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_month(m: str | None) -> str | None:
    if m in (None, ""):
        return None
    if not MONTH_RE.match(m):
        raise DealError(f"月は YYYY-MM 形式で指定してください: {m!r}")
    return m


def add_months(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def this_month(today: date | None = None) -> str:
    t = today or date.today()
    return f"{t.year:04d}-{t.month:02d}"


def prev_month(today: date | None = None) -> str:
    return add_months(this_month(today), -1)


def month_range(start: str, end: str) -> list[str]:
    out, m = [], start
    while m <= end:
        out.append(m)
        m = add_months(m, 1)
    return out


# ─── 会社名の正規化（請求書との突合に使用） ────────────────────────────────────

_CORP_WORDS = ["株式会社", "有限会社", "合同会社", "（株）", "(株)", "㈱", "（有）", "(有)",
               "御中", "様", "inc.", "inc", "co.,ltd.", "co., ltd.", "ltd.", "llc"]


def normalize_company(name: str | None) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).lower()
    for w in _CORP_WORDS:
        s = s.replace(unicodedata.normalize("NFKC", w).lower(), "")
    return re.sub(r"[\s・,.\-　]", "", s)


# ─── 参照・一覧 ──────────────────────────────────────────────────────────────

def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r else None


def get_deal(conn: sqlite3.Connection, deal_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone())


def list_deals(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute("SELECT * FROM deals WHERE status = ? ORDER BY id", (status,))
    else:
        rows = conn.execute("SELECT * FROM deals ORDER BY id")
    return [dict(r) for r in rows]


def find_deals(conn: sqlite3.Connection, query: str) -> list[dict]:
    """案件コード完全一致 → 顧客名/案件名の部分一致 の順で探す。"""
    q = query.strip()
    hit = conn.execute("SELECT * FROM deals WHERE upper(code) = upper(?)", (q,)).fetchone()
    if hit:
        return [dict(hit)]
    nq = normalize_company(q)
    out = []
    for d in list_deals(conn):
        hay = normalize_company(d["client_name"]) + normalize_company(d["title"])
        if nq and nq in hay:
            out.append(d)
    return out


def resolve_deal(conn: sqlite3.Connection, ref: str | int) -> dict:
    """案件コード / ID / 顧客名 から 1 件に特定する。曖昧なら DealError。"""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        d = get_deal(conn, int(ref))
        if d:
            return d
    matches = find_deals(conn, str(ref))
    if not matches:
        raise DealError(f"案件が見つかりません: {ref}")
    if len(matches) > 1:
        # 進行中の案件が 1 件だけならそれを採用
        open_ = [m for m in matches if m["status"] in OPEN_STATUSES]
        if len(open_) == 1:
            return open_[0]
        cands = ", ".join(f"{m['code']}({m['client_name']}/{m['title']}/{m['status']})" for m in matches)
        raise DealError(f"候補が複数あります。案件コードで指定してください: {cands}")
    return matches[0]


# ─── 登録・更新 ──────────────────────────────────────────────────────────────

def next_code(conn: sqlite3.Connection, year: int | None = None) -> str:
    y = year or date.today().year
    prefix = f"D-{y}-"
    row = conn.execute(
        "SELECT code FROM deals WHERE code LIKE ? ORDER BY code DESC LIMIT 1", (prefix + "%",)
    ).fetchone()
    n = int(row["code"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"{prefix}{n:03d}"


def _validate(fields: dict) -> dict:
    f = dict(fields)
    if "status" in f and f["status"] not in STATUSES:
        raise DealError(f"ステータスは {STATUSES} のいずれか: {f['status']!r}")
    if "billing_type" in f and f["billing_type"] not in BILLING_TYPES:
        raise DealError(f"請求形態は {BILLING_TYPES} のいずれか: {f['billing_type']!r}")
    if "start_month" in f:
        f["start_month"] = validate_month(f["start_month"])
    for k in ("amount", "months", "probability"):
        if k in f and f[k] is not None:
            f[k] = int(f[k])
            if f[k] < 0:
                raise DealError(f"{k} は 0 以上で指定してください")
    if f.get("months") == 0:
        raise DealError("months は 1 以上で指定してください")
    if "probability" in f and f["probability"] is not None and f["probability"] > 100:
        raise DealError("probability は 0〜100 で指定してください")
    return f


def create_deal(
    conn: sqlite3.Connection,
    client_name: str,
    title: str,
    amount: int = 0,
    billing_type: str = "一括",
    months: int = 1,
    start_month: str | None = None,
    status: str = "提案中",
    probability: int | None = None,
    owner: str | None = None,
    notes: str | None = None,
) -> dict:
    if not client_name or not title:
        raise DealError("顧客名と案件名は必須です")
    if billing_type == "一括":
        months = 1
    if probability is None:
        probability = {"受注": 100, "完了": 100, "失注": 0}.get(status, 50)
    f = _validate(dict(
        client_name=client_name.strip(), title=title.strip(), amount=amount,
        billing_type=billing_type, months=months, start_month=start_month,
        status=status, probability=probability, owner=owner, notes=notes,
    ))
    f["code"] = next_code(conn)
    if status == "受注":
        f["won_date"] = date.today().isoformat()
    if status == "失注":
        f["lost_date"] = date.today().isoformat()
    cols = ", ".join(f)
    conn.execute(f"INSERT INTO deals ({cols}) VALUES ({', '.join('?' * len(f))})", tuple(f.values()))
    deal = _row(conn.execute("SELECT * FROM deals WHERE code = ?", (f["code"],)).fetchone())
    log(conn, "案件登録", f"{deal['code']} {deal['client_name']} / {deal['title']} [{deal['status']}]")
    return deal


def update_deal(conn: sqlite3.Connection, ref: str | int, **fields) -> dict:
    deal = resolve_deal(conn, ref)
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise DealError(f"更新できない項目: {sorted(unknown)}")
    f = _validate({k: v for k, v in fields.items()})
    if f.get("billing_type") == "一括":
        f["months"] = 1
    if not f:
        return deal
    sets = ", ".join(f"{k} = ?" for k in f) + ", updated_at = datetime('now','localtime')"
    conn.execute(f"UPDATE deals SET {sets} WHERE id = ?", (*f.values(), deal["id"]))
    new = get_deal(conn, deal["id"])
    changes = ", ".join(f"{k}: {deal[k]} → {new[k]}" for k in f if deal[k] != new[k])
    log(conn, "案件更新", f"{new['code']} {changes}")
    return new


def mark_won(
    conn: sqlite3.Connection,
    ref: str | int,
    won_date: str | None = None,
    **fields,
) -> dict:
    """受注にする。金額・開始月・月数なども同時に確定できる。"""
    deal = resolve_deal(conn, ref)
    fields.update(status="受注", probability=100,
                  won_date=won_date or date.today().isoformat(), lost_date=None, lost_reason=None)
    if not (fields.get("start_month") or deal["start_month"]):
        fields["start_month"] = this_month()
    return update_deal(conn, deal["id"], **fields)


def mark_lost(
    conn: sqlite3.Connection,
    ref: str | int,
    reason: str | None = None,
    lost_date: str | None = None,
) -> dict:
    deal = resolve_deal(conn, ref)
    return update_deal(conn, deal["id"], status="失注", probability=0,
                       lost_date=lost_date or date.today().isoformat(), lost_reason=reason)


def delete_deal(conn: sqlite3.Connection, ref: str | int) -> dict:
    deal = resolve_deal(conn, ref)
    conn.execute("DELETE FROM deals WHERE id = ?", (deal["id"],))
    log(conn, "案件削除", f"{deal['code']} {deal['client_name']} / {deal['title']}")
    return deal


# ─── 売上見込 ────────────────────────────────────────────────────────────────

def planned_revenue(deal: dict) -> dict[str, int]:
    """案件 1 件の月別計上予定（税抜）。開始月未設定なら空。"""
    if not deal.get("start_month") or deal["status"] == "失注":
        return {}
    if deal["billing_type"] == "月額":
        return {add_months(deal["start_month"], i): deal["amount"] for i in range(max(deal["months"], 1))}
    return {deal["start_month"]: deal["amount"]}


def contract_value(deal: dict) -> int:
    return deal["amount"] * (deal["months"] if deal["billing_type"] == "月額" else 1)


def forecast(conn: sqlite3.Connection, months: list[str]) -> dict[str, dict[str, int]]:
    """
    月別の売上見込を返す。
      confirmed: 受注・完了案件の計上予定
      weighted : 見込・提案中案件の計上予定 × 確度
    """
    out = {m: {"confirmed": 0, "weighted": 0} for m in months}
    for d in list_deals(conn):
        for m, amt in planned_revenue(d).items():
            if m not in out:
                continue
            if d["status"] in ("受注", "完了"):
                out[m]["confirmed"] += amt
            elif d["status"] in OPEN_STATUSES:
                out[m]["weighted"] += round(amt * d["probability"] / 100)
    return out


def set_target(conn: sqlite3.Connection, month: str, target: int) -> None:
    validate_month(month)
    conn.execute(
        "INSERT INTO monthly_targets(month, target) VALUES (?, ?) "
        "ON CONFLICT(month) DO UPDATE SET target = excluded.target",
        (month, int(target)),
    )
    log(conn, "目標設定", f"{month}: {int(target):,} 円")
