# 数据质量监控平台 — 详细设计文档

---

## 模块总览

整个项目拆分为 5 个独立模块，模块间通过数据库表做数据交换，可以独立开发、独立测试：

```
┌──────────────────────────────────────────────────────────┐
│                     MySQL (SPD_26.5)                     │
│  quality_rules │ check_execution_log │ quality_check_results │
└──────────────────────────────────────────────────────────┘
        ▲                  ▲                    ▲
        │                  │                    │
  ① quality_schema.sql     │                    │
  ② quality_rules.sql      │                    │
        │                  │                    │
        │         ③ run_checks.py               │
        │         (写结果到两张 log 表)            │
        │                  │                    │
        │         ⑤ app.py (Flask)              │
        │         (读结果 + 渲染看板)              │
        │                                        │
  ④ generate_dirty_data.py (写脏数据到业务表)        │
        │                                        │
  ┌─────┴──────────────────────────────────────┘
  │  users / products / orders / order_items (业务表)
  └──────────────────────────────────────────────
```

| 模块 | 文件 | 依赖 | 可独立测试 |
|------|------|------|:--------:|
| ① 建表 | `quality_schema.sql` | 无（仅需数据库） | ✓ |
| ② 规则配置 | `quality_rules.sql` | 模块 ① | ✓ |
| ③ 检查引擎 | `run_checks.py` | 模块 ①② | ✓ |
| ④ 脏数据生成 | `generate_dirty_data.py` | 无（仅需业务表有数据） | ✓ |
| ⑤ Web 看板 | `app.py` + `templates/` | 模块 ①②③ | ✓ |

---

## 分工

| 模块 | SQL 含量 | 负责人 | 说明 |
|------|:---:|:---:|------|
| ① `quality_schema.sql` | 100% | **用户** | 3 张表 DDL，字段类型选型、外键定义，和项目一的建表练习一致 |
| ② `quality_rules.sql` | 100% | **用户** | 18 条 INSERT，理解规则类型、选配置参数、判断严重级别 — 整个项目的业务核心 |
| ③ `run_checks.py` — SQL 生成部分 | 核心 | **用户** | 7 种 rule_type 各自拼什么检查 SQL，最锻炼 SQL 思维能力 |
| ③ `run_checks.py` — Python 框架部分 | 辅助 | Claude | pymysql 调用、JSON 解析、循环遍历、错误处理等 Python 壳子 |
| ④ `generate_dirty_data.py` | 10% | Claude | 随机选行、发 UPDATE/DELETE，SQL 简单，Python 脚本为主 |
| ⑤ `app.py` + templates | 5% | Claude | Flask 路由、ECharts 图表、HTML 模板，属于 Web 开发，和 SQL 无关 |

> ③ 中 SQL 生成部分与 Python 框架部分的分界线：`generate_check_sql(rule)` 函数由用户实现，其余（DB 连接、遍历规则、写入结果表、`run_all_checks()` 主流程）由 Claude 实现。

---

## 模块 ①：建表（quality_schema.sql）

### 职责

在 `SPD_26.5` 库中创建 3 张质量监控表，不插入任何数据。

### 需要修改的点

当前已有的 `quality_schema.sql` 需做以下调整：

| 修改项 | 当前 | 改为 |
|--------|------|------|
| severity 默认值 | `'warning'` | 不变，但需确保能存 `critical` / `warning` / `info` 三个值（VARCHAR 已满足） |
| rule_type 支持 | 6 种（缺 uniqueness） | 7 种，VARCHAR 已满足，改的是 quality_rules.sql |
| sample_data 语义 | 字段名暗示「样本」 | 保持不变，用注释说明存的是「全部违规行」 |
| RBAC 扩展 SQL | 末尾的 INSERT INTO permissions / role_permissions | **删除**（方案 B 不依赖项目一 RBAC） |

### 表结构（最终版）

与 proposal.md 中定义一致（3 张表，字段不变），不再重复。

### 字段类型说明

