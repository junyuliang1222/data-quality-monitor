import json
import os
import time
from datetime import datetime, timedelta

import pymysql

# ---------- 数据库连接 ----------

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": "SPD_26.5",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}


def get_db():
    return pymysql.connect(**DB_CONFIG)


# ---------- SQL 生成 ----------


def generate_check_sql(rule):
    """
    参数: rule 是一行 quality_rules 记录（dict）
    返回: 要执行的检查 SQL 字符串
    """
    rule_type = rule["rule_type"]
    target_table = rule["target_table"]
    target_column = rule["target_column"]
    config = json.loads(rule["rule_config"]) if rule["rule_config"] else {}

    if rule_type == "not_null":
        return f"SELECT * FROM {target_table} WHERE {target_column} IS NULL"

    elif rule_type == "uniqueness":
        return f"SELECT {target_column}, COUNT(*) AS cnt FROM {target_table} GROUP BY {target_column} HAVING COUNT(*) > 1"

    elif rule_type == "pattern_match":
        pattern = config["pattern"]
        return f"SELECT * FROM {target_table} WHERE {target_column} IS NOT NULL AND {target_column} NOT REGEXP '{pattern}'"

    elif rule_type == "value_in_set":
        values = config["values"]
        formatted = []
        for v in values:
            if isinstance(v, str):
                formatted.append(f"'{v}'")
            else:
                formatted.append(str(v))
        value_list = ", ".join(formatted)
        return (
            f"SELECT * FROM {target_table} WHERE {target_column} NOT IN ({value_list})"
        )

    elif rule_type == "value_range":
        conditions = []
        if "min" in config:
            conditions.append(f"{target_column} < {config['min']}")
        if "max" in config:
            conditions.append(f"{target_column} > {config['max']}")
        where_clause = " OR ".join(conditions)
        return f"SELECT * FROM {target_table} WHERE {where_clause}"

    elif rule_type == "referential_integrity":
        parent_table = config["parent_table"]
        parent_column = config["parent_column"]
        return (
            f"SELECT t.* FROM {target_table} t "
            f"LEFT JOIN {parent_table} p ON t.{target_column} = p.{parent_column} "
            f"WHERE p.{parent_column} IS NULL"
        )

    elif rule_type == "business_logic":
        return config["check_sql"]

    else:
        raise ValueError(f"未知的 rule_type: {rule_type}")


# ---------- 检查执行 ----------


def _cleanup_old_records(conn):
    """清理 30 天前的旧记录"""
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM quality_check_results WHERE execution_id IN "
            "(SELECT execution_id FROM check_execution_log WHERE started_at < %s)",
            (cutoff,),
        )
        cur.execute("DELETE FROM check_execution_log WHERE started_at < %s", (cutoff,))
    conn.commit()


def _get_violation_sample(conn, rule, check_sql):
    """
    执行检查 SQL，返回 (violations_count, sample_data_json)。
    uniqueness 类型需要特殊处理：先查重复值，再查这些值对应的完整行。
    """
    with conn.cursor() as cur:
        cur.execute(check_sql)
        rows = cur.fetchall()

    if not rows:
        return 0, json.dumps([])

    if rule["rule_type"] == "uniqueness":
        # rows 是 [{col: val, cnt: n}, ...]，需要反查原始违规行
        target_column = rule["target_column"]
        target_table = rule["target_table"]
        dup_values = [row[target_column] for row in rows]

        # 处理联合列的情况（如 order_id,product_id）
        if "," in target_column:
            cols = [c.strip() for c in target_column.split(",")]
            conditions = []
            for row in rows:
                parts = []
                for c in cols:
                    val = row[c]
                    if isinstance(val, str):
                        parts.append(f"{c}='{val}'")
                    else:
                        parts.append(f"{c}={val}")
                conditions.append("(" + " AND ".join(parts) + ")")
            where = " OR ".join(conditions)
            fetch_sql = f"SELECT * FROM {target_table} WHERE {where}"
        else:
            placeholders = ", ".join(
                f"'{v}'" if isinstance(v, str) else str(v) for v in dup_values
            )
            fetch_sql = f"SELECT * FROM {target_table} WHERE {target_column} IN ({placeholders})"

        with conn.cursor() as cur:
            cur.execute(fetch_sql)
            violation_rows = cur.fetchall()
        violations_count = len(violation_rows)
    else:
        violation_rows = rows
        violations_count = len(rows)

    # 序列化为 JSON，datetime 对象转字符串
    def serialize(obj):
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return str(obj)

    sample = json.dumps(violation_rows, default=serialize, ensure_ascii=False)
    return violations_count, sample


