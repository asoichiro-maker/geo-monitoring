"""
accounting/invoices.py
----------------------
請求書フォルダを読み込み、請求書 1 枚ずつから
  請求書番号 / 請求先 / 請求日 / 対象月 / 税抜 / 消費税 / 税込
を抽出して DB に取り込む。取込済みファイルはハッシュで判定し二重取込しない。

抽出方法:
  1. Claude API（ANTHROPIC_API_KEY があれば）: PDF をそのまま読ませる。スキャン PDF にも対応。
  2. 正規表現（API キーが無い / API 失敗時のフォールバック）: テキスト PDF・Excel・txt。

環境変数:
  INVOICE_DIR                 請求書フォルダ（サブフォルダも再帰的に読む）
  INVOICE_BILLING_MONTH_RULE  対象月が請求書に書かれていない場合の計上月ルール
                                issue_month    : 請求日の月（既定。月末締め当月発行）
                                previous_month : 請求日の前月（翌月初に前月分を発行する運用）
  ACCOUNTING_CLAUDE_MODEL     抽出・アシスタントに使うモデル（既定 claude-opus-5-5）
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path

from .db import log
from .deals import add_months, list_deals, normalize_company, planned_revenue

logger = logging.getLogger(__name__)

SUPPORTED_EXT = {".pdf", ".xlsx", ".xlsm", ".txt"}
DEFAULT_MODEL = "claude-opus-5-5"


@dataclass
class Extracted:
    invoice_no: str | None = None
    client_name: str | None = None
    issue_date: str | None = None      # YYYY-MM-DD
    service_month: str | None = None   # YYYY-MM（「◯月分」の記載があれば）
    subtotal: int | None = None
    tax: int | None = None
    total: int | None = None


# ─── テキスト抽出 ────────────────────────────────────────────────────────────

def read_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(str(path), data_only=True, read_only=True)
        lines = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c not in (None, "")]
                if cells:
                    lines.append(" ".join(cells))
        return "\n".join(lines)
    return path.read_text(encoding="utf-8", errors="ignore")


# ─── 正規表現による抽出 ──────────────────────────────────────────────────────

def _to_int(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


def _norm_year(y: str, era: str | None) -> int:
    y_i = int(y)
    if era in ("令和", "R"):
        return 2018 + y_i
    return y_i


_DATE = r"(令和|R)?\s*(\d{1,4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?"


def extract_regex(text: str) -> Extracted:
    t = unicodedata.normalize("NFKC", text)
    ex = Extracted()

    m = re.search(r"(?:請求書番号|請求番号|請求書No|Invoice\s*No)\.?\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]*)", t, re.I)
    if m:
        ex.invoice_no = m.group(1)

    m = re.search(r"^\s*(.+?)\s*(?:御中|様)\s*$", t, re.M)
    if m:
        ex.client_name = m.group(1).strip()

    m = re.search(r"(?:請求日|発行日|請求年月日)\s*[:：]?\s*" + _DATE, t) or re.search(_DATE, t)
    if m:
        y = _norm_year(m.group(2), m.group(1))
        ex.issue_date = f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"

    m = re.search(r"(令和|R)?\s*(\d{1,4})\s*年\s*(\d{1,2})\s*月\s*分", t)
    if m:
        ex.service_month = f"{_norm_year(m.group(2), m.group(1)):04d}-{int(m.group(3)):02d}"
    else:
        m = re.search(r"(\d{1,2})\s*月\s*分", t)
        if m and ex.issue_date:
            iy, im = int(ex.issue_date[:4]), int(ex.issue_date[5:7])
            sm = int(m.group(1))
            ex.service_month = f"{iy - (1 if sm > im else 0):04d}-{sm:02d}"

    amt = r"[:：]?\s*[¥￥]?\s*([\d,]{2,})"
    m = re.search(r"(?:ご請求金額|御請求金額|請求金額|税込合計|合計金額|総合計)\s*(?:[(（]税込[)）])?\s*" + amt, t)
    if m:
        ex.total = _to_int(m.group(1))
    m = re.search(r"(?:小計|税抜金額|税抜合計|合計\s*[(（]税抜[)）])\s*" + amt, t)
    if m:
        ex.subtotal = _to_int(m.group(1))
    m = re.search(r"(?:消費税(?:額)?(?:\s*[(（]?\s*10\s*%\s*[)）]?)?)\s*" + amt, t)
    if m:
        ex.tax = _to_int(m.group(1))
    if ex.total is None:
        m = re.search(r"合計\s*" + amt, t)
        if m:
            ex.total = _to_int(m.group(1))
    return ex


# ─── Claude による抽出 ───────────────────────────────────────────────────────

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_no":    {"type": ["string", "null"], "description": "請求書番号"},
        "client_name":   {"type": ["string", "null"], "description": "請求先（宛名・御中/様の前の会社名）。発行元ではない"},
        "issue_date":    {"type": ["string", "null"], "description": "請求日 YYYY-MM-DD（和暦は西暦に変換）"},
        "service_month": {"type": ["string", "null"], "description": "「◯月分」等の対象月 YYYY-MM。記載が無ければ null"},
        "subtotal":      {"type": ["integer", "null"], "description": "税抜合計（円）"},
        "tax":           {"type": ["integer", "null"], "description": "消費税額（円）"},
        "total":         {"type": ["integer", "null"], "description": "税込の請求金額（円）"},
    },
    "required": ["invoice_no", "client_name", "issue_date", "service_month", "subtotal", "tax", "total"],
    "additionalProperties": False,
}

_EXTRACT_PROMPT = (
    "これは当社が発行した請求書です。記載内容から項目を抽出してください。"
    "読み取れない項目は推測せず null にしてください。金額は円単位の整数（カンマ・記号なし）。"
)


def extract_claude(path: Path, client=None) -> Extracted:
    import anthropic
    client = client or anthropic.Anthropic()
    if path.suffix.lower() == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode()
        doc = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    else:
        doc = {"type": "text", "text": f"<invoice file=\"{path.name}\">\n{read_text(path)}\n</invoice>"}
    resp = client.messages.create(
        model=os.environ.get("ACCOUNTING_CLAUDE_MODEL", DEFAULT_MODEL),
        max_tokens=4096,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _EXTRACT_SCHEMA}},
        messages=[{"role": "user", "content": [doc, {"type": "text", "text": _EXTRACT_PROMPT}]}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"抽出が完了しませんでした (stop_reason={resp.stop_reason})")
    text = next(b.text for b in resp.content if b.type == "text")
    return Extracted(**json.loads(text))


# ─── 補完・計上月 ────────────────────────────────────────────────────────────

def fill_amounts(ex: Extracted, tax_rate: float = 0.10) -> Extracted:
    """税抜/税/税込のうち 2 つ（または 1 つ）から残りを補完する。"""
    s, t, tot = ex.subtotal, ex.tax, ex.total
    if tot is not None and s is None:
        s = tot - t if t is not None else round(tot / (1 + tax_rate))
    if s is not None and tot is None:
        tot = s + t if t is not None else s + int(s * tax_rate)
    if s is not None and tot is not None and t is None:
        t = tot - s
    ex.subtotal, ex.tax, ex.total = s, t, tot
    return ex


def billing_month_for(ex: Extracted, rule: str | None = None) -> str | None:
    if ex.service_month:
        return ex.service_month
    if not ex.issue_date:
        return None
    m = ex.issue_date[:7]
    rule = rule or os.environ.get("INVOICE_BILLING_MONTH_RULE", "issue_month")
    return add_months(m, -1) if rule == "previous_month" else m


# ─── 案件との突合 ────────────────────────────────────────────────────────────

def match_deal(conn: sqlite3.Connection, client_name: str | None, month: str | None,
               subtotal: int | None) -> int | None:
    nc = normalize_company(client_name)
    if not nc:
        return None
    cands = [d for d in list_deals(conn)
             if d["status"] != "失注"
             and (nc == normalize_company(d["client_name"])
                  or nc in normalize_company(d["client_name"])
                  or normalize_company(d["client_name"]) in nc)]
    if not cands:
        return None

    def score(d: dict) -> tuple:
        plan = planned_revenue(d)
        return (
            month in plan and subtotal is not None and plan[month] == subtotal,  # 月・金額とも一致
            month in plan,                                                       # 計上予定月に一致
            d["status"] in ("受注", "完了"),
            d["id"],
        )
    return max(cands, key=score)["id"]


# ─── フォルダ取込 ────────────────────────────────────────────────────────────

def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoice_dir() -> Path | None:
    d = os.environ.get("INVOICE_DIR")
    return Path(d).expanduser() if d else None


def _closed_months(conn: sqlite3.Connection) -> set[str]:
    return {r["month"] for r in conn.execute("SELECT month FROM monthly_closings")}


def ingest_file(conn: sqlite3.Connection, path: Path, use_claude: bool | None = None) -> dict | None:
    """1 ファイルを取り込む。取込済みなら None。"""
    h = _hash(path)
    if conn.execute("SELECT 1 FROM invoices WHERE file_hash = ?", (h,)).fetchone():
        return None
    if use_claude is None:
        use_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))

    method, notes = "regex", []
    ex = None
    if use_claude:
        try:
            ex, method = extract_claude(path), "claude"
        except Exception as e:  # API 障害時も取込自体は止めない
            logger.warning("Claude 抽出失敗 (%s): %s -> 正規表現にフォールバック", path.name, e)
            notes.append(f"Claude抽出失敗: {e}")
    if ex is None:
        ex = extract_regex(read_text(path))
    fill_amounts(ex)
    month = billing_month_for(ex)

    if ex.subtotal is None:
        notes.append("金額を読み取れませんでした")
    if month is None:
        notes.append("計上月を判定できませんでした")
    if not ex.client_name:
        notes.append("請求先を読み取れませんでした")
    if month and month in _closed_months(conn):
        notes.append(f"{month} は確定済みです（締め後に追加された請求書）")

    deal_id = match_deal(conn, ex.client_name, month, ex.subtotal)
    if deal_id is None and ex.client_name:
        notes.append("該当する案件がありません")

    conn.execute(
        """INSERT INTO invoices(file_path, file_hash, invoice_no, client_name, issue_date,
               billing_month, subtotal, tax, total, deal_id, extracted_by, needs_review, review_note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(path), h, ex.invoice_no, ex.client_name, ex.issue_date, month,
         ex.subtotal, ex.tax, ex.total, deal_id, method,
         int(any(n for n in notes if "該当する案件" not in n)), " / ".join(notes) or None),
    )
    row = dict(conn.execute("SELECT * FROM invoices WHERE file_hash = ?", (h,)).fetchone())
    log(conn, "請求書取込", f"{path.name}: {ex.client_name} {month} {ex.subtotal} ({method})")
    return row


