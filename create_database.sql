-- Create the prediction_system database
CREATE DATABASE IF NOT EXISTS prediction_system;
USE prediction_system;

-- Create sales_data table with all features
CREATE TABLE IF NOT EXISTS sales_data (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    bread_type VARCHAR(100) NOT NULL,
    products_produced INT NOT NULL DEFAULT 0,
    actual_qty_sold INT NOT NULL DEFAULT 0,
    waste_returns INT NOT NULL DEFAULT 0,
    price_per_product DECIMAL(10,2) NOT NULL DEFAULT 0.0,
    temperature DECIMAL(5,2) NOT NULL DEFAULT 30.0,
    is_holiday TINYINT(1) NOT NULL DEFAULT 0,
    is_promotion TINYINT(1) NOT NULL DEFAULT 0,
    sacks_used INT NOT NULL DEFAULT 0,
    plates_used INT NOT NULL DEFAULT 0,
    expense_amount DECIMAL(10,2) NOT NULL DEFAULT 0.0,
    is_historical TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_date_bread_type (date, bread_type),
    INDEX idx_date (date),
    INDEX idx_bread_type (bread_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @sales_historical_column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sales_data'
      AND COLUMN_NAME = 'is_historical'
);
SET @sales_historical_ddl = IF(
    @sales_historical_column_exists = 0,
    'ALTER TABLE sales_data ADD COLUMN is_historical TINYINT(1) NOT NULL DEFAULT 0',
    'SELECT 1'
);
PREPARE sales_historical_stmt FROM @sales_historical_ddl;
EXECUTE sales_historical_stmt;
DEALLOCATE PREPARE sales_historical_stmt;

-- Preserve uploaded source rows, but do not restore rows from before an intentional clear.
CREATE TABLE IF NOT EXISTS raw_sales_uploads (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE,
    bread_type VARCHAR(200),
    products_produced INT DEFAULT 0,
    actual_qty_sold INT DEFAULT 0,
    waste_returns INT DEFAULT 0,
    price_per_product DECIMAL(10,2) DEFAULT 0.0,
    temperature DECIMAL(5,2) DEFAULT 30.0,
    is_holiday TINYINT(1) DEFAULT 0,
    is_promotion TINYINT(1) DEFAULT 0,
    sacks_used INT DEFAULT 0,
    plates_used INT DEFAULT 0,
    expense_amount DECIMAL(10,2) DEFAULT 0.0,
    is_historical TINYINT(1) NOT NULL DEFAULT 0,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_raw_uploaded_at (uploaded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @raw_historical_column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'raw_sales_uploads'
      AND COLUMN_NAME = 'is_historical'
);
SET @raw_historical_ddl = IF(
    @raw_historical_column_exists = 0,
    'ALTER TABLE raw_sales_uploads ADD COLUMN is_historical TINYINT(1) NOT NULL DEFAULT 0',
    'SELECT 1'
);
PREPARE raw_historical_stmt FROM @raw_historical_ddl;
EXECUTE raw_historical_stmt;
DEALLOCATE PREPARE raw_historical_stmt;

CREATE TABLE IF NOT EXISTS sales_data_recovery_state (
    id TINYINT PRIMARY KEY,
    raw_upload_id_cutoff BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Active product catalog used for new sales and prediction entries.
CREATE TABLE IF NOT EXISTS bread_type_catalog (
    id INT AUTO_INCREMENT PRIMARY KEY,
    bread_type VARCHAR(100) NOT NULL UNIQUE,
    default_price DECIMAL(10,2) NOT NULL DEFAULT 4.50,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    shelf_life_days INT NOT NULL DEFAULT 5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Daily production batches and the FIFO sale allocations derived from sales_data.
CREATE TABLE IF NOT EXISTS production_batches (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    product_id INT NULL,
    bread_type VARCHAR(100) NOT NULL,
    production_date DATE NOT NULL,
    quantity_produced INT NOT NULL DEFAULT 0,
    quantity_sold INT NOT NULL DEFAULT 0,
    leftover_quantity INT NOT NULL DEFAULT 0,
    waste_quantity INT NOT NULL DEFAULT 0,
    expiry_date DATE NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    source_sales_id INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_production_batch_sales (source_sales_id),
    INDEX idx_production_batch_product_date (bread_type, production_date),
    INDEX idx_production_batch_expiry (expiry_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sales_batch_allocations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    sale_id INT NOT NULL,
    batch_id BIGINT NOT NULL,
    allocated_quantity INT NOT NULL,
    UNIQUE KEY uk_sales_batch_allocation (sale_id, batch_id),
    INDEX idx_sales_batch_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO bread_type_catalog (bread_type, default_price, is_active) VALUES
('Ube Pandesal', 4.50, 1),
('Cheese Bread', 4.50, 1),
('Ensaymada', 4.50, 1),
('Mamon', 4.50, 1),
('Star Bread', 4.50, 1),
('Pandesal', 4.50, 1),
('Stick Bread', 4.50, 1),
('Monggo Bread', 4.50, 1),
('Biscutcho', 4.50, 1),
('Loaf Bread', 4.50, 1),
('Teren', 4.50, 1),
('Burger Buns', 4.50, 1),
('Fita Favorita', 4.50, 1),
('Ugoy-Ugoy', 4.50, 1),
('Pandeleche', 4.50, 1),
('Sambag-Sambag', 4.50, 1),
('Munay', 4.50, 1),
('Cookies', 4.50, 1),
('Radio-Radio', 4.50, 1),
('Panchu', 4.50, 1),
('Pan', 4.50, 1),
('Sliced Pan (Katalogan)', 4.50, 1),
('Panches', 4.50, 1);

-- Create table for month-level expense totals
CREATE TABLE IF NOT EXISTS monthly_expenses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    scope_type ENUM('daily','weekly','monthly') NOT NULL DEFAULT 'monthly',
    period_key VARCHAR(20) NOT NULL,
    expense_month DATE NULL,
    category VARCHAR(100) NOT NULL,
    amount DECIMAL(12,2) NOT NULL DEFAULT 0.0,
    note VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_expense_scope (scope_type, period_key),
    INDEX idx_expense_month (expense_month),
    INDEX idx_category (category),
    UNIQUE KEY unique_scope_category (scope_type, period_key, category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Create ingredients inventory table
CREATE TABLE IF NOT EXISTS ingredients (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    current_stock DECIMAL(14,4) NOT NULL DEFAULT 0,
    unit VARCHAR(50) NOT NULL DEFAULT '',
    reorder_threshold DECIMAL(14,4) NOT NULL DEFAULT 0,
    last_restock DATETIME NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ingredient_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Record auditable ingredient stock changes; retain history if an ingredient is deleted
CREATE TABLE IF NOT EXISTS ingredient_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ingredient_id INT NULL,
    ingredient_name VARCHAR(200) NOT NULL,
    action_type ENUM('ADD','DEDUCT','INITIAL') NOT NULL,
    quantity_changed DECIMAL(14,4) NOT NULL,
    unit VARCHAR(50) NOT NULL DEFAULT '',
    stock_before DECIMAL(14,4) NOT NULL,
    stock_after DECIMAL(14,4) NOT NULL,
    remarks TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ingredient_history_ingredient (ingredient_id),
    INDEX idx_ingredient_history_created (created_at, id),
    CONSTRAINT fk_ingredient_history_ingredient
        FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Create users table for login system
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP NULL,
    is_active TINYINT(1) DEFAULT 1,
    INDEX idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Bakery access code used by the single-code login.
CREATE TABLE IF NOT EXISTS access_settings (
    id INT PRIMARY KEY,
    code_hash VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Create model_performance table
CREATE TABLE IF NOT EXISTS model_performance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    training_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    r2_score DECIMAL(5,4) NOT NULL,
    mae DECIMAL(10,2) NOT NULL,
    accuracy INT NOT NULL,
    training_count INT NOT NULL,
    test_count INT NOT NULL,
    model_version INT DEFAULT 1,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_training_date (training_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Create predictions table
CREATE TABLE IF NOT EXISTS predictions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    bread_type VARCHAR(100) NOT NULL,
    prediction_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    date_predicted DATE NOT NULL,
    temperature DECIMAL(5,2),
    is_holiday TINYINT(1) DEFAULT 0,
    is_promotion TINYINT(1) DEFAULT 0,
    predicted_demand INT NOT NULL,
    buffer INT DEFAULT 0,
    recommended_production INT NOT NULL,
    confidence INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_bread_type (bread_type),
    INDEX idx_date_predicted (date_predicted),
    INDEX idx_prediction_date (prediction_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Create production_plan table
CREATE TABLE IF NOT EXISTS production_plan (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    bread_type VARCHAR(100) NOT NULL,
    day_date DATE NOT NULL,
    planned_quantity INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_plan_date (plan_date),
    INDEX idx_bread_type (bread_type),
    INDEX idx_day_date (day_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Insert default admin user (password is empty string for demo - change in production)
INSERT IGNORE INTO users (username, password_hash, role) VALUES
('admin', '', 'admin');

-- Insert sample monthly expenses for current month
INSERT IGNORE INTO monthly_expenses (expense_month, category, amount, note) VALUES
('2026-04-01', 'Ingredients', 2500.00, 'Flour, sugar, eggs, and other baking ingredients'),
('2026-04-01', 'Utilities', 800.00, 'Electricity and water bills'),
('2026-04-01', 'Rent', 1200.00, 'Monthly shop rent'),
('2026-04-01', 'Equipment', 300.00, 'Equipment maintenance and supplies'),
('2026-04-01', 'Marketing', 150.00, 'Advertising and promotions'),
('2026-04-01', 'Insurance', 200.00, 'Business insurance'),
('2026-04-01', 'Transportation', 100.00, 'Delivery and transportation costs'),
('2026-04-01', 'Other', 50.00, 'Other small expenses');
