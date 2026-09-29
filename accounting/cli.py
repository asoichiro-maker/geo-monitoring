"""
accounting/cli.py -- 会計管理 CLI

    python -m accounting.cli deal list [--status 受注]
    python -m accounting.cli deal add --client "ABC商事" --title "サイト改修" --amount 3000000 --start 2026-11
    python -m accounting.cli deal add --client "みらい" --title "SEO支援" --type 月額 --amount 250000 --months 6 --start 2026-10 --status 受注
    python -m accounting.cli deal won D-2026-001 [--amount 2800000] [--start 2026-11]
    python -m accounting.cli deal lost D-2026-002 --reason "予算凍結"
    python -m accounting.cli deal update D-2026-001 --probability 70 --owner 山田
    python -m accounting.cli deal delete D-2026-003

    python -m accounting.cli invoices scan [--folder PATH] [--no-claude]
    python -m accounting.cli invoices list [--month 2026-09]

    python -m accounting.cli status 2026-09          # 確定前チェック（請求漏れ・要確認）
    python -m accounting.cli close 2026-09           # 売上実績を確定
    python -m accounting.cli reopen 2026-09 --reason "請求書差し替え"
    python -m accounting.cli monthly                 # 請求書取込 + 前月確定（スケジューラ用）
    python -m accounting.cli target 2026-10 5000000

    python -m accounting.cli ask "ABC商事のサイト改修 300万で受注、11月納品"

--json を付けると結果を JSON で出力する（Claude Code から呼ぶとき用）。
"""

from __future__ import annotations

import argparse
import json
import sys

from . import closing, deals, invoices
from .db import session


def _yen(v) -> str:
    return "-" if v is None else f"{int(v):,}円"


def _print_deal(d: dict) -> None:
    amt = _yen(d["amount"]) + (f"/月×{d['months']}" if d["billing_type"] == "月額" else "")
    print(f"{d['code']}  [{d['status']}]  {d['client_name']} / {d['title']}  {amt}  "
          f"開始:{d['start_month'] or '-'}  確度:{d['probability']}%"
          + (f"  失注理由:{d['lost_reason']}" if d.get("lost_reason") else ""))


def _out(args, obj, printer=None) -> None:
    if args.json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    elif printer:
        printer(obj)
    else:
        print(obj)


def _deal_fields(a) -> dict:
    m = {"client": "client_name", "title": "title", "amount": "amount", "type": "billing_type",
         "months": "months", "start": "start_month", "status": "status", "probability": "probability",
         "owner": "owner", "notes": "notes"}
    return {v: getattr(a, k) for k, v in m.items() if getattr(a, k, None) is not None}


def _add_deal_opts(p, required: bool = False) -> None:
    p.add_argument("--client", required=required)
    p.add_argument("--title", required=required)
    p.add_argument("--amount", type=int, help="税抜。一括は総額、月額は1ヶ月分")
    p.add_argument("--type", choices=deals.BILLING_TYPES)
    p.add_argument("--months", type=int)
    p.add_argument("--start", help="計上開始月 YYYY-MM")
    p.add_argument("--status", choices=deals.STATUSES)
    p.add_argument("--probability", type=int)
    p.add_argument("--owner")
    p.add_argument("--notes")


