# Manual Ingredient Stock Adjustments

Ingredient stock is managed manually. Saving a sale, uploading sales, saving a
batch, recording a production outcome, or generating a prediction does not
change ingredient quantities.

## Add or deduct stock

1. Open **Ingredients Inventory** and locate the ingredient.
2. Select **Add stock** to record a delivery, or **Deduct stock** to record
   usage, spoilage, or another manual adjustment.
3. Enter a positive quantity and optionally a remark.
4. Save. The stock change and its history entry are committed together.

Manual deductions cannot exceed the current stock. The page reports an error
without changing stock or history if a deduction is too large.

## Review history

The Ingredient History section shows opening balances and manual stock
adjustments. Use its searchable ingredient selector and date range filters to
review records. Historical records from before manual-only stock management are
preserved as-is and are not deleted.

## Recipe data

Bread-to-ingredient recipe quantities remain in `app.py` as data. They are not
used to change stock when sales or production are saved.
