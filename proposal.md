# 数据质量监控平台 — 需求文档

## 定位

电商数据分析平台的姊妹项目，侧重点为**数据工程**。面向面试展示：面对脏数据时，如何系统性地发现、诊断数据质量问题。

与项目一（电商后台）共用同一个 MySQL 数据库 `SPD_26.5`，但作为独立 Flask 应用运行。

---

## 技术方案

| 项目 | 说明 |
|------|------|
| 数据库 | MySQL 9.3，共用项目一的 `SPD_26.5` 库 |
| 后端 | Python 3.13 + Flask + pymysql |
| 前端 | ECharts 5.5，独立模板目录 |
| 定时任务 | Python `schedule` 库，集成在 Flask 进程内 |
| 运行端口 | 独立端口（如 `5001`），与项目一互不影响 |

---

## 数据库设计

在现有 `SPD_26.5` 库中新增 3 张表：

### 1. quality_rules（质量规则配置表）

| 字段 | 类型 | 说明 |
|------|------|------|
| rule_id | INT PK AUTO_INCREMENT | 规则 ID |
| rule_name | VARCHAR(100) NOT NULL | 规则名称 |
| target_table | VARCHAR(50) NOT NULL | 监控的目标表 |
| target_column | VARCHAR(50) | 目标列（业务逻辑检查可为 NULL） |
| rule_type | VARCHAR(30) NOT NULL | 规则类型（7 种，见下方） |
| rule_config | JSON | 规则参数 |
| severity | VARCHAR(10) DEFAULT 'warning' | 严重级别：critical / warning / info |
| is_active | TINYINT(1) DEFAULT 1 | 是否启用 |
| description | VARCHAR(200) | 规则描述 |
| created_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**7 种 rule_type**：

| 类型 | 用途 | rule_config 示例 | 实现方式 |
|------|------|------------------|----------|
| `not_null` | 字段不能为空 | `{}` | 自动生成 `SELECT * FROM {table} WHERE {column} IS NULL` |
| `uniqueness` | 字段值不能重复 | `{}` | 自动生成 `SELECT {column}, COUNT(*) FROM {table} GROUP BY {column} HAVING COUNT(*) > 1` |
| `pattern_match` | 正则格式校验（检查字段值是否符合正则表达式） | `{"pattern": "^1[3-9]\\d{9}$"}` | 自动生成 `SELECT * FROM {table} WHERE {column} NOT REGEXP '{pattern}'` |
| `value_in_set` | 值必须在白名单中 | `{"values": [0, 1, 2, 3]}` | 自动生成 `SELECT * FROM {table} WHERE {column} NOT IN (...)` |
| `value_range` | 数值范围限制 | `{"min": 0.01}` 或 `{"max": 100}` 或两者都有 | 自动生成 `SELECT * FROM {table} WHERE {column} < min OR {column} > max` |
| `referential_integrity` | 外键引用完整性（检查某列的值是否都能在父表中找到） | `{"parent_table": "users", "parent_column": "user_id"}` | 自动生成 `SELECT t.* FROM {table} t LEFT JOIN {parent_table} p ON t.{column} = p.{parent_column} WHERE p.{parent_column} IS NULL` |
| `business_logic` | 自定义 SQL 业务逻辑（最灵活的扩展方式，直接写 SQL 返回违规行） | `{"check_sql": "SELECT o.order_id FROM orders o JOIN order_items oi ON o.order_id = oi.order_id GROUP BY o.order_id HAVING ABS(o.total_amount - SUM(oi.unit_price * oi.quantity)) > 0.01"}` | 直接执行 `rule_config` 中的 `check_sql`，返回结果即为违规行 |

> 前 6 种规则类型由 `run_checks.py` 根据配置自动拼接 SQL，`business_logic` 是自由扩展口——当内置类型无法满足时，直接写入自定义 SQL。

**3 级 severity**：

| 级别 | 含义 | 示例 |
|------|------|------|
| `critical` | 阻断性错误，必须立即修复 | 价格为负、订单用户不存在 |
| `warning` | 警告，可能存在问题 | 手机号格式不符合规范 |
| `info` | 提示，值得关注但不影响业务 | 库存低于安全阈值 |

