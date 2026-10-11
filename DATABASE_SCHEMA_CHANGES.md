# 📊 DATABASE SCHEMA CHANGES - DETAILED VIEW

## New Tables Created

### 1. `production_batches` Table
Tracks each day's production per bread type as a batch, linked to FIFO sales allocation.

```sql
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
```

**Key Fields Explained:**
- `production_date`: When this batch was produced
- `expiry_date`: production_date + shelf_life_days (auto-calculated)
- `quantity_produced`: Units made
- `quantity_sold`: Units sold (from FIFO allocation)
- `leftover_quantity`: Units still in stock (produced - sold)
- `waste_quantity`: Units lost (only counted after expiry_date)
- `source_sales_id`: Links to the sales_data record that triggered batch creation

---

### 2. `sales_batch_allocations` Table
Records which batches each sale draws from (FIFO order).

```sql
CREATE TABLE IF NOT EXISTS sales_batch_allocations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    sale_id INT NOT NULL,
    batch_id BIGINT NOT NULL,
    allocated_quantity INT NOT NULL,
    UNIQUE KEY uk_sales_batch_allocation (sale_id, batch_id),
    INDEX idx_sales_batch_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Why This Matters:**
- When a sale of 100 units exists, but 3 batches provide it (Batch1: 50, Batch2: 30, Batch3: 20), this table records those allocations.
- If the sale is edited to 80, we know Batch1 loses 20, Batch2 loses 0, Batch3 loses 0.
- If the sale is deleted, all batch quantities are restored.
- This ensures waste calculation is always correct.

---

### 3. `ingredient_history` Table
Audit trail for all ingredient stock changes.

```sql
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
```

**Key Fields:**
- `action_type`: 'ADD', 'DEDUCT', or 'INITIAL' (first entry)
- `quantity_changed`: The amount added/removed (always positive, action_type tells direction)
- `stock_before` / `stock_after`: The stock level before and after this action
- `remarks`: Optional context (e.g., "delivery", "spoilage", "production use")
- `ingredient_name`: Stored as text so history stays readable if ingredient is deleted

**Example Scenario:**
```
1. Add 50 kg flour → INSERT ingredient_history (action=ADD, qty=50, before=0, after=50)
2. Deduct 10 kg   → INSERT ingredient_history (action=DEDUCT, qty=10, before=50, after=40)
3. Later, if you delete the ingredient → ingredient_id becomes NULL, but ingredient_name='Flour' remains
```

---

### 4. `bread_type_catalog` Table (New)
Product management table with soft delete.

```sql
CREATE TABLE IF NOT EXISTS bread_type_catalog (
    id INT AUTO_INCREMENT PRIMARY KEY,
    bread_type VARCHAR(100) NOT NULL UNIQUE,
    default_price DECIMAL(10,2) NOT NULL DEFAULT 4.50,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    shelf_life_days INT NOT NULL DEFAULT 5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Key Fields:**
- `is_active`: 1 = can be used for new sales/production; 0 = soft deleted
- `shelf_life_days`: Days until expiry (default 5)
- All 23 bread types are pre-inserted with `is_active = 1`

---

## Tables Modified

### 5. `ingredients` Table Enhancement
Added soft delete support.

```sql
ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS is_active TINYINT(1) NOT NULL DEFAULT 1;
```

**Change:**
- All existing ingredients have `is_active = 1` by default
- When a user deletes an ingredient, `is_active` is set to 0
- All ingredient selectors query only `WHERE is_active = 1`

---

### 6. `sales_data` Table (No Changes)
Original structure is completely preserved:
```sql
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_date_bread_type (date, bread_type),
    INDEX idx_date (date),
    INDEX idx_bread_type (bread_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**No changes**: This is your existing sales data, completely untouched.

---

## Data Relationships Diagram

```
sales_data (Existing)
    │
    ├─ Links to: production_batches
    │   └─ production_batches.source_sales_id = sales_data.id
    │
    └─ Links to: sales_batch_allocations
        └─ sales_batch_allocations.sale_id = sales_data.id
            └─ sales_batch_allocations.batch_id = production_batches.id

production_batches (New)
    ├─ product_id → bread_type_catalog.id (optional, for product features)
    ├─ bread_type → matches sales_data.bread_type
    └─ production_date + expiry_date → used for FIFO and waste calc

ingredients (Existing)
    ├─ Now has: is_active column
    └─ Links to: ingredient_history
        └─ ingredient_history.ingredient_id = ingredients.id

ingredient_history (New)
    └─ Records every stock change (ADD, DEDUCT, INITIAL)

bread_type_catalog (New)
    ├─ is_active = 1: Product can be used
    ├─ is_active = 0: Product is deleted (soft delete)
    ├─ shelf_life_days: Controls expiry_date = production_date + shelf_life_days
    └─ product_id = bread_type_catalog.id (future use for product features)
```

---

## How Data Flows Through the System

### Scenario: Produce 100, Sell 60, Then 30, Wait 5 Days

**Day 1 - Production:**
```
INSERT INTO sales_data (date, bread_type, products_produced) 
    VALUES ('2025-01-15', 'Pandesal', 100);

INSERT INTO production_batches (bread_type, production_date, quantity_produced, expiry_date, unit_price)
    VALUES ('Pandesal', '2025-01-15', 100, '2025-01-20', 4.50);
    -- id = 1
```

**Day 1 - First Sale (60 units):**
```
INSERT INTO sales_data (date, bread_type, actual_qty_sold)
    VALUES ('2025-01-15', 'Pandesal', 60);
    -- id = 2

UPDATE production_batches SET quantity_sold = 60, leftover_quantity = 40 
    WHERE id = 1;

INSERT INTO sales_batch_allocations (sale_id, batch_id, allocated_quantity)
    VALUES (2, 1, 60);
    -- Batch 1 now has: produced=100, sold=60, leftover=40
```

**Day 2 - Second Sale (30 units):**
```
INSERT INTO sales_data (date, bread_type, actual_qty_sold)
    VALUES ('2025-01-16', 'Pandesal', 30);
    -- id = 3

UPDATE production_batches SET quantity_sold = 90, leftover_quantity = 10
    WHERE id = 1;
    -- Batch 1 now has: produced=100, sold=90, leftover=10

INSERT INTO sales_batch_allocations (sale_id, batch_id, allocated_quantity)
    VALUES (3, 1, 30);
```

**Day 6 - Waste Calculated (After Expiry):**
```
-- Batch 1 expires on 2025-01-20 (Day 5)
-- On Day 6, the waste calculation runs:

UPDATE production_batches SET waste_quantity = 10
    WHERE id = 1 AND expiry_date < CURRENT_DATE;
    -- Now: produced=100, sold=90, leftover=0, waste=10

-- Display on Production Log:
-- "Pandesal" for 2025-01-15 shows:
--   Actual: 100, Sold: 90, Leftover: 0, Waste: 10 (₱45.00)
```

---

## Migration Strategy

### Local MySQL:
Run `create_database.sql` once to set up all new tables.

### Railway MySQL:
The `sync_to_railway.py` script:
1. Creates `bread_type_catalog` if missing
2. Adds `is_active` to `ingredients` if missing
3. Creates `production_batches` if missing
4. Creates `sales_batch_allocations` if missing
5. Creates `ingredient_history` if missing
6. Syncs all data with soft-delete columns preserved

### Existing Data:
- All historical `sales_data` records are preserved
- New `production_batches` are calculated from existing sales_data + predictions
- New `ingredient_history` rows are inserted for each new ingredient add/deduct action
- Deleted ingredients/products are marked `is_active = 0` (not deleted)

---

## Indexes for Performance

```sql
-- production_batches indexes
INDEX idx_production_batch_product_date (bread_type, production_date)
    -- Fast queries like: WHERE bread_type = 'Pan' AND production_date BETWEEN X AND Y

INDEX idx_production_batch_expiry (expiry_date)
    -- Fast queries like: WHERE expiry_date < CURRENT_DATE (for waste calc)

-- ingredient_history indexes
INDEX idx_ingredient_history_ingredient (ingredient_id)
    -- Fast queries like: WHERE ingredient_id = 5

INDEX idx_ingredient_history_created (created_at, id)
    -- Fast queries like: WHERE created_at >= ? AND created_at <= ? ORDER BY created_at DESC
```

---

## CSRF & Security

All new routes use parameterized SQL and CSRF tokens:
- `_csrf_token` generated in `app.py` and injected into all forms
- POST routes validate token before processing
- All ingredient/product delete operations are POST (not GET)
- Soft delete ensures no data is permanently lost

---

## Test Coverage

Database changes are validated by:
- `tests/test_ingredient_history.py` → ingredient add/deduct/history
- `tests/test_production_inventory.py` → production batch and FIFO logic
- `tests/test_ingredient_creation.py` → ingredient soft delete
- `tests/test_production_log.py` → filtering and waste calculation
- All 65 tests passing

---

## FAQ

**Q: What if I have old production data without batches?**
A: The `calculate_production_batches()` function in `production_inventory.py` creates batches from existing `sales_data` on first load. No data is lost.

**Q: Can I recover a deleted ingredient/product?**
A: Yes! Re-add it with the same name to set `is_active = 1`. History will reattach to the same ingredient.

**Q: Does deletion sync to Railway?**
A: Yes! The `sync_to_railway.py` script includes the `is_active` column, so deleted items (is_active=0) stay deleted after sync.

**Q: Can I change shelf_life_days per product?**
A: Yes! Edit the `shelf_life_days` value in `bread_type_catalog`, and future batches will use that value.

**Q: What about old batches with the wrong shelf life?**
A: Historical batches keep their original `expiry_date`. Future production uses the updated `shelf_life_days`.

