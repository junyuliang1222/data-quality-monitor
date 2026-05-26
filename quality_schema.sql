use `SPD_26.5`;

-- ========================================
-- 1. 质量规则配置表
-- ========================================
CREATE TABLE IF NOT EXISTS quality_rules (
    rule_id        INT AUTO_INCREMENT PRIMARY KEY,
    rule_name      VARCHAR(100) NOT NULL,
    target_table   VARCHAR(50) NOT NULL,
    target_column  VARCHAR(50),
    rule_type      VARCHAR(30) NOT NULL,
    rule_config    JSON,
    severity       VARCHAR(10) DEFAULT 'warning',
    is_active      TINYINT(1) DEFAULT 1,
    description    VARCHAR(200),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ========================================
-- 2. 检查执行日志表
-- ========================================
CREATE TABLE IF NOT EXISTS check_execution_log (
    execution_id        INT AUTO_INCREMENT PRIMARY KEY,
    started_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at            TIMESTAMP,
    status              VARCHAR(20) DEFAULT 'running',
    total_rules         INT DEFAULT 0,
    rules_passed        INT DEFAULT 0,
    rules_failed        INT DEFAULT 0,
    total_violations    INT DEFAULT 0,
    triggered_by        VARCHAR(50) DEFAULT 'manual'
);

-- ========================================
-- 3. 检查结果表
-- ========================================
CREATE TABLE IF NOT EXISTS quality_check_results (
    result_id         INT AUTO_INCREMENT PRIMARY KEY,
    execution_id      INT NOT NULL,
    rule_id           INT NOT NULL,
    violations_count  INT DEFAULT 0,
    sample_data       JSON,
    status            VARCHAR(10) DEFAULT 'pass',
    execution_time_ms INT DEFAULT 0,
    FOREIGN KEY (execution_id) REFERENCES check_execution_log(execution_id),
    FOREIGN KEY (rule_id) REFERENCES quality_rules(rule_id)
);


