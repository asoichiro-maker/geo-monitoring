#!/usr/bin/env python3
"""
manage_queries.py — クエリ管理CLI

使い方:
    python manage_queries.py list                    # 全クエリ一覧
    python manage_queries.py list --brand dears      # ブランド絞り込み
    python manage_queries.py list --category general # カテゴリ絞り込み

    python manage_queries.py add                     # 対話式追加
    python manage_queries.py add --all-brands        # 3社共通で追加

    python manage_queries.py edit                    # 対話式編集
    python manage_queries.py disable                 # 対話式無効化
    python manage_queries.py enable                  # 対話式有効化
    python manage_queries.py delete                  # 対話式削除（物理削除）
"""
from __future__ import annotations
import os, sys, argparse
from urllib.parse import urlparse, unquote

try:
    import psycopg2
except ImportError:
    print("psycopg2 が必要です: pip install psycopg2-binary")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── クライアント定義 ────────────────────────────────────────────────────────

CLIENTS = {
    "dears":  ("a1b2c3d4-0000-0000-0000-000000000001", "Dears Wedding"),
    "arluis": ("a1b2c3d4-0000-0000-0000-000000000002", "Arluis"),
    "watabe": ("a1b2c3d4-0000-0000-0000-000000000003", "ワタベウエディング"),
}
ALL_CLIENT_IDS = [v[0] for v in CLIENTS.values()]
ID_TO_NAME = {v[0]: v[1] for v in CLIENTS.values()}

CATEGORIES = ["general", "brand_specific", "competitor", "seasonal", "other"]


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
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        sslmode="require", connect_timeout=15,
        client_encoding="UTF8",
    )


# ─── ヘルパー ────────────────────────────────────────────────────────────────

def _resolve_client(brand_str: str | None) -> list[str]:
    """ブランド文字列をclient_idリストに変換。Noneなら全社。"""
    if brand_str is None:
        return ALL_CLIENT_IDS
    key = brand_str.lower()
    if key not in CLIENTS:
        print(f"不明なブランド: {brand_str}  (dears / arluis / watabe)")
        sys.exit(1)
    return [CLIENTS[key][0]]


def _pick(prompt: str, options: list, display=None) -> int:
    """番号選択プロンプト。選択インデックスを返す。"""
    display = display or options
    for i, item in enumerate(display, 1):
        print(f"  {i}. {item}")
    while True:
        try:
            n = int(input(f"{prompt} (番号): "))
            if 1 <= n <= len(options):
                return n - 1
        except (ValueError, KeyboardInterrupt):
            pass
        print(f"  1〜{len(options)} の番号を入力してください")


def _confirm(msg: str) -> bool:
    return input(f"{msg} [y/N]: ").strip().lower() == "y"


# ─── コマンド実装 ────────────────────────────────────────────────────────────

def cmd_list(args):
    """クエリ一覧を表示。"""
    client_ids = _resolve_client(args.brand)

    with _connect() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(client_ids))
            sql = f"""
                SELECT id, client_id, prompt_text, category, is_active, created_at
                FROM query_sets
                WHERE client_id IN ({placeholders})
                {"AND category = %s" if args.category else ""}
                {"AND is_active = FALSE" if args.inactive else ""}
                ORDER BY client_id, category, created_at
            """
            params = client_ids + ([args.category] if args.category else [])
            cur.execute(sql, params)
            rows = cur.fetchall()

    if not rows:
        print("該当するクエリがありません。")
        return

    current_cid = None
    for i, (rid, cid, prompt, cat, active, created) in enumerate(rows, 1):
        if cid != current_cid:
            print(f"\n{'='*60}")
            print(f"  {ID_TO_NAME.get(cid, cid)}")
            print(f"{'='*60}")
            current_cid = cid
        status = "✅" if active else "❌"
        print(f"  {i:>3}. [{cat}] {status} {prompt}")

    print(f"\n合計 {len(rows)} 件")


def cmd_add(args):
    """クエリを追加する。"""
    # ブランド選択
    if args.all_brands:
        client_ids = ALL_CLIENT_IDS
        print(f"対象: 3社共通で追加")
    elif args.brand:
        client_ids = _resolve_client(args.brand)
        print(f"対象: {ID_TO_NAME[client_ids[0]]}")
    else:
        print("\n追加先ブランドを選択してください:")
        names = [f"{n} ({k})" for k, (_, n) in CLIENTS.items()] + ["3社共通"]
        idx = _pick("選択", names)
        if idx == len(CLIENTS):
            client_ids = ALL_CLIENT_IDS
        else:
            client_ids = [list(CLIENTS.values())[idx][0]]

    # カテゴリ選択
    print("\nカテゴリを選択してください:")
    cat_idx = _pick("選択", CATEGORIES)
    category = CATEGORIES[cat_idx]

    # クエリ文入力
    print("\nクエリ文を入力してください（空行で終了）:")
    prompts = []
    while True:
        line = input("  クエリ: ").strip()
        if not line:
            break
        prompts.append(line)

    if not prompts:
        print("クエリが入力されませんでした。")
        return

    print(f"\n以下を追加します:")
    for p in prompts:
        print(f"  [{category}] {p}")
    print(f"  対象: {', '.join(ID_TO_NAME.get(c, c) for c in client_ids)}")

    if not _confirm("実行しますか？"):
        print("キャンセルしました。")
        return

    with _connect() as conn:
        with conn.cursor() as cur:
            for cid in client_ids:
                for prompt in prompts:
                    cur.execute(
                        """
                        INSERT INTO query_sets (client_id, prompt_text, category, is_active)
                        VALUES (%s, %s, %s, TRUE)
                        """,
                        (cid, prompt, category),
                    )
        conn.commit()

    print(f"✅ {len(prompts) * len(client_ids)} 件追加しました。")


