# Ingredient Stock Management

## Current behavior

Ingredient stock is changed only by the manual **Add stock** and **Deduct
stock** controls on the Ingredients page. Each successful manual adjustment
updates stock, records the legacy ingredient transaction, and writes a matching
Ingredient History row in the same transaction. Manual deductions that exceed
available stock are rejected.

Saving individual sales, uploading CSV/Excel sales, saving batch sales,
recording production outcomes, and generating predictions do not adjust
ingredient stock. No stock deduction is scheduled or triggered by those
workflows.

## Recipe data

The `BREAD_RECIPES` mapping in `app.py` is retained as recipe data. The
sales-saving paths do not read it or use it to update inventory. Keep recipe
quantities separate from stock adjustment logic if using them for future
reports or predictions.

## History and existing data

The Ingredient History table continues to store opening balances and manual
stock changes. Existing records—including rows created by the former
sales-triggered behavior—are preserved and remain visible. New sales and
production records do not create ingredient history entries.

The repository SQL definitions contain no stock-changing trigger, stored
procedure, or scheduled event. No trigger-removal migration is required for the
definitions checked into this repository. If a database has a separately
installed custom trigger, inspect it in that MySQL instance before removing it.

## Validation

Run the focused checks with:

```bash
python -m pytest -q tests/test_recipe_data_manual_inventory.py tests/test_ingredient_history.py
```

These tests verify that recipe data remains available, sales do not change
ingredient stock/history, and manual adjustments continue to update stock and
history.
