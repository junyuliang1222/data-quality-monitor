"""
数据质量监控平台 — Flask Web 应用
独立端口 5001，简易登录认证
"""

import json
import os
import threading
import time

import pymysql
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from functools import wraps

from run_checks import run_all_checks

app = Flask(__name__)
app.secret_key = "dq-monitor-secret-key-2025"

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": "SPD_26.5",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

# 硬编码管理员账号
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"

# 各表的主键列映射
TABLE_PK = {
    "users": "user_id",
    "products": "product_id",
    "orders": "order_id",
    "order_items": ["order_id", "product_id"],
}


def get_db():
    return pymysql.connect(**DB_CONFIG)


# ---------- 认证 ----------


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


# ---------- 页面路由 ----------


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USER and password == ADMIN_PASSWORD:
            session["user"] = username
            return redirect(url_for("dashboard"))
        error = "用户名或密码错误"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template("quality_dashboard.html")


@app.route("/rules")
@login_required
def rules():
    return render_template("rules.html")


# ---------- API 路由 ----------


@app.route("/api/run-checks", methods=["POST"])
@login_required
def api_run_checks():
    result = run_all_checks(triggered_by="manual")
    return jsonify(result)


@app.route("/api/check-summary")
@login_required
def api_check_summary():
    execution_id = request.args.get("execution_id")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            if execution_id:
                cur.execute(
                    "SELECT * FROM check_execution_log WHERE execution_id = %s",
                    (execution_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM check_execution_log WHERE status = 'completed' "
                    "ORDER BY started_at DESC LIMIT 1"
                )
            row = cur.fetchone()
            if not row:
                return jsonify({})
            # 序列化 datetime
            row["started_at"] = (
                row["started_at"].strftime("%Y-%m-%d %H:%M:%S")
                if row["started_at"]
                else None
            )
            row["ended_at"] = (
                row["ended_at"].strftime("%Y-%m-%d %H:%M:%S")
                if row["ended_at"]
                else None
            )
            return jsonify(row)
    finally:
        conn.close()


@app.route("/api/issue-distribution")
@login_required
def api_issue_distribution():
    execution_id = request.args.get("execution_id")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # 确定 execution_id
            if not execution_id:
                cur.execute(
                    "SELECT execution_id FROM check_execution_log "
                    "WHERE status = 'completed' ORDER BY started_at DESC LIMIT 1"
                )
                row = cur.fetchone()
                if not row:
                    return jsonify(
                        {"by_table": [], "by_rule_type": [], "by_severity": []}
                    )
                execution_id = row["execution_id"]

            # 按表分布
            cur.execute(
                "SELECT r.target_table, SUM(qcr.violations_count) AS violations "
                "FROM quality_check_results qcr "
                "JOIN quality_rules r ON qcr.rule_id = r.rule_id "
                "WHERE qcr.execution_id = %s AND qcr.status = 'fail' "
                "GROUP BY r.target_table "
                "ORDER BY violations DESC",
                (execution_id,),
            )
            by_table = cur.fetchall()

            # 按规则类型分布
            cur.execute(
                "SELECT r.rule_type, SUM(qcr.violations_count) AS violations "
                "FROM quality_check_results qcr "
                "JOIN quality_rules r ON qcr.rule_id = r.rule_id "
                "WHERE qcr.execution_id = %s AND qcr.status = 'fail' "
                "GROUP BY r.rule_type "
                "ORDER BY violations DESC",
                (execution_id,),
            )
            by_rule_type = cur.fetchall()

            # 按严重级别分布
            cur.execute(
                "SELECT r.severity, SUM(qcr.violations_count) AS violations "
                "FROM quality_check_results qcr "
                "JOIN quality_rules r ON qcr.rule_id = r.rule_id "
                "WHERE qcr.execution_id = %s AND qcr.status = 'fail' "
                "GROUP BY r.severity "
                "ORDER BY violations DESC",
                (execution_id,),
            )
            by_severity = cur.fetchall()

            return jsonify(
                {
                    "by_table": by_table,
                    "by_rule_type": by_rule_type,
                    "by_severity": by_severity,
                }
            )
    finally:
        conn.close()


@app.route("/api/execution-history")
@login_required
def api_execution_history():
    limit = request.args.get("limit", 20, type=int)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM check_execution_log ORDER BY started_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
            for row in rows:
                row["started_at"] = (
                    row["started_at"].strftime("%Y-%m-%d %H:%M:%S")
                    if row["started_at"]
                    else None
                )
                row["ended_at"] = (
                    row["ended_at"].strftime("%Y-%m-%d %H:%M:%S")
                    if row["ended_at"]
                    else None
                )
            return jsonify(rows)
    finally:
        conn.close()


@app.route("/api/violation-details")
@login_required
def api_violation_details():
    execution_id = request.args.get("execution_id")
    rule_id = request.args.get("rule_id")

    if not execution_id:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT execution_id FROM check_execution_log "
                    "WHERE status = 'completed' ORDER BY started_at DESC LIMIT 1"
                )
                row = cur.fetchone()
                execution_id = row["execution_id"] if row else None
        finally:
            conn.close()

    if not execution_id:
        return jsonify([])

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if rule_id:
                cur.execute(
                    "SELECT qcr.*, r.rule_name, r.target_table, r.target_column, "
                    "r.rule_type, r.severity "
                    "FROM quality_check_results qcr "
                    "JOIN quality_rules r ON qcr.rule_id = r.rule_id "
                    "WHERE qcr.execution_id = %s AND qcr.rule_id = %s",
                    (execution_id, rule_id),
                )
            else:
                cur.execute(
                    "SELECT qcr.*, r.rule_name, r.target_table, r.target_column, "
                    "r.rule_type, r.severity "
                    "FROM quality_check_results qcr "
                    "JOIN quality_rules r ON qcr.rule_id = r.rule_id "
                    "WHERE qcr.execution_id = %s AND qcr.status = 'fail' "
                    "ORDER BY qcr.violations_count DESC",
                    (execution_id,),
                )
            rows = cur.fetchall()
            return jsonify(rows)
    finally:
        conn.close()