def run_all_checks(triggered_by="manual"):
    """
    执行全部已启用的质量规则检查。

    返回:
        {
            'execution_id': 123,
            'status': 'completed',
            'total_rules': 18,
            'rules_passed': 15,
            'rules_failed': 3,
            'total_violations': 42,
            'duration_ms': 1850
        }
    """
    start_time = time.time()
    execution_id = None

    try:
        conn = get_db()
    except Exception as e:
        return {
            "execution_id": None,
            "status": "failed",
            "total_rules": 0,
            "rules_passed": 0,
            "rules_failed": 0,
            "total_violations": 0,
            "duration_ms": int((time.time() - start_time) * 1000),
            "error": f"数据库连接失败: {str(e)}",
        }

    try:
        # 1. 清理旧记录
        _cleanup_old_records(conn)

        # 2. 插入执行日志
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO check_execution_log (status, triggered_by) VALUES ('running', %s)",
                (triggered_by,),
            )
            execution_id = cur.lastrowid
        conn.commit()

        # 3. 查询所有启用的规则
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM quality_rules WHERE is_active = 1")
            rules = cur.fetchall()

        total_rules = len(rules)
        rules_passed = 0
        rules_failed = 0
        total_violations = 0

        # 4. 逐条执行检查
        for rule in rules:
            rule_start = time.time()

            try:
                check_sql = generate_check_sql(rule)
                violations_count, sample_data = _get_violation_sample(
                    conn, rule, check_sql
                )
                elapsed_ms = int((time.time() - rule_start) * 1000)

                status = "pass" if violations_count == 0 else "fail"
                if status == "pass":
                    rules_passed += 1
                else:
                    rules_failed += 1
                total_violations += violations_count

                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO quality_check_results "
                        "(execution_id, rule_id, violations_count, sample_data, status, execution_time_ms) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            execution_id,
                            rule["rule_id"],
                            violations_count,
                            sample_data,
                            status,
                            elapsed_ms,
                        ),
                    )
                conn.commit()

            except Exception as e:
                rules_failed += 1
                elapsed_ms = int((time.time() - rule_start) * 1000)
                error_sample = json.dumps([{"error": str(e)}], ensure_ascii=False)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO quality_check_results "
                        "(execution_id, rule_id, violations_count, sample_data, status, execution_time_ms) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            execution_id,
                            rule["rule_id"],
                            0,
                            error_sample,
                            "fail",
                            elapsed_ms,
                        ),
                    )
                conn.commit()

        # 5. 更新执行日志
        ended_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        duration_ms = int((time.time() - start_time) * 1000)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE check_execution_log SET ended_at=%s, status='completed', "
                "total_rules=%s, rules_passed=%s, rules_failed=%s, total_violations=%s "
                "WHERE execution_id=%s",
                (
                    ended_at,
                    total_rules,
                    rules_passed,
                    rules_failed,
                    total_violations,
                    execution_id,
                ),
            )
        conn.commit()

        return {
            "execution_id": execution_id,
            "status": "completed",
            "total_rules": total_rules,
            "rules_passed": rules_passed,
            "rules_failed": rules_failed,
            "total_violations": total_violations,
            "duration_ms": duration_ms,
        }

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        if execution_id is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE check_execution_log SET ended_at=%s, status='failed' WHERE execution_id=%s",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), execution_id),
                    )
                conn.commit()
            except Exception:
                pass
        return {
            "execution_id": None,
            "status": "failed",
            "total_rules": 0,
            "rules_passed": 0,
            "rules_failed": 0,
            "total_violations": 0,
            "duration_ms": duration_ms,
            "error": str(e),
        }

    finally:
        conn.close()


if __name__ == "__main__":
    result = run_all_checks()
    print(json.dumps(result, ensure_ascii=False, indent=2))
