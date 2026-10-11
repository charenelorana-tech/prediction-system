-- Idempotent soft-delete migration for the selected MySQL database.
SET @add_product_is_active_sql = (
    SELECT IF(
        COUNT(*) = 0,
        'ALTER TABLE `bread_type_catalog` ADD COLUMN `is_active` TINYINT(1) NOT NULL DEFAULT 1',
        'SELECT 1'
    )
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'bread_type_catalog'
      AND COLUMN_NAME = 'is_active'
);

PREPARE add_product_is_active_stmt FROM @add_product_is_active_sql;
EXECUTE add_product_is_active_stmt;
DEALLOCATE PREPARE add_product_is_active_stmt;
