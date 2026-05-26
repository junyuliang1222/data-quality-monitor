"""
脏数据生成脚本 — 在干净业务数据中注入 3%~5% 脏数据，用于验证质量检查。
"""

import os
import random

import pymysql

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": "SPD_26.5",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}


def generate_dirty_data():
    """
    注入脏数据，返回各类脏数据的注入数量。
    """
    conn = pymysql.connect(**DB_CONFIG)
    result = {
        "null_name": 0,
        "null_phone": 0,
        "duplicate_phone": 0,
        "bad_phone_format": 0,
        "negative_price": 0,
        "negative_inventory": 0,
        "wrong_category": 0,
        "negative_amount": 0,
        "broken_reference": 0,
        "mismatched_amount": 0,
    }

    try:
        # ---- 0. 准备工作：临时去掉 NOT NULL 约束（以便注入 NULL 值）----
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE users MODIFY user_name VARCHAR(50)")
            cur.execute("ALTER TABLE users MODIFY phone_num VARCHAR(20)")
        conn.commit()

        # ---- 1. 空值-姓名：随机 2~3 行 user_name 设为 NULL ----
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users ORDER BY RAND() LIMIT 3")
            user_ids = [row["user_id"] for row in cur.fetchall()]
            for uid in user_ids:
                cur.execute(
                    "UPDATE users SET user_name = NULL WHERE user_id = %s", (uid,)
                )
            result["null_name"] = len(user_ids)
        conn.commit()

        # ---- 2. 空值-手机号：随机 2 行 phone_num 设为 NULL ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM users WHERE phone_num IS NOT NULL ORDER BY RAND() LIMIT 2"
            )
            user_ids = [row["user_id"] for row in cur.fetchall()]
            for uid in user_ids:
                cur.execute(
                    "UPDATE users SET phone_num = NULL WHERE user_id = %s", (uid,)
                )
            result["null_phone"] = len(user_ids)
        conn.commit()

        # ---- 3. 手机号重复：随机取 1 个用户的手机号复制给另 1 个 ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, phone_num FROM users WHERE phone_num IS NOT NULL ORDER BY RAND() LIMIT 2"
            )
            rows = cur.fetchall()
            if len(rows) >= 2:
                cur.execute(
                    "UPDATE users SET phone_num = %s WHERE user_id = %s",
                    (rows[0]["phone_num"], rows[1]["user_id"]),
                )
                result["duplicate_phone"] = 1
        conn.commit()

        # ---- 4. 手机号格式错误：随机 3~4 行改成无效格式 ----
        bad_formats = ["abc123", "123", "1380000", "99999999999"]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM users WHERE phone_num IS NOT NULL AND phone_num REGEXP '^1[3-9][0-9]{9}$' ORDER BY RAND() LIMIT 4"
            )
            user_ids = [row["user_id"] for row in cur.fetchall()]
            for uid in user_ids:
                new_phone = random.choice(bad_formats)
                cur.execute(
                    "UPDATE users SET phone_num = %s WHERE user_id = %s",
                    (new_phone, uid),
                )
            result["bad_phone_format"] = len(user_ids)
        conn.commit()

        # ---- 5. 价格负数：随机 2 个商品 price 设为 -0.01 ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT product_id FROM products WHERE price > 0 ORDER BY RAND() LIMIT 2"
            )
            product_ids = [row["product_id"] for row in cur.fetchall()]
            for pid in product_ids:
                cur.execute(
                    "UPDATE products SET price = -0.01 WHERE product_id = %s", (pid,)
                )
            result["negative_price"] = len(product_ids)
        conn.commit()

        # ---- 6. 库存负数：随机 1 个商品 inventory 设为 -5 ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT product_id FROM products WHERE inventory >= 0 ORDER BY RAND() LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE products SET inventory = -5 WHERE product_id = %s",
                    (row["product_id"],),
                )
                result["negative_inventory"] = 1
        conn.commit()

        # ---- 7. 品类名错误：随机 1 个商品 category 改成无效值 ----
        with conn.cursor() as cur:
            cur.execute("SELECT product_id FROM products ORDER BY RAND() LIMIT 1")
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE products SET category = '不存在的品类' WHERE product_id = %s",
                    (row["product_id"],),
                )
                result["wrong_category"] = 1
        conn.commit()

        # ---- 8. 金额负数：随机 2 个已取消订单 total_amount 设为 -50 ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id FROM orders WHERE order_status = '已取消' AND total_amount > 0 ORDER BY RAND() LIMIT 2"
            )
            order_ids = [row["order_id"] for row in cur.fetchall()]
            for oid in order_ids:
                cur.execute(
                    "UPDATE orders SET total_amount = -50 WHERE order_id = %s", (oid,)
                )
            result["negative_amount"] = len(order_ids)
        conn.commit()

        # ---- 9. 引用断裂：删除 1 个 user（需临时关闭 FK 检查）----
        with conn.cursor() as cur:
            # 选一个有关联订单的用户
            cur.execute(
                "SELECT u.user_id FROM users u "
                "INNER JOIN orders o ON u.user_id = o.user_id "
                "ORDER BY RAND() LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                cur.execute("SET FOREIGN_KEY_CHECKS = 0")
                cur.execute("DELETE FROM users WHERE user_id = %s", (row["user_id"],))
                cur.execute("SET FOREIGN_KEY_CHECKS = 1")
                result["broken_reference"] = 1
        conn.commit()

        # ---- 10. 金额不一致：随机 1 个订单 total_amount 加 100 ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, total_amount FROM orders ORDER BY RAND() LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                new_amount = float(row["total_amount"]) + 100
                cur.execute(
                    "UPDATE orders SET total_amount = %s WHERE order_id = %s",
                    (new_amount, row["order_id"]),
                )
                result["mismatched_amount"] = 1
        conn.commit()

    finally:
        conn.close()

    return result


if __name__ == "__main__":
    result = generate_dirty_data()
    total = sum(result.values())
    print(f"注入脏数据完成，共 {total} 处：")
    for k, v in result.items():
        if v > 0:
            print(f"  {k}: {v}")