### 2. check_execution_log（检查执行日志表）

| 字段 | 类型 | 说明 |
|------|------|------|
| execution_id | INT PK AUTO_INCREMENT | 执行 ID |
| started_at | TIMESTAMP | 开始时间 |
| ended_at | TIMESTAMP | 结束时间 |
| status | VARCHAR(20) DEFAULT 'running' | running / completed / failed |
| total_rules | INT DEFAULT 0 | 总规则数 |
| rules_passed | INT DEFAULT 0 | 通过规则数 |
| rules_failed | INT DEFAULT 0 | 失败规则数 |
| total_violations | INT DEFAULT 0 | 违规总行数 |
| triggered_by | VARCHAR(50) DEFAULT 'manual' | 触发方式：manual / scheduled |

### 3. quality_check_results（检查结果表）

| 字段 | 类型 | 说明 |
|------|------|------|
| result_id | INT PK AUTO_INCREMENT | 结果 ID |
| execution_id | INT NOT NULL FK | 关联的执行记录 |
| rule_id | INT NOT NULL FK | 关联的规则 |
| violations_count | INT DEFAULT 0 | 违规行数 |
| sample_data | JSON | 全部违规行数据 |
| status | VARCHAR(10) DEFAULT 'pass' | pass / fail |
| execution_time_ms | INT DEFAULT 0 | 本条规则执行耗时（毫秒） |

- `execution_id` → `check_execution_log.execution_id`
- `rule_id` → `quality_rules.rule_id`

### 两张日志表的关系

一次质量检查 = 一次考试：

| 表 | 对应概念 | 数据量 | 举例 |
|----|----------|--------|------|
| `check_execution_log` | 考试记录 | 每次检查 1 条 | 「2025-05-26 14:30 执行检查，18 条规则中 3 条未通过，耗时 3.2s」 |
| `quality_check_results` | 每道题的得分 | 每次检查 N 条（N = 规则数） | 「规则 1 通过，0 违规，120ms」<br>「规则 4 未通过，12 违规，样本数据 `[...]`，85ms」 |

两者通过 `execution_id` 一对多关联。`execution_log` 看全局，`check_results` 看每个规则的细节。

### 数据保留策略

- `check_execution_log` 和 `quality_check_results` 保留最近 **30 天**，定期自动清理

---

## 预置检查规则

覆盖 4 张核心业务表（users / products / orders / order_items），规则预置在 `quality_rules.sql` 中。

| 表 | 规则数 | 规则示例 |
|-----|--------|----------|
| users | 5 | 姓名非空、手机号非空、手机号唯一性、手机号格式、会员等级范围 |
| products | 4 | 名称非空、价格 > 0、库存 ≥ 0、品类白名单 |
| orders | 5 | 状态白名单、金额 > 0、user_id 引用完整性、订单金额与明细汇总一致 |
| order_items | 4 | quantity ≥ 1、unit_price > 0、product_id 引用完整性、order_id 引用完整性 |
| **合计** | **18** | |

---

## 脏数据生成脚本

一个独立的 Python 脚本（`generate_dirty_data.py`），用于在干净数据中混入各类脏数据，验证质量检查是否能正确发现问题。

需注入的脏数据类型：
- 空值：随机将某些行的必填字段设为 NULL
- 格式错误：随机改坏几个手机号
- 范围超限：将几个商品价格设为负数、库存设为负数
- 引用断裂：删除少量 users/products 行造成孤儿引用
- 唯一性违反：复制某条手机号到另一用户
- 业务逻辑矛盾：修改某订单 total_amount 使其与明细汇总不一致

脏数据比例控制在 3%~5%，确保不影响正常演示。

---

## Flask Web 应用

### 应用信息

- 独立 Flask 应用：`data_quality/app.py`
- 独立端口：与项目一的 5000 分开，运行在 5001
- 独立模板目录：`data_quality/templates/`
- 认证：简易 session 登录（不依赖项目一的 RBAC 体系），后续可扩展

