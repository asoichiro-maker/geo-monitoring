"""
accounting/assistant.py
-----------------------
自然言語で会計データを操作するアシスタント（Claude API + ツール呼び出し）。

例:
  「ABC商事のサイト改修、300万で受注。11月納品」
  「XYZの広告運用は失注。理由は予算凍結」
  「株式会社みらいのSEO支援 月額25万×6ヶ月 10月開始で受注した」
  「先月分の売上を確定して」

ダッシュボードのチャット欄と CLI（python -m accounting.cli ask "..."）から呼ばれる。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from typing import Any, Callable

from . import closing, deals, invoices

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TURNS = 10


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_DEAL_FIELDS = {
    "client_name":  {"type": "string", "description": "顧客名（会社名）"},
    "title":        {"type": "string", "description": "案件名"},
    "amount":       {"type": "integer", "description": "税抜金額（円）。一括は総額、月額は 1 ヶ月あたり"},
    "billing_type": {"type": "string", "enum": deals.BILLING_TYPES},
    "months":       {"type": "integer", "description": "月額契約の月数"},
    "start_month":  {"type": "string", "description": "売上計上開始月 YYYY-MM（一括は納品/検収月）"},
    "probability":  {"type": "integer", "description": "受注確度 0-100"},
    "owner":        {"type": "string", "description": "担当者"},
    "notes":        {"type": "string"},
}

TOOLS: list[dict] = [
    {
        "name": "list_deals",
        "description": "案件一覧を取得する。status を指定すると絞り込み。既存案件を特定する前に必ず呼ぶ。",
        "input_schema": _obj({"status": {"type": "string", "enum": deals.STATUSES}}, []),
    },
    {
        "name": "create_deal",
        "description": "新しい案件を登録する。いきなり受注した案件は status='受注' で登録する。",
        "input_schema": _obj({**_DEAL_FIELDS, "status": {"type": "string", "enum": deals.STATUSES}},
                             ["client_name", "title"]),
    },
    {
        "name": "mark_won",
        "description": "既存案件を受注にする。金額・開始月・月数が発話に含まれていれば同時に更新する。",
        "input_schema": _obj({"deal": {"type": "string", "description": "案件コード（D-YYYY-NNN）"},
                              **{k: _DEAL_FIELDS[k] for k in ("amount", "billing_type", "months", "start_month")}},
                             ["deal"]),
    },
    {
        "name": "mark_lost",
        "description": "既存案件を失注にする。",
        "input_schema": _obj({"deal": {"type": "string", "description": "案件コード"},
                              "reason": {"type": "string", "description": "失注理由"}}, ["deal"]),
    },
    {
        "name": "update_deal",
        "description": "既存案件の項目を更新する（金額・確度・開始月・ステータス等）。",
        "input_schema": _obj({"deal": {"type": "string", "description": "案件コード"},
                              **_DEAL_FIELDS,
                              "status": {"type": "string", "enum": deals.STATUSES}}, ["deal"]),
    },
    {
        "name": "scan_invoices",
        "description": "請求書フォルダを読み込み、新しい請求書を取り込む。",
        "input_schema": _obj({}, []),
    },
    {
        "name": "month_status",
        "description": "指定月の売上集計・請求漏れ候補・要確認請求書・確定状況を返す。",
        "input_schema": _obj({"month": {"type": "string", "description": "YYYY-MM"}}, ["month"]),
    },
    {
        "name": "close_month",
        "description": "指定月の売上実績を確定する。ユーザーが明示的に確定を依頼した場合のみ呼ぶ。",
        "input_schema": _obj({"month": {"type": "string", "description": "YYYY-MM"}}, ["month"]),
    },
    {
        "name": "set_target",
        "description": "月次売上目標（税抜・円）を設定する。",
        "input_schema": _obj({"month": {"type": "string"}, "target": {"type": "integer"}}, ["month", "target"]),
    },
]


def _brief(d: dict) -> dict:
    keys = ("code", "client_name", "title", "status", "billing_type", "amount", "months",
            "start_month", "probability", "owner", "lost_reason")
    return {k: d.get(k) for k in keys}


def _handlers(conn: sqlite3.Connection) -> dict[str, Callable[[dict], Any]]:
    def list_deals(a):
        return [_brief(d) for d in deals.list_deals(conn, a.get("status"))]

    def create_deal(a):
        return _brief(deals.create_deal(conn, **a))

    def mark_won(a):
        ref = a.pop("deal")
        return _brief(deals.mark_won(conn, ref, **a))

    def mark_lost(a):
        return _brief(deals.mark_lost(conn, a["deal"], reason=a.get("reason")))

    def update_deal(a):
        ref = a.pop("deal")
        return _brief(deals.update_deal(conn, ref, **a))

    def scan_invoices(_a):
        r = invoices.scan_folder(conn)
        return {"folder": r["folder"], "added": len(r["added"]), "skipped": r["skipped"],
                "errors": r["errors"],
                "added_detail": [{k: i[k] for k in ("client_name", "billing_month", "subtotal", "review_note")}
                                 for i in r["added"]]}

    def month_status(a):
        c = closing.precheck(conn, a["month"])
        return {**{k: c[k] for k in ("month", "closed", "invoice_count", "revenue", "forecast_confirmed", "target")},
                "needs_review": [{k: i[k] for k in ("id", "client_name", "review_note")} for i in c["needs_review"]],
                "unbilled_deals": [{"code": d["code"], "client_name": d["client_name"], "planned": d["planned"]}
                                   for d in c["unbilled_deals"]]}

    def close_month(a):
        c = closing.close_month(conn, a["month"])
        return {"month": c["month"], "revenue": c["revenue"], "invoice_count": c["invoice_count"],
                "completed_deals": c["completed_deals"],
                "unbilled_deals": [d["code"] for d in c["unbilled_deals"]]}

    def set_target(a):
        deals.set_target(conn, a["month"], a["target"])
        return {"ok": True}

    return locals()


SYSTEM = """あなたは会計管理ダッシュボードの操作アシスタントです。
ユーザーの日本語の指示をツールで実行し、実行結果を簡潔に日本語で報告します。

