"""会計管理モジュールのテスト:  python -m pytest tests/test_accounting.py"""
import pytest

from accounting import closing, deals, invoices
from accounting.db import connect


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("INVOICE_BILLING_MONTH_RULE", "issue_month")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def _invoice(folder, name, body):
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(body, encoding="utf-8")


def test_deal_lifecycle(conn):
    d = deals.create_deal(conn, "株式会社みらい", "SEO支援", amount=250000, billing_type="月額",
                          months=3, start_month="2026-08")
    assert d["code"].startswith("D-") and d["status"] == "提案中"
    assert deals.mark_won(conn, "みらい")["status"] == "受注"
    assert deals.planned_revenue(deals.get_deal(conn, d["id"])) == {
        "2026-08": 250000, "2026-09": 250000, "2026-10": 250000}
    lost = deals.create_deal(conn, "XYZ", "広告", amount=1)
    assert deals.mark_lost(conn, lost["code"], reason="予算")["lost_reason"] == "予算"
    with pytest.raises(deals.DealError):
        deals.create_deal(conn, "A", "B", start_month="2026-13")


def test_regex_extraction():
    ex = invoices.extract_regex(
        "株式会社ABC 御中\n請求書番号: INV-1\n請求日: 令和8年9月1日\n8月分\n小計 ¥100,000\n消費税 ¥10,000\nご請求金額 ¥110,000")
    assert (ex.client_name, ex.invoice_no, ex.issue_date, ex.service_month) == \
        ("株式会社ABC", "INV-1", "2026-09-01", "2026-08")
    assert (ex.subtotal, ex.tax, ex.total) == (100000, 10000, 110000)
    ex = invoices.fill_amounts(invoices.Extracted(total=110000))
    assert (ex.subtotal, ex.tax) == (100000, 10000)


def test_scan_and_close(conn, tmp_path):
    a = deals.create_deal(conn, "ABC商事", "改修", amount=100000, start_month="2026-08", status="受注")
    b = deals.create_deal(conn, "ぬけ工業", "LP", amount=50000, start_month="2026-08", status="受注")
    inv = tmp_path / "inv"
    _invoice(inv, "a.txt", "株式会社ABC商事 御中\n請求日: 2026年8月31日\n小計 ¥100,000\nご請求金額 ¥110,000")
    r = invoices.scan_folder(conn, inv)
    assert len(r["added"]) == 1 and r["added"][0]["deal_id"] == a["id"]
    assert invoices.scan_folder(conn, inv)["skipped"] == 1

    chk = closing.precheck(conn, "2026-08")
    assert chk["revenue"] == 100000
    assert [d["id"] for d in chk["unbilled_deals"]] == [b["id"]]

    res = closing.close_month(conn, "2026-08")
    assert res["completed_deals"] == [a["code"]]
    with pytest.raises(closing.ClosingError):
        closing.close_month(conn, "2026-08")
    with pytest.raises(ValueError):
        invoices.update_invoice(conn, r["added"][0]["id"], subtotal=1)
    closing.reopen_month(conn, "2026-08")
    assert not closing.is_closed(conn, "2026-08")


def test_needs_review_blocks_close(conn, tmp_path):
    inv = tmp_path / "inv"
    _invoice(inv, "x.txt", "株式会社Q 御中\n請求日: 2026年8月31日\n金額未記載")
    invoices.scan_folder(conn, inv)
    with pytest.raises(closing.ClosingError):
        closing.close_month(conn, "2026-08")
    closing.close_month(conn, "2026-08", allow_review=True)