### 路由设计

| 页面 | 路由 | 方法 | 功能 |
|------|------|------|------|
| 登录 | `/login` | GET/POST | 简易登录验证 |
| 质量看板 | `/` | GET | 主看板页面 |
| 执行检查 API | `/api/run-checks` | POST | 手动触发一次质量检查 |
| 检查概览 API | `/api/check-summary` | GET | 最近一次检查汇总数据 |
| 问题分布 API | `/api/issue-distribution` | GET | 按表/按规则类型的违规分布 |
| 历史记录 API | `/api/execution-history` | GET | 历次执行日志列表 |
| 违规明细 API | `/api/violation-details` | GET | 按 rule_id + execution_id 查看违规详情 |
| 根因下钻 API | `/api/drill-down` | GET | 对违规数据做多维度 GROUP BY 下钻 |
| 规则管理页 | `/rules` | GET | 查看/启用/禁用规则列表 |

### 看板内容

1. **最近检查概览**（统计卡片）：通过规则数、失败规则数、问题总数、执行耗时
2. **问题分布图**（ECharts）：按表的违规数柱状图 + 按规则类型的违规数饼图
3. **违规明细列表**：表格展示每条违规记录，支持点击下钻
4. **历史执行记录**：历次检查的时间、状态、触发方式、耗时
5. **手动执行按钮**：一键触发全量检查

### 根因分析（下钻）

针对违规数据做多维度 GROUP BY 分析，帮助定位问题来源：

- **时间维度**：按天/周聚合违规数，看是否在某时段集中出现
- **业务维度**：按品类、会员等级等分组，看是否集中在特定类别
- 前端通过 ECharts 柱状图/折线图展示下钻结果

---

## 文件结构

```
sql_project/
├── SPD_26.5/                  # 项目一：电商平台（已有）
│   └── ...
└── data_quality/              # 项目二：数据质量平台（新建）
    ├── proposal.md            # 本需求文档
    ├── quality_schema.sql     # 3 张质量表建表语句 + RBAC 权限扩展
    ├── quality_rules.sql      # 18 条预置检查规则
    ├── generate_dirty_data.py # 脏数据生成脚本
    ├── run_checks.py          # 执行质量检查的 Python 脚本
    ├── app.py                 # Flask Web 应用（质量看板）
    └── templates/
        ├── base.html          # 基础布局
        ├── login.html         # 登录页
        ├── quality_dashboard.html  # 质量看板主页面
        └── rules.html         # 规则管理页
```

---

## 与项目一的关系

| 维度 | 说明 |
|------|------|
| 数据库 | 共用 `SPD_26.5`，新增 3 张表 |
| RBAC | 权限表新增 `view_quality` 权限，但不强制依赖（质量平台用简易登录） |
| 业务表 | 项目一的 users/products/orders/order_items 是质量检查的目标表 |
| 启动方式 | 两个独立进程、两个端口，互不影响 |
| 展示方式 | 面试时可独立展示，也可放在一起展示完整的数据平台能力 |

### 脏数据对项目一的影响及演示流程

`generate_dirty_data.py` 直接修改业务表，所以项目一也会看到脏数据。推荐演示顺序：

1. **先演示项目一**（此时数据是干净的）— 展示电商后台各页面功能
2. **跑 `generate_dirty_data.py`** — 注入 3%~5% 脏数据
3. **演示项目二** — 跑质量检查，展示数据质量平台发现问题
4. **跑 `insert_data.py`** — 重新生成干净数据，项目一恢复如初

---

## 近期计划（本版本）

1. 修改 `quality_schema.sql`（增加 uniqueness 类型、info 级别、调整 sample_data 语义）
2. 修改 `quality_rules.sql`（增加 uniqueness 规则、调整 severity）
3. 实现 `generate_dirty_data.py`
4. 实现 `run_checks.py`
5. 实现 `app.py` + 全部模板

## 远期计划

- 数据漂移检测（字段分布突变告警）
- 邮件/钉钉告警通知
- 数据自动修复建议
