"""
accounting/db.py
----------------
会計管理ダッシュボード -- SQLite ストレージ

DB ファイルの場所は環境変数 ACCOUNTING_DB_PATH（既定: ./data/accounting.db）。
GEO モニタリング側の PostgreSQL とは独立しており、ローカル PC だけで完結する。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "accounting.db"

SCHEMA = """
-- 案件（パイプライン〜受注〜失注〜完了）
CREATE TABLE IF NOT EXISTS deals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,            -- 例: D-2026-001
    client_name     TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT '提案中'
                            CHECK (status IN ('見込','提案中','受注','失注','完了')),
    billing_type    TEXT    NOT NULL DEFAULT '一括'
                            CHECK (billing_type IN ('一括','月額')),
    amount          INTEGER NOT NULL DEFAULT 0,         -- 一括: 総額 / 月額: 月額（税抜・円）
    months          INTEGER NOT NULL DEFAULT 1,         -- 月額契約の月数（一括は 1）
    start_month     TEXT,                               -- 'YYYY-MM' 売上計上開始月
    probability     INTEGER NOT NULL DEFAULT 50,        -- 受注確度 %（受注=100, 失注=0）
    owner           TEXT,
    won_date        TEXT,
    lost_date       TEXT,
    lost_reason     TEXT,
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 請求書（請求書フォルダから自動取込）
CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT    NOT NULL,
    file_hash       TEXT    NOT NULL UNIQUE,            -- 同じファイルの二重取込防止
    invoice_no      TEXT,
    client_name     TEXT,
    issue_date      TEXT,                               -- 'YYYY-MM-DD'
    billing_month   TEXT,                               -- 'YYYY-MM' 売上計上月
    subtotal        INTEGER,                            -- 税抜
    tax             INTEGER,
    total           INTEGER,                            -- 税込
    deal_id         INTEGER REFERENCES deals(id) ON DELETE SET NULL,
    extracted_by    TEXT,                               -- 'claude' / 'regex'
    needs_review    INTEGER NOT NULL DEFAULT 0,         -- 金額・日付が読めなかった等
    review_note     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_invoices_month ON invoices(billing_month);

-- 月次締め（確定した月はロック）
CREATE TABLE IF NOT EXISTS monthly_closings (
    month           TEXT    PRIMARY KEY,                -- 'YYYY-MM'
    revenue         INTEGER NOT NULL,                   -- 確定売上（税抜）
    revenue_incl_tax INTEGER NOT NULL,
    invoice_count   INTEGER NOT NULL,
    closed_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    note            TEXT
);

-- 確定時点の売上実績スナップショット（案件別）
CREATE TABLE IF NOT EXISTS revenue_actuals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    month           TEXT    NOT NULL REFERENCES monthly_closings(month) ON DELETE CASCADE,
    deal_id         INTEGER REFERENCES deals(id) ON DELETE SET NULL,
    client_name     TEXT,
    invoice_id      INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
    amount          INTEGER NOT NULL,                   -- 税抜
    amount_incl_tax INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actuals_month ON revenue_actuals(month);

-- 月次売上目標
CREATE TABLE IF NOT EXISTS monthly_targets (
    month           TEXT    PRIMARY KEY,
    target          INTEGER NOT NULL
);

-- 操作ログ（自然言語での登録内容を含めて全て残す）
CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    action          TEXT    NOT NULL,
    detail          TEXT
);
"""


def db_path() -> Path:
    return Path(os.environ.get("ACCOUNTING_DB_PATH") or DEFAULT_DB_PATH)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: str | Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log(conn: sqlite3.Connection, action: str, detail: str = "") -> None:
    conn.execute("INSERT INTO activity_log(action, detail) VALUES (?, ?)", (action, detail))
