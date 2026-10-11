-- Additive, idempotent schema migration. Does not alter existing sales or production rows.
SET @add_shelf_life_sql = (
    SELECT IF(
        COUNT(*) = 0,
        'ALTER TABLE `bread_type_catalog` ADD COLUMN `shelf_life_days` INT NOT NULL DEFAULT 5',
        'SELECT 1'
    )
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'bread_type_catalog'
      AND COLUMN_NAME = 'shelf_life_days'
);

PREPARE add_shelf_life_stmt FROM @add_shelf_life_sql;
EXECUTE add_shelf_life_stmt;
DEALLOCATE PREPARE add_shelf_life_stmt;

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