| 字段 | 类型选择理由 |
|------|-------------|
| `rule_config` | JSON — MySQL 原生 JSON 类型，支持 `JSON_EXTRACT` 查询，灵活存不同类型的配置参数 |
| `severity` | VARCHAR(10) — 枚举值有限，CHECK 约束可加可不加（MySQL 8.0.16+ 支持） |
| `sample_data` | JSON — 存全部违规行的 JSON 数组，3%~5% 脏数据下最大约 150 行，单行不到 200 字节，总量 < 30KB |
| `triggered_by` | VARCHAR(50) — `manual` 或 `scheduled`，后续可扩展更多触发来源 |

### 测试方法

```sql
-- 执行建表
SOURCE quality_schema.sql;

-- 验证表存在且有正确的列
DESCRIBE quality_rules;
DESCRIBE check_execution_log;
DESCRIBE quality_check_results;
```

---

## 模块 ②：规则配置（quality_rules.sql）

### 职责

插入 18 条预置质量规则到 `quality_rules` 表，覆盖 4 张业务表。

### 规则清单

#### users 表（5 条）

| # | 规则名称 | 列 | 类型 | 配置 | 级别 |
|---|---------|-----|------|------|------|
| 1 | 用户姓名不能为空 | user_name | not_null | `{}` | critical |
| 2 | 手机号不能为空 | phone_num | not_null | `{}` | critical |
| 3 | 手机号不能重复 | phone_num | uniqueness | `{}` | critical |
| 4 | 手机号格式校验 | phone_num | pattern_match | `{"pattern": "^1[3-9]\\d{9}$"}` | warning |
| 5 | 会员等级在有效范围内 | user_level | value_in_set | `{"values": [0, 1, 2, 3]}` | critical |

#### products 表（4 条）

| # | 规则名称 | 列 | 类型 | 配置 | 级别 |
|---|---------|-----|------|------|------|
| 6 | 商品名称不能为空 | product_name | not_null | `{}` | critical |
| 7 | 商品价格必须大于 0 | price | value_range | `{"min": 0.01}` | critical |
| 8 | 库存不能为负数 | inventory | value_range | `{"min": 0}` | critical |
| 9 | 品类必须在已知范围内 | category | value_in_set | `{"values": ["手机数码","电脑办公","影音娱乐","服饰鞋包","图书教育","美妆个护","家居生活","食品饮料","运动户外"]}` | warning |

#### orders 表（5 条）

| # | 规则名称 | 列 | 类型 | 配置 | 级别 |
|---|---------|-----|------|------|------|
| 10 | 订单状态必须在 5 种状态内 | order_status | value_in_set | `{"values": ["待付款","已付款","已发货","已完成","已取消"]}` | critical |
| 11 | 订单金额必须大于 0 | total_amount | value_range | `{"min": 0.01}` | critical |
| 12 | 订单用户必须存在 | user_id | referential_integrity | `{"parent_table": "users", "parent_column": "user_id"}` | critical |
| 13 | 订单金额与明细汇总一致 | — | business_logic | 见下方 SQL | critical |

规则 13 的 check_sql：
```sql
SELECT o.order_id, o.total_amount, ROUND(SUM(oi.unit_price * oi.quantity), 2) AS calc_total
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
GROUP BY o.order_id
HAVING ABS(o.total_amount - SUM(oi.unit_price * oi.quantity)) > 0.01
```

#### order_items 表（4 条）

| # | 规则名称 | 列 | 类型 | 配置 | 级别 |
|---|---------|-----|------|------|------|
| 14 | 购买数量必须大于 0 | quantity | value_range | `{"min": 1}` | critical |
| 15 | 明细单价必须大于 0 | unit_price | value_range | `{"min": 0.01}` | critical |
| 16 | 明细商品必须存在 | product_id | referential_integrity | `{"parent_table": "products", "parent_column": "product_id"}` | critical |
| 17 | 明细订单必须存在 | order_id | referential_integrity | `{"parent_table": "orders", "parent_column": "order_id"}` | critical |
| 18 | 订单 ID + 商品 ID 联合唯一 | (order_id, product_id) | uniqueness | `{}` | info |