def scan_folder(conn: sqlite3.Connection, folder: str | Path | None = None,
                use_claude: bool | None = None) -> dict:
    folder = Path(folder).expanduser() if folder else invoice_dir()
    if not folder:
        raise ValueError("請求書フォルダが未設定です（INVOICE_DIR を設定してください）")
    if not folder.exists():
        raise FileNotFoundError(f"請求書フォルダが見つかりません: {folder}")
    added, skipped, errors = [], 0, []
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXT or p.name.startswith(("~$", ".")):
            continue
        try:
            row = ingest_file(conn, p, use_claude=use_claude)
        except Exception as e:
            errors.append(f"{p.name}: {e}")
            continue
        if row is None:
            skipped += 1
        else:
            added.append(row)
    return {
        "folder": str(folder),
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "needs_review": [r for r in added if r["needs_review"]],
    }


# ─── 参照・手修正 ────────────────────────────────────────────────────────────

INVOICE_EDITABLE = {"invoice_no", "client_name", "issue_date", "billing_month",
                    "subtotal", "tax", "total", "deal_id", "needs_review", "review_note"}


def list_invoices(conn: sqlite3.Connection, month: str | None = None) -> list[dict]:
    sql = ("SELECT i.*, d.code AS deal_code, d.title AS deal_title FROM invoices i "
           "LEFT JOIN deals d ON d.id = i.deal_id")
    if month:
        return [dict(r) for r in conn.execute(sql + " WHERE i.billing_month = ? ORDER BY i.issue_date", (month,))]
    return [dict(r) for r in conn.execute(sql + " ORDER BY i.billing_month DESC, i.issue_date DESC")]


def update_invoice(conn: sqlite3.Connection, invoice_id: int, **fields) -> dict:
    unknown = set(fields) - INVOICE_EDITABLE
    if unknown:
        raise ValueError(f"更新できない項目: {sorted(unknown)}")
    cur = conn.execute("SELECT billing_month FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if cur is None:
        raise ValueError(f"請求書が見つかりません: id={invoice_id}")
    closed = _closed_months(conn)
    if cur["billing_month"] in closed or fields.get("billing_month") in closed:
        raise ValueError("確定済みの月の請求書は修正できません。先に月次確定を取り消してください")
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE invoices SET {sets} WHERE id = ?", (*fields.values(), invoice_id))
        log(conn, "請求書修正", f"id={invoice_id} {fields}")
    return dict(conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone())


def as_dict(ex: Extracted) -> dict:
    return asdict(ex)
