# Documentation Index

## Getting started

- [QUICK_START.md](QUICK_START.md): local setup and core pages.
- [USER_MANUAL.md](USER_MANUAL.md): concise workflow guide.

## Ingredient inventory

- [USING_INGREDIENT_DEDUCTION.md](USING_INGREDIENT_DEDUCTION.md): manual stock
  adjustments and history.
- [INGREDIENT_DEDUCTION.md](INGREDIENT_DEDUCTION.md): current stock behavior,
  recipe data, and validation details.
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md): current inventory
  behavior and focused tests.

Ingredient stock changes only through the manual Add stock and Deduct stock
controls on the Ingredients page. Sales, sales uploads, batch sales, production
outcomes, and predictions do not change ingredient stock. Existing ingredient
history is preserved.

## Recipe data

`BREAD_RECIPES` remains defined in `app.py`. It is retained as recipe data but
is not used by sales or production workflows to change inventory.

## Other project documentation

- [DEPLOYMENT.md](DEPLOYMENT.md): deployment configuration.
- [ALPHA_TEST_GUIDE.md](ALPHA_TEST_GUIDE.md): system testing guide.
- [DOCUMENTATION_INDEX.md](DOCUMENTATION_INDEX.md): this index.

## Verification

Run the full test suite with:

```bash
python -m pytest -q
```