> 规则 18 用 info 级别：联合主键保证了数据库层面不会重复，此规则仅作为 double-check 提示。

### 不依赖项目一的 RBAC

当前 `quality_schema.sql` 末尾的 INSERT INTO permissions / INSERT INTO role_permissions 语句需要**删除**。方案 B 不依赖项目一的 RBAC。

### 测试方法

```sql
SOURCE quality_rules.sql;

-- 验证数量
SELECT COUNT(*) FROM quality_rules;  -- 应为 18

-- 按 rule_type 分布检查
SELECT rule_type, COUNT(*) FROM quality_rules GROUP BY rule_type;

-- 按 target_table 分布检查
SELECT target_table, COUNT(*) FROM quality_rules GROUP BY target_table;
```

---

## 模块 ③：检查引擎（run_checks.py）

### 职责

读取 `quality_rules` 表中所有启用的规则，对每一条规则生成并执行检查 SQL，将结果写入 `check_execution_log` 和 `quality_check_results`。

### 对外接口

```python
def run_all_checks(triggered_by: str = 'manual') -> dict:
    """
    执行全部已启用的质量规则检查。
    
    参数:
        triggered_by: 'manual' 或 'scheduled'
    
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
```

### 执行流程

```
1. 清理 30 天前的旧记录（check_execution_log + quality_check_results）
2. 在 check_execution_log 中插入一条新记录（status='running'）
3. 从 quality_rules 查询所有 is_active=1 的规则
4. 对每条规则：
   a. 根据 rule_type 动态生成检查 SQL
   b. 记录开始时间，执行 SQL
   c. 对 uniqueness 类型：特殊的聚合查询，查询结果每行都是一组重复值
   d. 对其他类型：查询结果每行都是一条违规记录
   e. 统计违规行数，将违规行转为 JSON 存入 sample_data
   f. 写入 quality_check_results（pass / fail）
5. 更新 check_execution_log（status='completed'，汇总统计）
6. 返回汇总 dict
```

### SQL 生成规则

`run_checks.py` 中需要一个函数 `generate_check_sql(rule)` ，根据 `rule_type` 拼出对应的检查 SQL。

**通用约定**：
- 所有 `target_column` 的值直接拼入 SQL（不涉及用户输入，无 SQL 注入风险）
- 返回的查询是「找出所有违规行」的 SELECT 语句
- `business_logic` 类型直接使用 `rule_config.check_sql`

#### not_null

```sql
SELECT * FROM {target_table}
WHERE {target_column} IS NULL
```

#### uniqueness

```sql
SELECT {target_column}, COUNT(*) AS dup_count
FROM {target_table}
GROUP BY {target_column}
HAVING COUNT(*) > 1
```

返回的是重复值列表（列值 + 重复次数），而不是原始行。需要额外再查一次取出所有重复值对应的行作为 sample_data。

#### pattern_match

```sql
SELECT * FROM {target_table}
WHERE {target_column} IS NOT NULL
  AND {target_column} NOT REGEXP '{pattern}'
```

说明：NULL 值不参与正则匹配（由 not_null 规则单独覆盖），避免两个规则都报同一行的违规。

#### value_in_set

```sql
SELECT * FROM {target_table}
WHERE {target_column} NOT IN ({value_list})
```

`value_list` 从 `rule_config.values` JSON 数组拼接，数字类型不加引号，字符串类型加引号。

#### value_range

```sql
-- 有 min 无 max
SELECT * FROM {target_table} WHERE {target_column} < {min}

-- 有 max 无 min
SELECT * FROM {target_table} WHERE {target_column} > {max}

-- 有 min 有 max
SELECT * FROM {target_table}
WHERE {target_column} < {min} OR {target_column} > {max}
```

#### referential_integrity

```sql
SELECT t.*
FROM {target_table} t
LEFT JOIN {parent_table} p ON t.{target_column} = p.{parent_column}
WHERE p.{parent_column} IS NULL
```

#### business_logic

