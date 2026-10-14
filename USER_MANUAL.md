# Che Che Bakeshop Production Predictive System

## User Manual

This manual explains how bakery staff can use the system to review business
activity, record sales and production, create demand forecasts, and manage
ingredient stock and expenses. It covers user tasks only; it does not contain
program code or developer setup instructions.

## 1. Open the system and sign in

1. On the bakery computer, double-click the **Che Che System** desktop
   shortcut.
2. Wait for the system to open in your web browser.
3. Enter the bakery access code and select **Access Dashboard**.
4. To check what you entered before signing in, select **Show Password**.

If the system does not open, or you do not know the access code, contact the
person responsible for maintaining the system. Do not change application files
or computer settings.

### Change the access code

1. On the sign-in page, select **Change access code**.
2. Enter the current access code.
3. Enter a new code of at least six characters, then enter it again to confirm.
4. Select **Save access code**. Return to the sign-in page and use the new code.

Keep the access code private. Use **Logout** in the navigation bar when you
finish.

## 2. Navigate the system

The navigation bar provides access to:

- **Dashboard** — business summaries, charts, and ingredient alerts.
- **Predict Demand** — generate a demand forecast.
- **Sales Log** — enter or upload sales and review sales history.
- **Production Log** — review forecasts and record actual production.
- **Ingredients** — review stock, record stock changes, and enter ingredient
  expenses.
- **Model Performance** — review forecast metrics and retrain the model.
- **About** — information about the bakery and system.

Some additional history pages can be opened from the related screen, such as
**View Production Log History**.

## 3. Use the Dashboard

The Dashboard summarizes recorded activity, including units sold, revenue,
expenses, profit, model accuracy, and waste-reduction information.

1. Choose a reporting period: **Daily**, **Weekly**, **Monthly**, or **All Data**.
2. If a date, week, or month selector appears, choose the period you want.
3. Select **Apply** to refresh the view.
4. Review the bread-type distribution and any **Active Alerts** for ingredients
   that need restocking.
5. Where available, use the period comparison and export controls to review or
   download a summary.

Dashboard figures are based on information entered in the system. Check the
Sales Log, Production Log, and Ingredients page if a figure looks incomplete.

## 4. Record and review sales

### Enter sales manually

1. Open **Sales Log**.
2. In **Manual Entry**, select the start date and date scope. The scope can cover
   one day or several days, up to one week.
3. Choose a bread type from **Add a bread**. Repeat for each bread type to
   record.
4. Check the displayed production amount and enter the price and quantity sold.
   The sold-quantity field is available when actual production has been
   recorded for that bread and date.
5. Check the dates and quantities, then select **Save All Records**.

If the production amount is missing or incorrect, open **Production Log** and
record or correct actual production first. Do not enter a sold quantity greater
than the amount produced.

### Upload sales

1. Select **Upload** on the Sales Log.
2. Choose the sales spreadsheet to upload. Excel workbooks in `.xls` or `.xlsx`
   format are accepted.
3. Review the message shown after the upload. Check the Sales Log history to
   confirm the records appear as expected.

### Review or correct sales history

- Select **View History** to jump to the history table.
- Use the date-period controls or the date and bread-type filters to narrow the
  records.
- Review the produced, sold, waste, price, and earnings columns.
- Select **Edit** beside a record to correct it, then save the change.
- Confirm the selected date and bread type before editing.

The Sales Log also shows sales and financial summaries for the selected
period. Take care with any action that asks to clear records; clearing data can
remove information needed for reporting.

### Manage bread types

Use **Add new product** to enter the product name and default price. Use
**Delete product** only when a product should no longer be available for future
entries. Deleting a product removes it from future-entry selectors; existing
sales history is preserved.

## 5. Generate a demand forecast

