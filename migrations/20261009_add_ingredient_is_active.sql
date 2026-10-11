-- Idempotent soft-delete migration for the selected MySQL database.
SET @add_ingredient_is_active_sql = (
    SELECT IF(
        COUNT(*) = 0,
        'ALTER TABLE `ingredients` ADD COLUMN `is_active` TINYINT(1) NOT NULL DEFAULT 1',
        'SELECT 1'
    )
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ingredients'
      AND COLUMN_NAME = 'is_active'
);

PREPARE add_ingredient_is_active_stmt FROM @add_ingredient_is_active_sql;
EXECUTE add_ingredient_is_active_stmt;
DEALLOCATE PREPARE add_ingredient_is_active_stmt;