直接执行 `rule_config.check_sql`，不额外处理。

### 错误处理

| 场景 | 处理方式 |
|------|----------|
| SQL 执行报错（如表不存在） | 该规则标记为 fail，sample_data 存入错误信息，继续执行下一条规则 |
| 规则类型未知 | 抛出 ValueError，跳过该规则并在日志中记录 |
| 数据库连接失败 | 整个执行失败，check_execution_log 状态设为 failed |

### 测试方法

```bash
# 在干净数据上跑一次（预期全部通过）
python -c "
from run_checks import run_all_checks
result = run_all_checks()
print(result)
# 预期: total_rules=18, rules_passed=18, rules_failed=0
"

# 跑脏数据脚本后再跑一次（预期有违规）
python generate_dirty_data.py
python -c "
from run_checks import run_all_checks
result = run_all_checks()
print(result)
# 预期: rules_failed > 0, total_violations > 0
"

# 验证结果表中的内容
# 查 quality_check_results 看哪些规则 fail 了
```

---

## 模块 ④：脏数据生成（generate_dirty_data.py）

### 职责

在现有业务数据中随机注入 3%~5% 的脏数据，用于验证模块 ③ 能否正确检测。

### 对外接口

```python
def generate_dirty_data() -> dict:
    """
    注入脏数据，返回各类脏数据的注入数量。
    
    返回:
        {
            'null_name': 3,         # 用户姓名设为 NULL
            'null_phone': 2,        # 手机号设为 NULL
            'duplicate_phone': 1,   # 手机号重复
            'bad_phone_format': 4,  # 手机号格式错误
            'negative_price': 2,    # 商品价格为负
            'negative_inventory': 1,# 库存为负
            'wrong_category': 1,    # 品类名错误
            'negative_amount': 2,   # 订单金额为负
            'broken_reference': 2,  # 引用断裂
            'mismatched_amount': 1, # 订单金额与明细不一致
        }
    """
```

### 注入的脏数据类型（共 10 类）

| # | 类型 | 目标表 | 操作 | 数量 |
|---|------|--------|------|------|
| 1 | 空值-姓名 | users | 随机 2~3 行，SET user_name = NULL | ~3 |
| 2 | 空值-手机号 | users | 随机 2 行，SET phone_num = NULL | ~2 |
| 3 | 手机号重复 | users | 随机取 1 个用户的手机号，复制给另 1 个用户 | 1 组 |
| 4 | 手机号格式错误 | users | 随机 3~4 行，改 phone_num 为 'abc123'、'123' 等 | ~4 |
| 5 | 价格负数 | products | 随机 2 个商品，SET price = -0.01 | 2 |
| 6 | 库存负数 | products | 随机 1 个商品，SET inventory = -5 | 1 |
| 7 | 品类名错误 | products | 随机 1 个商品，SET category = '不存在的品类' | 1 |
| 8 | 金额负数 | orders | 随机 2 个已取消订单，SET total_amount = -50 | 2 |
| 9 | 引用断裂 | orders | DELETE 1 个 user，使对应订单的 user_id 变成孤儿 | 1 |
| 10 | 金额不一致 | orders | 随机 1 个订单，SET total_amount = 原值 + 100 | 1 |

### 关键约束

- **不要删 products**：引用断裂只删 users，因为 products 和 order_items 也是父子关系，删 products 会触发外键约束报错
- **金额不一致不能动 order_items**：只改 orders.total_amount，不动 order_items 的单价和数量
- 总脏数据量控制在 **25~30 行**（在 3000+ 总行数中占比 < 1%，足够触发检查但不过度污染）

### 测试方法

```bash
# 跑脏数据脚本
python generate_dirty_data.py

# 手工验证
# 查 users 表中的 NULL 姓名
# 查 products 表中的负价格
# 查 orders 表中的孤儿记录
# 跑 run_checks.py 看能否全部检出
```

---

## 模块 ⑤：Web 看板（app.py + templates/）

### 职责

提供数据质量监控的 Web 界面，包括登录、看板首页、规则列表、手动执行检查等功能。

