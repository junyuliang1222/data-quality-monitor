# DataQuality Monitor — 数据质量监控平台

电商数据分析平台的姊妹项目，侧重点为**数据工程**。面向面试展示：面对脏数据时，如何系统性地发现、诊断数据质量问题。

**技术栈**：MySQL 9.3 + Python 3.13 + Flask + pymysql + ECharts 5.5

---

## 项目速览

| 模块 | 内容 |
|------|------|
| 质量规则 | 7 种检查类型（not_null / uniqueness / pattern_match / value_in_set / value_range / referential_integrity / business_logic），18 条预置规则覆盖 4 张业务表 |
| 检查引擎 | Python 脚本按规则配置自动拼装 SQL，遍历执行并记录结果 |
| 脏数据生成 | 自动化脚本注入 10 类脏数据（空值、格式错误、引用断裂、业务逻辑矛盾等），用于验证质量检查有效性 |
| 质量看板 | Flask + ECharts 可视化，包含检查概览、问题分布图、违规明细、历史记录、根因下钻 |
| 定时巡检 | Python `schedule` 库后台定时自动检查（每小时） |

---

## 与项目一的关系

| 维度 | 说明 |
|------|------|
| 数据库 | 共用 `SPD_26.5`，新增 3 张质量监控表（quality_rules / check_execution_log / quality_check_results） |
| 业务表 | 项目一的 users / products / orders / order_items 是质量检查的目标表 |
| 启动方式 | 独立进程、独立端口（5001），与项目一互不影响 |
| 仓库 | 独立 GitHub 仓库，可分别展示 |

### 演示流程

1. 先演示项目一（数据干净）
2. 跑 `generate_dirty_data.py` 注入脏数据
3. 演示项目二（质量平台发现问题 + 下钻分析）
4. 跑项目一的 `insert_data.py` 恢复干净数据

---

## 快速启动

```bash
git clone <your-repo-url>
cd data_quality
conda activate sql-project
pip install pymysql flask schedule

# 设置 MySQL 密码（共用项目一的数据库）
export DB_PASSWORD='your-password'

# 初始化质量监控表（在 MySQL 中执行）
mysql -u root -p < quality_schema.sql
mysql -u root -p < quality_rules.sql

# 注入脏数据（可选，用于验证检查效果）
python generate_dirty_data.py

# 启动
python app.py
# 浏览器打开 http://127.0.0.1:5001/login
```

登录账号：`admin` / `admin123`

---

## 7 种质量规则类型

| 类型 | 用途 | 示例规则 |
|------|------|----------|
| `not_null` | 字段不能为空 | 用户姓名不能为 NULL |
| `uniqueness` | 字段值不能重复 | 手机号不能重复 |
| `pattern_match` | 正则格式校验 | 手机号格式 `^1[3-9]\d{9}$` |
| `value_in_set` | 值必须在白名单内 | 会员等级只能为 0~3 |
| `value_range` | 数值范围限制 | 商品价格 > 0 |
| `referential_integrity` | 外键引用完整性 | 订单用户必须在 users 表中存在 |
| `business_logic` | 自定义 SQL 业务逻辑 | 订单金额与明细汇总一致 |

3 级严重程度：`critical`（阻断）/ `warning`（警告）/ `info`（提示）

---

## 10 类脏数据注入

| 类型 | 目标表 | 操作 |
|------|--------|------|
| 空值-姓名 | users | 随机 2~3 行 SET user_name = NULL |
| 空值-手机号 | users | 随机 2 行 SET phone_num = NULL |
| 手机号重复 | users | 复制某用户手机号到另一用户 |
| 手机号格式错误 | users | 改为 `abc123` 等非法格式 |
| 价格负数 | products | 随机 2 个商品 SET price = -0.01 |
| 库存负数 | products | 随机 1 个商品 SET inventory = -5 |
| 品类名错误 | products | 改为不存在的品类名 |
| 金额负数 | orders | 随机 2 单 SET total_amount = -50 |
| 引用断裂 | orders | 删 1 个 users 行造成孤儿订单 |
| 金额不一致 | orders | 改 total_amount 使其与明细汇总不符 |

脏数据比例约 3%~5%，每次执行结果随机（同一规则每次通过/失败不完全一致）。

> 脏数据直接写入业务表，会影响项目一的展示。演示后务必跑 `insert_data.py` 恢复。

---

## 页面展示

| 页面 | 路由 | 功能 |
|------|------|------|
| 登录 | `/login` | 简易登录（admin / admin123） |
| 质量看板 | `/` | 检查概览卡片 + 问题分布图 + 违规明细 + 历史记录 + 手动执行按钮 |
| 规则列表 | `/rules` | 18 条预置规则只读展示 |

### API

| 路由 | 方法 | 功能 |
|------|------|------|
| `/api/run-checks` | POST | 手动触发一次质量检查 |
| `/api/check-summary` | GET | 最近一次检查汇总 |
| `/api/issue-distribution` | GET | 按表/按规则类型/按严重级别的违规分布 |
| `/api/execution-history` | GET | 历次执行日志 |
| `/api/violation-details` | GET | 某条规则的违规明细（含 sample_data） |
| `/api/drill-down` | GET | 根因下钻（按时间/品类/等级等维度 GROUP BY） |

---

## 文件结构

```
data_quality/
├── quality_schema.sql        # 3 张质量监控表 DDL
├── quality_rules.sql         # 18 条预置检查规则
├── generate_dirty_data.py    # 脏数据生成脚本
├── run_checks.py             # 质量检查引擎（含 generate_check_sql + run_all_checks）
├── app.py                    # Flask Web 应用
├── proposal.md               # 需求文档
├── detailed_design.md        # 详细设计文档
├── README.md                 # 本文件
└── templates/
    ├── base.html             # 基础布局
    ├── login.html            # 登录页
    ├── quality_dashboard.html # 质量看板主页面
    └── rules.html            # 规则列表页
```
