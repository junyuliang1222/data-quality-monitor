USE `SPD_26.5`;

-- ========================================
-- 7 种 rule_type 说明（对应 run_checks.py 的 generate_check_sql）
-- ========================================
-- 1. not_null              SELECT * FROM {表} WHERE {列} IS NULL
-- 2. uniqueness            SELECT {列}, COUNT(*) AS cnt FROM {表} GROUP BY {列} HAVING cnt > 1
-- 3. pattern_match         SELECT * FROM {表} WHERE {列} IS NOT NULL AND {列} NOT REGEXP '{pattern}'
-- 4. value_in_set          SELECT * FROM {表} WHERE {列} NOT IN (值列表)
-- 5. value_range           SELECT * FROM {表} WHERE {列} < min 或 {列} > max
-- 6. referential_integrity SELECT t.* FROM {表} t LEFT JOIN {父表} p ON t.{列} = p.{父列} WHERE p.{父列} IS NULL
-- 7. business_logic        直接执行 rule_config 中的 check_sql

-- ========================================
-- 18 条质量检查规则（4 张业务表）
-- ========================================
INSERT INTO quality_rules (rule_name, target_table, target_column, rule_type, rule_config, severity, description) VALUES

-- users（5条）
('用户姓名不能为空',       'users',       'user_name',   'not_null',              '{}',                                                                                         'critical', 'user_name 列不允许 NULL'),
('手机号不能为空',         'users',       'phone_num',   'not_null',              '{}',                                                                                         'critical', 'phone_num 列不允许 NULL'),
('手机号不能重复',         'users',       'phone_num',   'uniqueness',            '{}',                                                                                         'critical', 'phone_num 列不允许有重复值'),
('手机号格式校验',         'users',       'phone_num',   'pattern_match',         '{"pattern": "^1[3-9][0-9]{9}$"}',                                                           'warning',  '手机号应为 11 位数字，以 1 开头，第二位 3-9'),
('会员等级在有效范围内',   'users',       'user_level',  'value_in_set',          '{"values": [0, 1, 2, 3]}',                                                                   'critical', 'user_level 只能为 0(普通) 1(银卡) 2(金卡) 3(钻石)'),

-- products（4条）
('商品名称不能为空',       'products',    'product_name','not_null',              '{}',                                                                                         'critical', 'product_name 列不允许 NULL'),
('商品价格必须大于0',      'products',    'price',       'value_range',           '{"min": 0.01}',                                                                              'critical', 'price 必须为正数'),
('库存不能为负数',         'products',    'inventory',   'value_range',           '{"min": 0}',                                                                                 'critical', 'inventory 必须 >= 0'),
('品类必须在已知范围内',   'products',    'category',    'value_in_set',          '{"values": ["手机数码","电脑办公","影音娱乐","服饰鞋包","图书教育","美妆个护","家居生活","食品饮料","运动户外"]}', 'warning',  'category 必须是 10 个已知品类之一'),

-- orders（5条）
('订单状态必须在5种状态内','orders',      'order_status','value_in_set',          '{"values": ["待付款","已付款","已发货","已完成","已取消"]}',                                          'critical', 'order_status 只能是 5 种预定义状态之一'),
('订单金额必须大于0',      'orders',      'total_amount','value_range',           '{"min": 0.01}',                                                                              'critical', 'total_amount 必须为正数'),
('订单用户必须存在',       'orders',      'user_id',     'referential_integrity', '{"parent_table": "users", "parent_column": "user_id"}',                                    'critical', 'orders.user_id 必须在 users 表中存在'),
('订单金额与明细汇总一致', 'orders',      NULL,          'business_logic',        '{"check_sql": "SELECT o.order_id, o.total_amount, ROUND(SUM(oi.unit_price * oi.quantity), 2) AS calc_total FROM orders o JOIN order_items oi ON o.order_id = oi.order_id GROUP BY o.order_id HAVING ABS(o.total_amount - SUM(oi.unit_price * oi.quantity)) > 0.01"}', 'critical', 'orders.total_amount 应等于其 order_items 金额汇总'),

-- order_items（4条）
('购买数量必须大于0',      'order_items', 'quantity',    'value_range',           '{"min": 1}',                                                                                 'critical', 'quantity 必须 >= 1'),
('明细单价必须大于0',      'order_items', 'unit_price',  'value_range',           '{"min": 0.01}',                                                                              'critical', 'unit_price 必须为正数'),
('明细商品必须存在',       'order_items', 'product_id',  'referential_integrity', '{"parent_table": "products", "parent_column": "product_id"}',                              'critical', 'order_items.product_id 必须在 products 表中存在'),
('明细订单必须存在',       'order_items', 'order_id',    'referential_integrity', '{"parent_table": "orders", "parent_column": "order_id"}',                                  'critical', 'order_items.order_id 必须在 orders 表中存在'),
('订单ID+商品ID联合唯一',  'order_items', 'order_id,product_id', 'uniqueness',    '{}',                                                                                         'info',     '同一订单不能有重复商品（联合主键保证，此处为 double-check）');
