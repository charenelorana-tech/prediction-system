# Ingredient Inventory Behavior

## Current behavior

- Ingredient stock changes only through the manual Add stock and Deduct stock
  controls on the Ingredients page.
- Sales entry, CSV/Excel uploads, batch sales, production outcome logging, and
  predictions do not change ingredient stock.
- Manual stock updates and their Ingredient History records are committed
  together. Manual deductions larger than current stock are rejected.
- Existing history is preserved, including records written by the former
  automatic-deduction behavior.
- `BREAD_RECIPES` remains in `app.py` as recipe data; it is no longer invoked
  by the sales-saving workflows.

## Tests

Run:

```bash
python -m pytest -q tests/test_recipe_data_manual_inventory.py tests/test_ingredient_history.py
```

The coverage checks retained recipe data, no stock mutation after sales, and
continued manual adjustment/history behavior.