### 应用配置

| 项目 | 值 |
|------|-----|
| 端口 | `5001` |
| 数据库 | `SPD_26.5`（与项目一共用） |
| 登录方式 | 硬编码用户名密码（admin / admin123） |
| Session | Flask session，登录后保持 |

### 路由设计

#### 页面路由

| 路由 | 方法 | 功能 | 需认证 |
|------|------|------|:-----:|
| `/login` | GET | 登录页 | ✗ |
| `/login` | POST | 验证登录 | ✗ |
| `/logout` | GET | 退出登录 | ✓ |
| `/` | GET | 质量看板主页 | ✓ |
| `/rules` | GET | 规则列表（只读展示） | ✓ |

#### API 路由

| 路由 | 方法 | 请求参数 | 返回 | 功能 |
|------|------|----------|------|------|
| `/api/run-checks` | POST | 无 | `{execution_id, status, ...}` | 手动执行检查 |
| `/api/check-summary` | GET | `?execution_id=` (可选) | `{total_rules, passed, failed, violations, duration}` | 检查汇总，不传则返回最近一次 |
| `/api/issue-distribution` | GET | `?execution_id=` (可选) | `[{table, count}, {rule_type, count}]` | 按表 + 按规则类型的违规分布 |
| `/api/execution-history` | GET | `?limit=20` | `[{execution_id, started_at, status, ...}]` | 历次执行记录 |
| `/api/violation-details` | GET | `?rule_id=&execution_id=` | `[{result_id, violations_count, sample_data, ...}]` | 某次检查中某条规则的违规明细 |
| `/api/drill-down` | GET | `?rule_id=&execution_id=&dimension=` | `[{dim_value, count}]` | 根因下钻 GROUP BY 结果 |

### API 详细设计

#### GET /api/check-summary

```json
// 最近一次检查的汇总
{
  "execution_id": 123,
  "started_at": "2025-05-26 14:30:00",
  "ended_at": "2025-05-26 14:30:02",
  "total_rules": 18,
  "rules_passed": 15,
  "rules_failed": 3,
  "total_violations": 25,
  "triggered_by": "manual"
}
```

#### GET /api/issue-distribution

```json
{
  "by_table": [
    {"target_table": "users", "violations": 12},
    {"target_table": "products", "violations": 8},
    {"target_table": "orders", "violations": 5}
  ],
  "by_rule_type": [
    {"rule_type": "not_null", "violations": 8},
    {"rule_type": "pattern_match", "violations": 5},
    {"rule_type": "value_range", "violations": 7}
  ],
  "by_severity": [
    {"severity": "critical", "violations": 20},
    {"severity": "warning", "violations": 5}
  ]
}
```

> 数据来源：JOIN `quality_check_results` + `quality_rules`，按维度 GROUP BY + SUM(violations_count)

#### GET /api/violation-details

```json
{
  "rule_name": "手机号格式校验",
  "target_table": "users",
  "target_column": "phone_num",
  "rule_type": "pattern_match",
  "severity": "warning",
  "violations_count": 5,
  "sample_data": [
    {"user_id": 12, "user_name": "张三", "phone_num": "abc123"},
    {"user_id": 45, "user_name": "李四", "phone_num": "123"}
  ]
}
```

#### POST /api/run-checks

返回与 `/api/check-summary` 相同结构（执行完成后返回本次执行汇总）。前端按钮点下后显示 loading 状态，等返回后刷新页面数据。

#### GET /api/drill-down

下钻维度说明：

| 目标表 | 可用维度 | SQL GROUP BY 示例 | 说明 |
|--------|----------|-------------------|------|
| users | `user_level` | `GROUP BY user_level` | 按会员等级看违规分布 |
| users | `created_at` | `GROUP BY DATE_FORMAT(created_at, '%Y-%m')` | 按注册月份看 |
| products | `category` | `GROUP BY category` | 按品类看违规分布 |
| orders | `order_status` | `GROUP BY order_status` | 按订单状态看 |
| orders | `order_date` | `GROUP BY DATE_FORMAT(order_date, '%Y-%m')` | 按下单月份看 |
| order_items | `order_date` | 需 JOIN orders 表 | 按下单时间看 |