ルール:
- 既存案件の受注・失注・更新の前に list_deals で対象を特定する。顧客名が一致する案件が 1 件なら、それを使う。
  該当が無ければ、受注/失注の報告であっても create_deal で新規登録する（受注なら status='受注'、失注なら status='失注'）。
  候補が複数で特定できない場合は、実行せず候補を示して確認する。
- 金額は税抜の円整数で扱う。「300万」→3000000、「25万/月」→月額 250000。税込と明示された場合は 1.1 で割って税抜にする。
- 「月額」「毎月」「×6ヶ月」は billing_type='月額' と months。それ以外は '一括'。
- 開始月・納品月が発話にあれば start_month (YYYY-MM)。「来月」等は今日の日付から計算する。無ければ省略してよい。
- close_month はユーザーが確定を明示的に依頼したときだけ呼ぶ。確定前に month_status で請求漏れ候補を確認し、結果に含めて報告する。
- 実行後は、登録/更新した案件コード・顧客名・金額・ステータスを 1〜3 行で報告する。"""


def ask(conn: sqlite3.Connection, message: str, history: list[dict] | None = None,
        client=None) -> tuple[str, list[dict]]:
    """
    自然言語の指示を 1 つ処理して (返答テキスト, 更新後の会話履歴) を返す。
    DB の更新は conn 上で行われる（commit は呼び出し側）。
    """
    import anthropic
    client = client or anthropic.Anthropic()
    handlers = _handlers(conn)
    messages = list(history or [])
    messages.append({"role": "user", "content": f"（今日: {date.today().isoformat()}）\n{message}"})
    model = os.environ.get("ACCOUNTING_CLAUDE_MODEL", DEFAULT_MODEL)

    for _ in range(MAX_TURNS):
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            tools=TOOLS,
            output_config={"effort": "medium"},
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "refusal":
            return "この依頼は処理できませんでした。", messages
        if resp.stop_reason != "tool_use":
            text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            return text or "（完了しました）", messages

        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            fn = handlers.get(block.name)
            try:
                if fn is None:
                    raise ValueError(f"未知のツール: {block.name}")
                out = fn(dict(block.input))
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, ensure_ascii=False, default=str)})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"エラー: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})

    return "処理が長くなりすぎたため中断しました。指示を分けてください。", messages