@app.route("/api/drill-down")
@login_required
def api_drill_down():
    rule_id = request.args.get("rule_id", type=int)
    execution_id = request.args.get("execution_id", type=int)
    dimension = request.args.get("dimension", "")

    if not rule_id or not execution_id or not dimension:
        return jsonify({"error": "缺少参数"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            # 获取规则信息
            cur.execute("SELECT * FROM quality_rules WHERE rule_id = %s", (rule_id,))
            rule = cur.fetchone()
            if not rule:
                return jsonify({"error": "规则不存在"}), 404

            # 获取违规结果
            cur.execute(
                "SELECT sample_data FROM quality_check_results "
                "WHERE execution_id = %s AND rule_id = %s",
                (execution_id, rule_id),
            )
            result_row = cur.fetchone()
            if not result_row:
                return jsonify({"labels": [], "values": []})

            sample = (
                json.loads(result_row["sample_data"])
                if result_row["sample_data"]
                else []
            )
            if not sample:
                return jsonify({"labels": [], "values": []})

            target_table = rule["target_table"]

            # 提取违规行的主键值
            pk = TABLE_PK.get(target_table)
            if not pk:
                return jsonify({"error": f"未知表: {target_table}"}), 400

            # 白名单校验 dimension（防止 SQL 注入）
            allowed_dimensions = {
                "users": ["user_level", "created_at"],
                "products": ["category"],
                "orders": ["order_status", "order_date"],
                "order_items": ["order_date"],
            }
            allowed = allowed_dimensions.get(target_table, [])
            if dimension not in allowed:
                return jsonify({"error": f"不支持的维度: {dimension}"}), 400

            # 构建 WHERE 条件提取违规行
            if isinstance(pk, list):
                # 联合主键
                conditions = []
                for row in sample:
                    parts = []
                    for col in pk:
                        val = row.get(col)
                        if isinstance(val, str):
                            parts.append(f"{col}='{val}'")
                        else:
                            parts.append(f"{col}={val}")
                    conditions.append("(" + " AND ".join(parts) + ")")
                where_clause = " OR ".join(conditions)
            else:
                pk_values = [row.get(pk) for row in sample if row.get(pk) is not None]
                if not pk_values:
                    return jsonify({"labels": [], "values": []})
                str_vals = ", ".join(
                    f"'{v}'" if isinstance(v, str) else str(v) for v in pk_values
                )
                where_clause = f"{pk} IN ({str_vals})"

            # GROUP BY 查询
            if dimension == "created_at" or dimension == "order_date":
                group_sql = f"DATE_FORMAT({dimension}, '%Y-%m')"
            elif dimension == "order_status":
                group_sql = "order_status"
            elif dimension == "user_level":
                group_sql = "user_level"
            elif dimension == "category":
                group_sql = "category"
            else:
                group_sql = dimension

            # order_items 下钻需要 JOIN orders
            if target_table == "order_items" and dimension == "order_date":
                query = (
                    f"SELECT {group_sql} AS dim_value, COUNT(*) AS cnt "
                    f"FROM {target_table} oi "
                    f"JOIN orders o ON oi.order_id = o.order_id "
                    f"WHERE ({where_clause}) "
                    f"GROUP BY {group_sql} "
                    f"ORDER BY {group_sql}"
                )
            else:
                query = (
                    f"SELECT {group_sql} AS dim_value, COUNT(*) AS cnt "
                    f"FROM {target_table} "
                    f"WHERE {where_clause} "
                    f"GROUP BY {group_sql} "
                    f"ORDER BY {group_sql}"
                )

            cur.execute(query)
            rows = cur.fetchall()

            labels = [str(r["dim_value"]) for r in rows]
            values = [r["cnt"] for r in rows]
            return jsonify({"labels": labels, "values": values})

    finally:
        conn.close()


@app.route("/api/rules")
@login_required
def api_rules():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM quality_rules ORDER BY target_table, rule_id")
            rows = cur.fetchall()
            for row in rows:
                row["created_at"] = (
                    row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                    if row["created_at"]
                    else None
                )
            return jsonify(rows)
    finally:
        conn.close()


# ---------- 定时任务 ----------


def run_scheduled_checks():
    from run_checks import run_all_checks

    run_all_checks(triggered_by="scheduled")


def start_scheduler():
    """每小时执行一次质量检查"""
    while True:
        time.sleep(3600)
        run_scheduled_checks()


if __name__ == "__main__":
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    app.run(debug=True, port=5001)