实现方式：
- 前端传入 `rule_id`，后端从 `quality_rules` 得知 `target_table`
- 从 `sample_data`（JSON 中存的是违规行的主键）取出违规行的 ID 列表
- 在目标表上查这些 ID 对应的完整行，按用户选择的维度做 GROUP BY

请求示例：
```
GET /api/drill-down?rule_id=4&execution_id=123&dimension=user_level
```

返回：
```json
{
  "labels": ["普通会员(0)", "银卡(1)", "金卡(2)", "钻石(3)"],
  "values": [8, 3, 1, 0]
}
```

### 看板页面布局

#### 登录页（login.html）

- 用户名输入框
- 密码输入框
- 登录按钮
- 硬编码验证：admin / admin123

#### 看板主页面（quality_dashboard.html）

```
┌─────────────────────────────────────────────┐
│  [手动执行检查]  [上次检查: 2025-05-26 14:30]  │
├──────────┬──────────┬──────────┬──────────┤
│ 总规则   │ 通过     │ 失败     │ 违规总数  │
│   18     │   15     │    3     │   25     │
├──────────┴──────────┴──────────┴──────────┤
│  问题分布（按表柱状图）   │  问题分布（按类型饼图）│
│                          │                    │
├──────────────────────────┴────────────────────┤
│  历史执行记录（表格）                          │
│  # | 时间 | 触发方式 | 状态 | 通过 | 失败 | 违规│
├──────────────────────────────────────────────┤
│  违规明细（表格，支持点击行展开下钻）             │
│  规则名 | 表 | 严重级别 | 违规数 | [下钻按钮]  │
└──────────────────────────────────────────────┘
```

### 定时任务实现

```python
# app.py 启动时
import schedule
import threading
import time

def run_scheduled_checks():
    """定时执行质量检查"""
    from run_checks import run_all_checks
    run_all_checks(triggered_by='scheduled')

def start_scheduler():
    """启动定时任务（后台线程）"""
    schedule.every(1).hours.do(run_scheduled_checks)  # 每小时一次
    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次是否有任务到期

# 在 app.py 的 if __name__ == '__main__': 中启动后台线程
# threading.Thread(target=start_scheduler, daemon=True).start()
```

### 测试方法

```bash
# 1. 启动 Flask 应用
export DB_PASSWORD='your-password'
python data_quality/app.py

# 2. 浏览器打开 http://127.0.0.1:5001/login
# 3. 用 admin / admin123 登录
# 4. 验证看板页面显示正常
# 5. 点击「手动执行检查」按钮
# 6. 验证检查结果卡片、图表、明细列表均正确展示
# 7. 点击某个违规规则的下钻按钮，验证下钻分析弹窗
# 8. 访问 /rules 页面，验证规则列表只读展示
```

---

## 模块间接口约定

```
┌──────────────────────┐
│ quality_rules 表      │  ← ② 写入，③ 读取 (is_active=1)
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ run_checks.py         │  ← ③ 被 ⑤ 通过 import 调用
│ run_all_checks()      │   ⑤ 中定时任务也调用同一函数
└──────────┬───────────┘
           │ 写入
           ▼
┌──────────────────────┐
│ check_execution_log   │  ← ③ 写入，⑤ 读取
│ quality_check_results │  ← ③ 写入，⑤ 读取
└──────────────────────┘

┌──────────────────────┐
│ generate_dirty_data.py│  ← ④ 独立运行，不依赖任何其他模块
│ 直接写业务表           │    在 ③ 之前执行
└──────────────────────┘
```

模块 ③ 和 ⑤ 之间的数据流：
- ⑤ 调用 `run_all_checks()` → ③ 写入结果表 → ③ 返回 dict → ⑤ 将 dict 返回给前端
- ⑤ 的图表数据 API 直接从结果表查询，不再依赖 ③