def _print_precheck(c: dict) -> None:
    print(f"■ {c['month']}  {'【確定済み】' if c['closed'] else '【未確定】'}")
    print(f"  売上(税抜): {_yen(c['revenue'])}  税込: {_yen(c['revenue_incl_tax'])}  請求書: {c['invoice_count']}件")
    print(f"  受注ベース見込: {_yen(c['forecast_confirmed'])}  目標: {_yen(c['target'])}")
    for i in c["needs_review"]:
        print(f"  ⚠ 要確認 #{i['id']} {i['client_name']}: {i['review_note']}")
    for d in c["unbilled_deals"]:
        print(f"  ⚠ 請求漏れ候補 {d['code']} {d['client_name']} / {d['title']} 予定 {_yen(d['planned'])}")
    for i in c["unmatched_invoices"]:
        print(f"  ・案件未紐付け #{i['id']} {i['client_name']} {_yen(i['subtotal'])}")
    if c.get("completed_deals"):
        print(f"  完了にした案件: {', '.join(c['completed_deals'])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="accounting", description="会計管理 CLI")
    ap.add_argument("--json", action="store_true", help="JSON で出力")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("deal").add_subparsers(dest="sub", required=True)
    p = pd.add_parser("list"); p.add_argument("--status", choices=deals.STATUSES)
    p = pd.add_parser("add"); _add_deal_opts(p, required=True)
    p = pd.add_parser("won"); p.add_argument("ref"); p.add_argument("--date")
    for o in ("--amount", "--months"):
        p.add_argument(o, type=int)
    p.add_argument("--type", choices=deals.BILLING_TYPES); p.add_argument("--start")
    p = pd.add_parser("lost"); p.add_argument("ref"); p.add_argument("--reason"); p.add_argument("--date")
    p = pd.add_parser("update"); p.add_argument("ref"); _add_deal_opts(p)
    p = pd.add_parser("delete"); p.add_argument("ref")

    pi = sub.add_parser("invoices").add_subparsers(dest="sub", required=True)
    p = pi.add_parser("scan"); p.add_argument("--folder"); p.add_argument("--no-claude", action="store_true")
    p = pi.add_parser("list"); p.add_argument("--month")

    p = sub.add_parser("status"); p.add_argument("month", nargs="?")
    p = sub.add_parser("close"); p.add_argument("month", nargs="?"); p.add_argument("--allow-review", action="store_true")
    p.add_argument("--note")
    p = sub.add_parser("reopen"); p.add_argument("month"); p.add_argument("--reason")
    p = sub.add_parser("monthly"); p.add_argument("--month"); p.add_argument("--folder")
    p.add_argument("--allow-review", action="store_true")
    p = sub.add_parser("target"); p.add_argument("month"); p.add_argument("amount", type=int)
    p = sub.add_parser("ask"); p.add_argument("message", nargs="+")

    a = ap.parse_args(argv)
    try:
        with session() as conn:
            if a.cmd == "deal":
                if a.sub == "list":
                    rows = deals.list_deals(conn, a.status)
                    _out(a, rows, lambda rs: [_print_deal(d) for d in rs] or print("（案件なし）"))
                elif a.sub == "add":
                    _out(a, deals.create_deal(conn, **_deal_fields(a)), _print_deal)
                elif a.sub == "won":
                    f = {k: v for k, v in dict(amount=a.amount, months=a.months, billing_type=a.type,
                                                start_month=a.start).items() if v is not None}
                    _out(a, deals.mark_won(conn, a.ref, won_date=a.date, **f), _print_deal)
                elif a.sub == "lost":
                    _out(a, deals.mark_lost(conn, a.ref, reason=a.reason, lost_date=a.date), _print_deal)
                elif a.sub == "update":
                    _out(a, deals.update_deal(conn, a.ref, **_deal_fields(a)), _print_deal)
                elif a.sub == "delete":
                    _out(a, deals.delete_deal(conn, a.ref), lambda d: print(f"削除しました: {d['code']}"))
            elif a.cmd == "invoices":
                if a.sub == "scan":
                    r = invoices.scan_folder(conn, a.folder, use_claude=False if a.no_claude else None)
                    _out(a, r, lambda r: (
                        print(f"{r['folder']}: 新規 {len(r['added'])} 件 / 取込済み {r['skipped']} 件"),
                        [print(f"  + {i['client_name']} {i['billing_month']} {_yen(i['subtotal'])}"
                               + (f"  ⚠ {i['review_note']}" if i['review_note'] else "")) for i in r["added"]],
                        [print(f"  ✗ {e}") for e in r["errors"]]))
                else:
                    _out(a, invoices.list_invoices(conn, a.month), lambda rs: [print(
                        f"#{i['id']} {i['billing_month']} {i['client_name']} {_yen(i['subtotal'])} "
                        f"案件:{i['deal_code'] or '-'}" + (" ⚠" if i["needs_review"] else "")) for i in rs])
            elif a.cmd == "status":
                _out(a, closing.precheck(conn, a.month or deals.prev_month()), _print_precheck)
            elif a.cmd == "close":
                _out(a, closing.close_month(conn, a.month or deals.prev_month(),
                                            allow_review=a.allow_review, note=a.note), _print_precheck)
            elif a.cmd == "reopen":
                closing.reopen_month(conn, a.month, a.reason)
                _out(a, {"reopened": a.month}, lambda _: print(f"{a.month} の確定を取り消しました"))
            elif a.cmd == "monthly":
                r = closing.run_monthly(conn, a.month, folder=a.folder, allow_review=a.allow_review)
                def pr(r):
                    s = r["scan"]
                    print(f"請求書取込: 新規 {len(s['added'])} 件 / 取込済み {s['skipped']} 件")
                    if r.get("error"):
                        print(f"✗ 確定できませんでした: {r['error']}")
                    _print_precheck(r["closing"])
                _out(a, r, pr)
                if r.get("error"):
                    return 2
            elif a.cmd == "target":
                deals.set_target(conn, a.month, a.amount)
                _out(a, {"month": a.month, "target": a.amount}, lambda _: print("目標を設定しました"))
            elif a.cmd == "ask":
                from .assistant import ask
                reply, _ = ask(conn, " ".join(a.message))
                _out(a, {"reply": reply}, lambda o: print(o["reply"]))
    except (deals.DealError, closing.ClosingError, ValueError, FileNotFoundError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