1. Open **Predict Demand**.
2. Choose one bread type, or choose **All** to forecast all available types.
3. Select the date to forecast.
4. Enter the expected temperature in degrees Celsius.
5. Mark **Holiday** or **Peak Day** when applicable.
6. Select **Generate Forecast**.
7. Review the results:
   - **Predicted Demand** is the estimated number customers may want.
   - **Safety Buffer** is the additional amount included for planning.
   - **Recommended Production** is the suggested amount to prepare.
   - **Confidence** indicates the model's confidence for the result.
8. Use the recommendation as a planning aid and consider current bakery
   conditions before deciding what to produce.

Submitted forecasts are saved and can be reviewed through the Production Log.

## 6. Record actual production and review production history

### Record actual production

1. Open **Production Log**.
2. Use the bread-type and date filters to find the correct row. Select
   **Apply filters**.
3. Enter the actual number produced in the **Actual Production** field.
4. Wait for the on-screen save status before leaving the page.
5. Open **Sales Log** and record the number sold for the same bread type and
   date.

The log shows predicted demand, recommended production, actual production,
sold quantity, leftover quantity, and waste. Leftover and waste figures are
calculated from recorded production and sales; they may change as records are
updated.

### Review or print production history

1. Select **View Production Log History** from the Production Log.
2. Filter by bread type and date range. Choose a preset range or a custom range,
   then select **Apply filters**.
3. Review the totals and individual records.
4. To print, choose a print range and its date details, then select **Print
   Log**.

## 7. Manage ingredient inventory

The Ingredients page shows current quantities, units, stock status, and
low-stock alerts.

### Record a stock change

1. Find the ingredient in **Stock Level of Ingredients**.
2. Select the plus control to add stock, or the minus control to record stock
   used.
3. Choose **Stock In** or **Stock Out**, enter the quantity, and optionally add
   a reason.
4. Select **Save** and check that the displayed stock and status have updated.

Enter a positive quantity. A deduction cannot exceed the available stock.
Ingredient quantities change only when a staff member records a manual stock
adjustment; sales, production, and forecasts do not automatically change
ingredient stock.

### Review ingredient history

Use **Ingredient History** to filter by ingredient and date range. The history
shows recorded stock changes and opening balances. Select **Print** if you need
a paper copy.

### Add or delete an ingredient

- Select **Add new ingredient** to enter an ingredient name and suggested unit.
- Use **Delete ingredient** only when it should no longer appear in ingredient
  selectors. The system keeps its existing stock records and history.

## 8. Record ingredient expenses

The Ingredients page includes an **Ingredients Expense Breakdown** section.

1. Choose a scope: **Daily**, **Weekly**, or **Monthly**.
2. Select the date, week, or month you want to record.
3. Select **Apply Scope**.
4. Enter the amount and unit for each ingredient expense.
5. Select **Save Expenses**.
6. Use **View Details** to review saved expense history. Printing controls are
   available for the summary and history.

Check the selected period before saving so expenses are recorded against the
correct date or week.

## 9. Review model performance

Open **Model Performance** to review the model's R² score, mean absolute error
(MAE), accuracy, training and validation record counts, and feature importance.
Select **Retrain Model** only when an authorized user intends to refresh the
model using the available sales history. Allow the operation to finish before
leaving the page.

## 10. Good operating practices

- Enter the correct date and bread type before saving any record.
- Record actual production before entering its sales.
- Review saved records after entry or upload.
- Record ingredient deliveries and usage with manual stock adjustments.
- Add a short reason to stock adjustments when useful for later review.
- Do not clear sales, expense, or other records unless authorized.
- Log out when finished, especially on a shared computer.

## 11. If something does not look right

- **Cannot sign in:** check the access code and contact the system
  administrator if it is unavailable.
- **Forecast or history is empty:** check the selected date, bread type, and
  filters. Confirm that sales or forecast records have been entered.
- **Cannot enter a sold quantity:** record actual production for the matching
  bread type and date first.
- **Stock adjustment is rejected:** confirm that the quantity is positive and
  that a stock deduction does not exceed the current balance.
- **A save or upload reports an error:** note the screen and action, avoid
  repeating destructive actions, and contact the system administrator.
