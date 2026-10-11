# Quick Start

## Ingredients

Ingredient stock changes only when you use the manual **Add stock** or
**Deduct stock** controls on the Ingredients page. Saving or uploading sales,
saving batch records, recording production outcomes, and creating predictions
do not change ingredient stock.

1. Open **Ingredients Inventory**.
2. Find the ingredient in **Stock Levels**.
3. Choose **Add stock** or **Deduct stock**, enter a positive quantity, and
   optionally add a reason.
4. Save the adjustment. The stock level and Ingredient History update together.
   A manual deduction greater than the available stock is rejected.

Use the **Ingredient History** filters to review changes by ingredient and date.
History records created by the previous automatic-deduction behavior remain
available; they are not removed.

## Sales, Production, and Predictions

Sales and production records continue to save as before, and predictions remain
available. These workflows do not adjust ingredient inventory. Bread recipe
data remains in the application for future reporting or prediction use.

## Local setup

- Install dependencies from `requirements.txt`.
- Start the app with `python app.py`.
- Sign in, then open the Sales Log, Production Plan, Predict, or Ingredients
  Inventory pages as needed.
