#!/usr/bin/env python3
"""
Database initialization script for the prediction system.
Run this once to set up all required tables.
"""

import os
import sys
import mysql.connector

# Configuration (matches app.py)
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", 3306))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "prediction_system")

def create_database():
    """Create the database and tables"""
    try:
        # Connect without specifying database
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()
        
        # Create database
        print(f"Creating database '{DB_NAME}'...")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
        conn.commit()
        cursor.close()
        conn.close()
        
        # Connect to the database
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        cursor = conn.cursor()

        print("Creating ingredients table...")
        cursor.execute("""
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("SHOW COLUMNS FROM ingredients LIKE 'is_active'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE ingredients ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1"
            )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bread_type_catalog (
                id INT AUTO_INCREMENT PRIMARY KEY,
                bread_type VARCHAR(100) NOT NULL UNIQUE,
                default_price DECIMAL(10,2) NOT NULL DEFAULT 4.50,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("SHOW COLUMNS FROM bread_type_catalog LIKE 'default_price'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE bread_type_catalog ADD COLUMN default_price DECIMAL(10,2) NOT NULL DEFAULT 4.50"
            )
        cursor.execute("SHOW COLUMNS FROM bread_type_catalog LIKE 'is_active'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE bread_type_catalog ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1"
            )
        cursor.execute("SHOW COLUMNS FROM bread_type_catalog LIKE 'shelf_life_days'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE bread_type_catalog ADD COLUMN shelf_life_days INT NOT NULL DEFAULT 5"
            )
        cursor.execute("""
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales_batch_allocations (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                sale_id INT NOT NULL,
                batch_id BIGINT NOT NULL,
                allocated_quantity INT NOT NULL,
                UNIQUE KEY uk_sales_batch_allocation (sale_id, batch_id),
                INDEX idx_sales_batch_batch (batch_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("""
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("SET time_zone = '+08:00'")
        cursor.execute("""
            INSERT INTO ingredient_history (
                ingredient_id, ingredient_name, action_type, quantity_changed,
                unit, stock_before, stock_after, remarks
            )
            SELECT i.id, i.name, 'INITIAL', i.current_stock, i.unit, 0, i.current_stock,
                   'Opening balance when ingredient history was enabled'
            FROM ingredients AS i
            LEFT JOIN ingredient_history AS h ON h.ingredient_id = i.id
            WHERE h.id IS NULL
        """)
        conn.commit()
        
        # Create sales_data table
        print("Creating sales_data table...")
        cursor.execute("""
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("SHOW COLUMNS FROM sales_data LIKE 'is_historical'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE sales_data ADD COLUMN is_historical TINYINT(1) NOT NULL DEFAULT 0"
            )
        conn.commit()
        
        # Create users table for login
        print("Creating users table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(50) DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP NULL,
                is_active TINYINT(1) DEFAULT 1,
                INDEX idx_username (username)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        
        # Create model_performance table
        print("Creating model_performance table...")
        cursor.execute("""
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()

        # Create monthly_expenses table
        print("Creating monthly_expenses table...")
        cursor.execute("""
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        
        # Create predictions table
        print("Creating predictions table...")
        cursor.execute("""
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        
        # Create production_plan table
        print("Creating production_plan table...")
        cursor.execute("""
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        
        # Insert default admin user
        print("Inserting default admin user...")
        cursor.execute("""
            INSERT IGNORE INTO users (username, password_hash, role) VALUES (%s, %s, %s)
        """, ('admin', '', 'admin'))
        conn.commit()
        
        # Insert sample monthly expenses
        print("Inserting sample monthly expenses...")
        sample_expenses = [
            ('2026-04-01', 'Ingredients', 2500.00, 'Flour, sugar, eggs, and other baking ingredients'),
            ('2026-04-01', 'Utilities', 800.00, 'Electricity and water bills'),
            ('2026-04-01', 'Rent', 1200.00, 'Monthly shop rent'),
            ('2026-04-01', 'Equipment', 300.00, 'Equipment maintenance and supplies'),
            ('2026-04-01', 'Marketing', 150.00, 'Advertising and promotions'),
            ('2026-04-01', 'Insurance', 200.00, 'Business insurance'),
            ('2026-04-01', 'Transportation', 100.00, 'Delivery and transportation costs'),
            ('2026-04-01', 'Other', 50.00, 'Other small expenses')
        ]
        cursor.executemany("""
            INSERT IGNORE INTO monthly_expenses (expense_month, category, amount, note) 
            VALUES (%s, %s, %s, %s)
        """, sample_expenses)
        conn.commit()
        
        print("\n✓ Database initialized successfully!")
        print(f"Database: {DB_NAME}")
        print(f"Host: {DB_HOST}:{DB_PORT}")
        print("\nTables created:")
        print("  - sales_data")
        print("  - users")
        print("  - model_performance")
        print("  - monthly_expenses")
        print("  - ingredients")
        print("  - ingredient_history")
        print("  - predictions")
        print("  - production_plan")
        
        cursor.close()
        conn.close()
        
    except mysql.connector.Error as err:
        print(f"✗ Error: {err}")
        sys.exit(1)

if __name__ == "__main__":
    create_database()