def cmd_edit(args):
    """クエリ文言を編集する。"""
    client_ids = _resolve_client(args.brand)

    with _connect() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(client_ids))
            cur.execute(
                f"SELECT id, client_id, prompt_text, category FROM query_sets "
                f"WHERE client_id IN ({placeholders}) AND is_active = TRUE "
                f"ORDER BY client_id, category, created_at",
                client_ids,
            )
            rows = cur.fetchall()

    if not rows:
        print("編集可能なクエリがありません。")
        return

    displays = [f"[{ID_TO_NAME.get(r[1],r[1])}][{r[3]}] {r[2]}" for r in rows]
    print("\n編集するクエリを選択してください:")
    idx = _pick("選択", rows, displays)
    rid, cid, old_prompt, cat = rows[idx]

    print(f"\n現在: {old_prompt}")
    new_prompt = input("新しいクエリ文: ").strip()
    if not new_prompt:
        print("キャンセルしました。")
        return

    if not _confirm(f"'{old_prompt}' → '{new_prompt}' に変更しますか？"):
        print("キャンセルしました。")
        return

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE query_sets SET prompt_text = %s WHERE id = %s",
                (new_prompt, rid),
            )
        conn.commit()

    print(f"✅ 更新しました。")
    print(f"  ⚠️  今月既に実行済みの場合、来月から新クエリが適用されます。")


def cmd_disable(args):
    """クエリを無効化（is_active = FALSE）。"""
    _toggle_active(args, activate=False)


def cmd_enable(args):
    """クエリを有効化（is_active = TRUE）。"""
    _toggle_active(args, activate=True)


def _toggle_active(args, activate: bool):
    target_status = not activate  # 現在の状態（変更前）
    action = "有効化" if activate else "無効化"
    client_ids = _resolve_client(args.brand)

    with _connect() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(client_ids))
            cur.execute(
                f"SELECT id, client_id, prompt_text, category FROM query_sets "
                f"WHERE client_id IN ({placeholders}) AND is_active = %s "
                f"ORDER BY client_id, category, created_at",
                (*client_ids, target_status),
            )
            rows = cur.fetchall()

    if not rows:
        print(f"{action}できるクエリがありません。")
        return

    displays = [f"[{ID_TO_NAME.get(r[1],r[1])}][{r[3]}] {r[2]}" for r in rows]
    print(f"\n{action}するクエリを選択してください:")
    idx = _pick("選択", rows, displays)
    rid, cid, prompt, cat = rows[idx]

    if not _confirm(f"'{prompt}' を{action}しますか？"):
        print("キャンセルしました。")
        return

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE query_sets SET is_active = %s WHERE id = %s",
                (activate, rid),
            )
        conn.commit()

    print(f"✅ {action}しました。")


def cmd_delete(args):
    """クエリを物理削除する（関連する ai_responses も CASCADE 削除される）。"""
    client_ids = _resolve_client(args.brand)

    with _connect() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(client_ids))
            cur.execute(
                f"SELECT id, client_id, prompt_text, category, is_active FROM query_sets "
                f"WHERE client_id IN ({placeholders}) "
                f"ORDER BY client_id, category, created_at",
                client_ids,
            )
            rows = cur.fetchall()

    if not rows:
        print("削除可能なクエリがありません。")
        return

    displays = [
        f"[{ID_TO_NAME.get(r[1],r[1])}][{r[3]}] {'✅' if r[4] else '❌'} {r[2]}"
        for r in rows
    ]
    print("\n削除するクエリを選択してください:")
    idx = _pick("選択", rows, displays)
    rid, cid, prompt, cat, active = rows[idx]

    # 関連データ件数を確認
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM ai_responses WHERE query_set_id = %s", (rid,)
            )
            resp_count = cur.fetchone()[0]

    print(f"\n削除対象: [{cat}] {prompt}")
    print(f"  ⚠️  関連する ai_responses: {resp_count}件 も CASCADE 削除されます")

    if not _confirm("本当に削除しますか？（取り消せません）"):
        print("キャンセルしました。")
        return

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM query_sets WHERE id = %s", (rid,))
        conn.commit()

    print(f"✅ 削除しました。（ai_responses {resp_count}件 も削除）")


# ─── エントリポイント ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GEO モニタリング クエリ管理ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        choices=["list", "add", "edit", "disable", "enable", "delete"],
        help="実行するコマンド",
    )
    parser.add_argument(
        "--brand", choices=["dears", "arluis", "watabe"],
        help="対象ブランド（省略時は全社）",
    )
    parser.add_argument(
        "--all-brands", action="store_true",
        help="3社共通で操作（addコマンド用）",
    )
    parser.add_argument(
        "--category",
        help="カテゴリ絞り込み（listコマンド用）",
    )
    parser.add_argument(
        "--inactive", action="store_true",
        help="無効クエリのみ表示（listコマンド用）",
    )

    args = parser.parse_args()

    commands = {
        "list":    cmd_list,
        "add":     cmd_add,
        "edit":    cmd_edit,
        "disable": cmd_disable,
        "enable":  cmd_enable,
        "delete":  cmd_delete,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
