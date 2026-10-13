import csv
import io
import json
import os
import random
import math
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

import mysql.connector
import numpy as np
import pandas as pd
from flask import Flask, abort, render_template, request, redirect, url_for, session, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
import logging
from production_inventory import (
    available_stock_for_day,
    calculate_production_batches,
    sales_overstock_warnings,
)

# Main application:
# This Flask app manages bakery sales records, trains a demand prediction model,
# generates weekly production logs, and displays dashboards for performance.
# It uses MySQL when enabled, with a fallback to JSON data loading.
# The routes support login, sales logging, prediction, production logging, and model metrics.
# Require XGBoost for training and prediction
try:
    from xgboost import XGBRegressor
    USE_XGBOOST = True
    print("Using XGBoost for ML predictions")
except ImportError as err:
    raise ImportError(
        "XGBoost is required for this system. Install the xgboost package before running the app."
    ) from err

# Application prefix and Flask setup
# The app can be mounted under a subpath like /prediction_system, but in local
# root-hosted previews we default to the site root unless an explicit prefix is
# provided through APP_PREFIX. We still keep a compatibility prefix at the WSGI
# layer so older /prediction_system links continue to resolve safely.
DEFAULT_APP_PREFIX = ""
COMPAT_PREFIX = "/prediction_system"

def resolve_app_prefix(raw_prefix=None):
    prefix = raw_prefix if raw_prefix is not None else os.environ.get("APP_PREFIX", DEFAULT_APP_PREFIX)
    if not prefix or prefix in ("/", "\\"):
        return ""
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/")


PREFIX = resolve_app_prefix()
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path=f"{PREFIX}/static",
)
app.config["APPLICATION_ROOT"] = ""
app.config["DEBUG"] = os.environ.get("FLASK_DEBUG", "1").strip().lower() in ("1", "true", "yes", "on")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD", "chechebakeshop").strip()

if PREFIX:
    app.static_url_path = f"{PREFIX}/static"
else:
    app.static_url_path = "/static"

# Logging
logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)

@app.context_processor
def inject_prefix():
    # Make the mount prefix available to all Jinja templates so links include it when needed
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return {"PREFIX": PREFIX, "csrf_token": token}

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "src", "data", "initial_data.json")

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", 3306))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "prediction_system")
USE_MYSQL = os.environ.get("USE_MYSQL", "1").strip().lower() in ("1", "true", "yes", "on")
PRODUCTION_OUTCOME_CUTOFF = date(2026, 10, 11)

BREAD_TYPES = [
    'Ube Pandesal', 'Cheese Bread', 'Ensaymada', 'Mamon', 'Star Bread',
    'Pandesal', 'Stick Bread', 'Monggo Bread', 'Biscutcho', 'Loaf Bread',
    'Teren', 'Burger Buns', 'Fita Favorita', 'Ugoy-Ugoy', 'Pandeleche',
    'Sambag-Sambag', 'Munay', 'Cookies', 'Radio-Radio', 'Panchu',
    'Pan', 'Sliced Pan (Katalogan)', 'Panches'
]
BREAD_PRICES = {bread_type: 4.5 for bread_type in BREAD_TYPES}
INACTIVE_BREAD_TYPES = set()

# Bread recipes: ingredient_name -> quantity per unit produced
BREAD_RECIPES = {
    'Pandesal': {
        'Flour (1st Class)': 0.08,
        'Sugar': 0.005,
        'Vegetable Oil': 0.003,
        'Margarine': 0.002,
        'Yeast': 0.001,
        'Salt': 0.0005,
        'Baking Powder': 0.0005,
    },
    'Ube Pandesal': {
        'Flour (1st Class)': 0.08,
        'Sugar': 0.006,
        'Vegetable Oil': 0.003,
        'Margarine': 0.002,
        'Yeast': 0.001,
        'Salt': 0.0005,
        'Baking Powder': 0.0005,
    },
    'Cheese Bread': {
        'Flour (1st Class)': 0.1,
        'Sugar': 0.007,
        'Vegetable Oil': 0.004,
        'Margarine': 0.003,
        'Yeast': 0.0015,
        'Salt': 0.0005,
        'Baking Powder': 0.0008,
    },
    'Ensaymada': {
        'Flour (1st Class)': 0.12,
        'Sugar': 0.015,
        'Vegetable Oil': 0.005,
        'Margarine': 0.008,
        'Lard': 0.004,
        'Yeast': 0.002,
        'Salt': 0.0008,
    },
    'Mamon': {
        'Flour (3rd Class)': 0.06,
        'Sugar': 0.04,
        'Vegetable Oil': 0.025,
        'Baking Powder': 0.005,
        'Salt': 0.0005,
    },
    'Burger Buns': {
        'Flour (1st Class)': 0.15,
        'Sugar': 0.01,
        'Vegetable Oil': 0.005,
        'Margarine': 0.004,
        'Yeast': 0.002,
        'Salt': 0.0008,
        'Baking Powder': 0.001,
    },
}

EXPENSE_CATEGORIES = [
    'Flour (1st Class)',
    'Flour (3rd Class)',
    'Sugar',
    'Vegetable Oil',
    'Margarine',
    'Lard',
    'Butter Milk',
    'Yeast',
    'Salt',
    'Baking Powder',
    'Anti-Amag',
    'Amoniaco'
]

# Default ingredients with units for fallback display when DB empty
DEFAULT_INGREDIENTS = [
    {"name": "Flour (1st Class)", "unit": "sack", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Flour (3rd Class)", "unit": "sack", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Sugar", "unit": "sack", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Vegetable Oil", "unit": "container", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Margarine", "unit": "tubs(40kgs)", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Lard", "unit": "tubs(40kgs)", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Butter Milk", "unit": "bags", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Yeast", "unit": "kgs", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Salt", "unit": "sack", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Baking Powder", "unit": "bags", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Anti-Amag", "unit": "kgs", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Amoniaco", "unit": "kgs", "current_stock": 0.0, "reorder_threshold": 0.0},
]

# In-memory application state used during requests.
# SALES_DATA is the loaded sales dataset. MODEL stores the trained ML model.
# WEEKLY_PLAN and MODEL_METRICS are cached values for display in the dashboard.
WEEKLY_PLAN = []
MODEL_METRICS = {}
MODEL = None
SALES_DATA = []
INGREDIENTS = []
INGREDIENT_HISTORY = []
MONTHLY_EXPENSES = {}
MANILA_TZ = timezone(timedelta(hours=8))
STOCK_QUANTUM = Decimal("0.0001")
INGREDIENT_HISTORY_PAGE_SIZE = 50


class PrefixMiddleware:
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith(self.prefix):
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + self.prefix
            environ["PATH_INFO"] = path[len(self.prefix):]
            if environ["PATH_INFO"] == "":
                environ["PATH_INFO"] = "/"
            return self.app(environ, start_response)
        # If the request does not include the mount prefix, pass through
        # unchanged so the app can be accessed at the root during tests
        # or when served without a mount point.
        return self.app(environ, start_response)


def parse_date(value):
    """Convert a string or date-like value to a datetime.date object."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        parts = [p.strip() for p in str(value).split("/")]
        if len(parts) == 3:
            month, day, year = parts
            if len(year) == 2:
                year = f"20{year}"
            return date(int(year), int(month), int(day))
    except Exception:
        pass
    return None


def format_date(value):
    d = parse_date(value)
    if not d:
        return value or ""
    return d.strftime("%b %d, %Y")


def deduplicate_sales_rows(rows):
    """Remove duplicate sales rows while preserving the first occurrence."""
    seen = set()
    deduped = []
    for row in rows or []:
        normalized = normalize_record(row)
        signature = (
            normalized.get("date"),
            normalized.get("bread_type"),
            normalized.get("products_produced"),
            normalized.get("actual_qty_sold"),
            normalized.get("waste_returns"),
            normalized.get("price_per_product"),
            normalized.get("temperature"),
            normalized.get("is_holiday"),
            normalized.get("is_promotion"),
            normalized.get("sacks_used"),
            normalized.get("plates_used"),
            normalized.get("expense_amount"),
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(normalized)
    return deduped


def normalize_record(row):
    """Normalize incoming sales rows into a consistent internal record format."""
    normalized_row = {
        "".join(character for character in str(key).casefold() if character.isalnum()): value
        for key, value in row.items()
    }

    def get_value(*names, default=None):
        for name in names:
            key = "".join(character for character in name.casefold() if character.isalnum())
            value = normalized_row.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if pd.isna(value):
                continue
            return value
        return default

    parsed = None
    for date_field in ("date", "production_date", "sale_date", "baked_day"):
        parsed = parse_date(get_value(date_field))
        if parsed:
            break
    record = {
        "date": parsed.isoformat() if parsed else "",
        "bread_type": get_value(
            "bread_type", "bread", "product_type", "product_name", default="Pandesal"
        ),
        "products_produced": int(float(get_value(
            "products_produced", "produced", "quantity_produced", default=0
        ))),
        "actual_qty_sold": int(float(get_value(
            "actual_qty_sold", "sales", "sold", "quantity_sold", "qty_sold", default=0
        ))),
        "waste_returns": int(float(get_value(
            "waste_returns", "waste", default=0
        ))),
        "price_per_product": float(get_value(
            "price_per_product", "price", "unit_price", "selling_price", default=0.0
        )),
        "temperature": float(get_value("temperature", default=30.0)),
        "is_holiday": int(float(get_value("is_holiday", default=0))),
        "is_promotion": int(float(get_value("is_promotion", default=0))),
        "sacks_used": int(float(get_value("sacks_used", default=0))),
        "plates_used": int(float(get_value("plates_used", default=0))),
        "expense_amount": float(get_value(
            "expense_amount", "expense", "cost", default=0.0
        )),
        "is_historical": int(get_value("is_historical", default=0) or 0),
    }
    # Preserve numeric id when present (e.g., rows loaded from MySQL)
    id_val = get_value("id")
    try:
        id_val = int(id_val) if id_val not in (None, "") else None
    except Exception:
        id_val = None
    record["id"] = id_val
    return record


def compute_lag_features(record, history):
    """Compute lag features from previously seen sales history for the same bread type."""
    parsed_date = parse_date(record.get("date"))
    if not parsed_date:
        return {
            "prev_day_sales": 0,
            "prev_3day_avg_sales": 0,
            "prev_7day_avg_sales": 0,
            "prev_14day_avg_sales": 0,
            "prev_14day_sum_sales": 0,
            "prev_same_weekday_avg_sales": 0,
        }

    bread_type = record.get("bread_type", "Pandesal")
    bread_history = history.get(bread_type, {})

    def get_sales_for_day(day_delta):
        day = parsed_date - timedelta(days=day_delta)
        return int(bread_history.get(day.isoformat(), 0))

    prev_day_sales = get_sales_for_day(1)
    prev_3day_sales = [get_sales_for_day(i) for i in range(1, 4)]
    prev_7day_sales = [get_sales_for_day(i) for i in range(1, 8)]
    same_weekday_sales = [get_sales_for_day(7 * i) for i in range(1, 5)]

    prev_14day_sales = [get_sales_for_day(i) for i in range(1, 15)]
    prev_14day_avg = int(round(sum(prev_14day_sales) / len(prev_14day_sales))) if prev_14day_sales else 0
    prev_14day_sum = sum(prev_14day_sales)
    same_weekday_sales = [get_sales_for_day(7 * i) for i in range(1, 5)]
    same_weekday_avg = int(round(sum(same_weekday_sales) / len(same_weekday_sales))) if same_weekday_sales else 0

    return {
        "prev_day_sales": prev_day_sales,
        "prev_3day_avg_sales": int(round(sum(prev_3day_sales) / len(prev_3day_sales))) if prev_3day_sales else 0,
        "prev_7day_avg_sales": int(round(sum(prev_7day_sales) / len(prev_7day_sales))) if prev_7day_sales else 0,
        "prev_14day_avg_sales": prev_14day_avg,
        "prev_14day_sum_sales": prev_14day_sum,
        "prev_same_weekday_avg_sales": same_weekday_avg,
    }


def build_feature_vector(record):
    """Convert a sales record into numeric features for the ML model."""
    bread_type = record.get("bread_type", "Pandesal")
    features = [1 if bread_type == bread else 0 for bread in BREAD_TYPES]
    parsed_date = parse_date(record.get("date"))
    weekday = parsed_date.weekday() if parsed_date else 0
    month = parsed_date.month if parsed_date else 1
    day_of_month = parsed_date.day if parsed_date else 1
    week_of_year = parsed_date.isocalendar()[1] if parsed_date else 1
    day_of_year = parsed_date.timetuple().tm_yday if parsed_date else 1
    is_weekend = 1 if weekday >= 5 else 0
    date_ordinal = parsed_date.toordinal() if parsed_date else 0
    features.extend([
        record.get("price_per_product", 0.0),
        record.get("temperature", 30.0),
        record.get("is_holiday", 0),
        record.get("is_promotion", 0),
        record.get("sacks_used", 0),
        record.get("plates_used", 0),
        weekday,
        is_weekend,
        month,
        day_of_month,
        week_of_year,
        day_of_year,
        date_ordinal,
        record.get("prev_day_sales", 0),
        record.get("prev_3day_avg_sales", 0),
        record.get("prev_7day_avg_sales", 0),
        record.get("prev_14day_avg_sales", 0),
        record.get("prev_14day_sum_sales", 0),
        record.get("prev_same_weekday_avg_sales", 0),
    ])
    return features


def prepare_training_data(sales):
    sorted_sales = sorted(
        (record for record in sales if record.get("date")),
        key=lambda r: (parse_date(r["date"]) or date.min, r.get("bread_type", ""))
    )
    history = {}
    X = []
    y = []
    for record in sorted_sales:
        lags = compute_lag_features(record, history)
        record_with_lags = {**record, **lags}
        X.append(build_feature_vector(record_with_lags))
        y.append(record["actual_qty_sold"])
        bread_type = record_with_lags.get("bread_type", "Pandesal")
        history.setdefault(bread_type, {})[record_with_lags["date"]] = record_with_lags["actual_qty_sold"]
    return np.array(X), np.array(y)


def calculate_accuracy_score(actual, predicted):
    """Return a stable accuracy percentage using a robust median relative error."""
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    if actual_array.size == 0 or predicted_array.size != actual_array.size:
        return 0

    error_ratio = np.abs(actual_array - predicted_array) / np.maximum(np.abs(actual_array), 1.0)
    error_ratio = np.clip(error_ratio, 0.0, 1.0)
    robust_error = float(np.median(error_ratio)) if error_ratio.size else 0.0
    accuracy = 100 - (robust_error * 100)
    return int(round(max(0, min(100, accuracy))))


def build_rolling_baseline_features(record, history, bread_type):
    """Create a small rolling-history feature set for fallback heuristic predictions."""
    parsed_date = parse_date(record.get("date"))
    if not parsed_date:
        return {}

    bread_history = history.get(bread_type, {})
    recent_window = []
    for offset in range(1, 31):
        day = parsed_date - timedelta(days=offset)
        recent_window.append(int(bread_history.get(day.isoformat(), 0)))

    recent_avg = int(round(sum(recent_window) / len(recent_window))) if recent_window else 0
    recent_sum = sum(recent_window)
    if recent_window:
        recent_trend = recent_window[0] - recent_window[-1]
    else:
        recent_trend = 0

    return {
        "rolling_avg_30": recent_avg,
        "rolling_sum_30": recent_sum,
        "rolling_trend_30": recent_trend,
    }


def train_model(sales):
    """Train a regression model on historical sales data and save metrics."""
    global MODEL
    X, y = prepare_training_data(sales)
    if not X.size or not y.size:
        MODEL = None
        return {"r2": 0.0, "mae": 0.0, "accuracy": 0, "trainingCount": 0, "testCount": 0}

    # Use log1p transformation on the target to improve stability
    y_log = np.log1p(y.astype(np.float64))

    try:
        model = HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_depth=6,
            max_iter=400,
            random_state=42,
        )

        if len(y) >= 30:
            split_index = max(int(len(y) * 0.8), 1)
            X_train, X_test = X[:split_index], X[split_index:]
            y_train, y_test = y_log[:split_index], y_log[split_index:]
        else:
            X_train, X_test, y_train, y_test = X, X, y_log, y_log

        try:
            model.fit(X_train, y_train)
        except TypeError:
            model.fit(X_train, y_train)

        y_pred_log = model.predict(X_test)
        predictions = np.maximum(0.0, np.expm1(y_pred_log))
        y_test_actual = np.expm1(y_test) if len(y_test) > 0 else np.array([])

        mae = mean_absolute_error(y_test_actual, predictions) if len(y_test_actual) > 0 else 0.0
        r2 = r2_score(y_test_actual, predictions) if len(y_test_actual) > 1 else 0.0
        accuracy = calculate_accuracy_score(y_test_actual, predictions)

        MODEL = model
        return {
            "r2": r2,
            "mae": mae,
            "accuracy": accuracy,
            "trainingCount": len(y_train),
            "testCount": len(y_test),
        }
    except Exception as err:
        print(f"ML training error ({'XGBoost' if USE_XGBOOST else 'LinearRegression'}):", err)
        MODEL = None
        return {"r2": 0.0, "mae": 0.0, "accuracy": 0, "trainingCount": 0, "testCount": 0}


def parse_csv_upload(file_storage):
    """Parse an uploaded CSV or Excel file and normalize rows into sales records."""
    if not file_storage or file_storage.filename == "":
        return []

    filename = (file_storage.filename or "").lower()
    try:
        file_storage.stream.seek(0)

        def is_blank_upload_value(value):
            if value is None:
                return True
            if isinstance(value, str):
                return not value.strip()
            if isinstance(value, (list, tuple)):
                return all(is_blank_upload_value(item) for item in value)
            try:
                return bool(pd.isna(value))
            except (TypeError, ValueError):
                return False

        if filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_storage.stream)
            rows = [
                (row_number, row)
                for row_number, row in enumerate(df.to_dict(orient="records"), start=2)
                if any(not is_blank_upload_value(value) for value in row.values())
            ]
        else:
            header = file_storage.stream.readline().decode("utf-8-sig", errors="replace")
            file_storage.stream.seek(0)
            delimiter = "\t" if header.count("\t") > header.count(",") else ","
            text_stream = io.TextIOWrapper(file_storage.stream, encoding="utf-8-sig", errors="replace")
            reader = csv.DictReader(text_stream, delimiter=delimiter)
            rows = []
            for row in reader:
                row_number = reader.line_num
                if row and any(
                    not is_blank_upload_value(value)
                    for value in row.values()
                ):
                    rows.append((row_number, row))

        date_fields = ("date", "production_date", "sale_date", "baked_day")

        def parse_upload_date(value):
            if value is None or pd.isna(value):
                return None
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            date_text = str(value).strip()
            if not date_text:
                return None
            for date_format in (
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%d/%m/%Y",
                "%b %d, %Y",
            ):
                try:
                    return datetime.strptime(date_text, date_format).date()
                except ValueError:
                    continue
            return None

        normalized = []
        for row_number, source_row in rows:
            normalized_headers = {
                "".join(character for character in str(key).casefold() if character.isalnum()): key
                for key in source_row
            }
            selected_date = None
            raw_date = None
            for field in date_fields:
                normalized_field = "".join(
                    character for character in field.casefold() if character.isalnum()
                )
                source_key = normalized_headers.get(normalized_field)
                if source_key is None:
                    continue
                candidate = source_row[source_key]
                if raw_date is None:
                    raw_date = candidate
                selected_date = parse_upload_date(candidate)
                if selected_date:
                    break
            if not selected_date:
                raise ValueError(
                    f"Record {row_number} has a missing or invalid date: {raw_date!r}."
                )

            row = dict(source_row)
            row["date"] = selected_date.isoformat()
            normalized.append(normalize_record(row))

        # Aggregate by (date, bread_type): sum quantities and expenses, compute weighted price
        agg = {}
        for rec in normalized:
            key = (rec.get("date"), rec.get("bread_type"))
            qty = int(rec.get("actual_qty_sold") or 0)
            price = float(rec.get("price_per_product") or 0.0)
            produced = int(rec.get("products_produced") or 0)
            waste = int(rec.get("waste_returns") or 0)
            expense = float(rec.get("expense_amount") or 0.0)

            if key not in agg:
                a = dict(rec)
                a["products_produced"] = produced
                a["actual_qty_sold"] = qty
                a["waste_returns"] = waste
                a["expense_amount"] = expense
                a["_revenue"] = qty * price
                agg[key] = a
            else:
                a = agg[key]
                a["products_produced"] = int(a.get("products_produced", 0)) + produced
                a["actual_qty_sold"] = int(a.get("actual_qty_sold", 0)) + qty
                a["waste_returns"] = int(a.get("waste_returns", 0)) + waste
                a["expense_amount"] = float(a.get("expense_amount", 0.0)) + expense
                a["_revenue"] = float(a.get("_revenue", 0.0)) + (qty * price)

        records = []
        for (d, b), a in agg.items():
            total_sold = int(a.get("actual_qty_sold", 0))
            revenue = float(a.pop("_revenue", 0.0))
            if total_sold > 0:
                a["price_per_product"] = round(revenue / total_sold, 2)
            else:
                a["price_per_product"] = float(a.get("price_per_product") or 0.0)
            records.append(a)

        return records
    except ValueError:
        raise
    except Exception:
        logger.exception("Could not parse uploaded file %r", file_storage.filename)
        return None


def get_db_connection():
    """Return a new connection to the MySQL database."""
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        autocommit=False,
    )


def load_access_code_hash():
    """Return the saved bakery access-code hash, or None if it has not been set."""
    if not USE_MYSQL:
        return None

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT code_hash FROM access_settings WHERE id = %s", (1,))
        row = cursor.fetchone()
        return row[0] if row else None
    except mysql.connector.Error:
        logger.exception("Could not load the saved bakery access code")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def verify_access_code(candidate):
    """Verify against the saved hash, falling back until an access code is saved."""
    code_hash = load_access_code_hash()
    if code_hash is not None:
        return check_password_hash(code_hash, candidate)
    return candidate == LOGIN_PASSWORD


def save_access_code_hash(code_hash):
    """Insert or replace the single saved bakery access-code hash."""
    if not USE_MYSQL:
        raise RuntimeError("Changing the access code requires MySQL.")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO access_settings (id, code_hash) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE code_hash = VALUES(code_hash)",
            (1, code_hash),
        )
        conn.commit()
    except mysql.connector.Error:
        if conn:
            conn.rollback()
        logger.exception("Could not save the bakery access code")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def load_custom_bread_types():
    """Load user-added bread types and their default prices."""
    if not USE_MYSQL:
        return {}

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT bread_type, default_price FROM bread_type_catalog ORDER BY bread_type")
        return {row[0]: float(row[1]) for row in cursor.fetchall()}
    except mysql.connector.Error as err:
        logger.error("Could not load custom bread types: %s", err)
        return {}
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def load_active_bread_types():
    """Load products available for new sales, prediction, and edit entries."""
    if not USE_MYSQL:
        inactive_names = {name.casefold() for name in INACTIVE_BREAD_TYPES}
        return [
            bread for bread in BREAD_TYPES
            if bread.casefold() not in inactive_names
        ]

    return [
        row["bread_type"] for row in load_active_product_rows()
    ]


def load_active_product_rows():
    """Return active product records for selectors and management controls."""
    if not USE_MYSQL:
        inactive_names = {name.casefold() for name in INACTIVE_BREAD_TYPES}
        return [
            {
                "id": index + 1,
                "bread_type": bread,
                "default_price": BREAD_PRICES.get(bread, 4.5),
            }
            for index, bread in enumerate(BREAD_TYPES)
            if bread.casefold() not in inactive_names
        ]

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, bread_type, default_price FROM bread_type_catalog "
            "WHERE is_active = 1 ORDER BY bread_type"
        )
        return [
            {
                "id": row["id"],
                "bread_type": row["bread_type"],
                "default_price": float(row["default_price"]),
            }
            for row in cursor.fetchall()
        ]
    except mysql.connector.Error:
        logger.exception("Could not load active products")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def persist_custom_bread_type(bread_type, default_price):
    """Insert a new product or reactivate a previously deleted product."""
    if not USE_MYSQL:
        return False, "Persistent product storage requires MySQL."

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, is_active FROM bread_type_catalog "
            "WHERE LOWER(bread_type) = LOWER(%s) LIMIT 1 FOR UPDATE",
            (bread_type,),
        )
        existing = cursor.fetchone()
        if existing:
            if existing[1]:
                conn.rollback()
                return False, "A product with that name already exists.", None, False
            cursor.execute(
                "UPDATE bread_type_catalog SET is_active = 1, default_price = %s WHERE id = %s",
                (default_price, existing[0]),
            )
            product_id = existing[0]
            reactivated = True
        else:
            cursor.execute(
                "INSERT INTO bread_type_catalog (bread_type, default_price, is_active) "
                "VALUES (%s, %s, 1)",
                (bread_type, default_price),
            )
            product_id = cursor.lastrowid
            reactivated = False
        conn.commit()
        return True, None, product_id, reactivated
    except mysql.connector.Error as err:
        if conn:
            conn.rollback()
        logger.error("Could not save custom bread type: %s", err)
        return False, "Could not save this product. Please try again.", None, False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def ensure_database():
    """Create the configured MySQL database if it does not yet exist."""
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        print("MySQL ensure_database error:", err)


def ensure_monthly_expenses_table_schema():
    """Ensure older MySQL databases have the scope columns needed by the expense forms."""
    if not USE_MYSQL:
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW COLUMNS FROM monthly_expenses")
        columns = {row[0].lower() for row in cursor.fetchall()}
        if "scope_type" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN scope_type ENUM('daily','weekly','monthly') NOT NULL DEFAULT 'monthly'")
        if "period_key" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN period_key VARCHAR(20) NOT NULL DEFAULT 'monthly'")
        if "expense_month" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN expense_month DATE NULL")
        if "note" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN note VARCHAR(255) NULL")
        if "created_at" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        try:
            cursor.execute("ALTER TABLE monthly_expenses ADD UNIQUE INDEX unique_scope_category (scope_type, period_key, category)")
        except mysql.connector.Error:
            pass
        conn.commit()
        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        print("MySQL monthly expenses schema ensure error:", err)


def ensure_ingredient_active_column(cursor):
    cursor.execute("SHOW COLUMNS FROM ingredients LIKE 'is_active'")
    if cursor.fetchone() is None:
        cursor.execute(
            "ALTER TABLE ingredients ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1"
        )


def init_db():
    if not USE_MYSQL:
        return
    # Ensure the database exists first
    ensure_database()

    # First connection: core tables and repairs
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create sales_data table with unique constraint on (date, bread_type)
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
            ) ENGINE=InnoDB
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bread_type_catalog (
                id INT AUTO_INCREMENT PRIMARY KEY,
                bread_type VARCHAR(100) NOT NULL UNIQUE,
                default_price DECIMAL(10,2) NOT NULL DEFAULT 4.50,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
        """)
        cursor.execute("SHOW COLUMNS FROM sales_data LIKE 'is_historical'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE sales_data ADD COLUMN is_historical TINYINT(1) NOT NULL DEFAULT 0"
            )
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
        cursor.executemany(
            "INSERT IGNORE INTO bread_type_catalog (bread_type, default_price, is_active) "
            "VALUES (%s, %s, 1)",
            [(bread_type, BREAD_PRICES.get(bread_type, 4.5)) for bread_type in BREAD_TYPES],
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
            ) ENGINE=InnoDB
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales_batch_allocations (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                sale_id INT NOT NULL,
                batch_id BIGINT NOT NULL,
                allocated_quantity INT NOT NULL,
                UNIQUE KEY uk_sales_batch_allocation (sale_id, batch_id),
                INDEX idx_sales_batch_batch (batch_id)
            ) ENGINE=InnoDB
        """)

        # Repair older imported schemas that may have been created without
        # AUTO_INCREMENT on the primary key. This is required for the app's
        # save path to work against a cloud database imported from the SQL dump.
        try:
            cursor.execute("SHOW INDEX FROM sales_data WHERE Key_name = 'PRIMARY'")
            primary_key_exists = cursor.fetchone() is not None
            if not primary_key_exists:
                cursor.execute("ALTER TABLE sales_data ADD PRIMARY KEY (id)")
        except mysql.connector.Error as err:
            logger.debug("sales_data primary key repair warning: %s", err)

        try:
            cursor.execute("ALTER TABLE sales_data MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT")
        except mysql.connector.Error as err:
            logger.debug("sales_data id repair warning: %s", err)

        for column_name, definition in (
            ("bad_waste", "INT NOT NULL DEFAULT 0"),
            ("good_waste", "INT NOT NULL DEFAULT 0"),
        ):
            try:
                cursor.execute(f"SHOW COLUMNS FROM sales_data LIKE '{column_name}'")
                if cursor.fetchone() is None:
                    cursor.execute(f"ALTER TABLE sales_data ADD COLUMN {column_name} {definition}")
            except mysql.connector.Error as err:
                logger.debug("sales_data column repair warning (%s): %s", column_name, err)

        # Add unique constraint to existing table if not already present
        try:
            cursor.execute("""
                ALTER TABLE sales_data ADD CONSTRAINT uk_date_bread_type UNIQUE (date, bread_type)
            """)
        except mysql.connector.Error as err:
            # Constraint already exists or other error, ignore unless unexpected
            if 'Duplicate key name' not in str(err):
                logger.debug("ALTER TABLE warning (may be OK): %s", err)

        # Create users table
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
            ) ENGINE=InnoDB
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS access_settings (
                id INT PRIMARY KEY,
                code_hash VARCHAR(255) NOT NULL
            ) ENGINE=InnoDB
        """)

        # Create model_performance table
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
            ) ENGINE=InnoDB
        """)

        # Create predictions table
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
                INDEX idx_date_predicted (date_predicted)
            ) ENGINE=InnoDB
        """)

        # Create production_plan table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS production_plan (
                id INT AUTO_INCREMENT PRIMARY KEY,
                plan_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                bread_type VARCHAR(100) NOT NULL,
                day_date DATE NOT NULL,
                planned_quantity INT NOT NULL DEFAULT 0,
                actual_quantity INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_plan_date (plan_date),
                INDEX idx_bread_type (bread_type),
                INDEX idx_day_date (day_date),
                UNIQUE KEY unique_plan_entry (plan_date, bread_type, day_date)
            ) ENGINE=InnoDB
        """)
        try:
            cursor.execute("ALTER TABLE production_plan ADD COLUMN actual_quantity INT NOT NULL DEFAULT 0")
        except mysql.connector.Error:
            pass
        try:
            cursor.execute("ALTER TABLE production_plan ADD UNIQUE INDEX unique_plan_entry (plan_date, bread_type, day_date)")
        except mysql.connector.Error:
            pass

        # Create monthly expenses table if it does not exist yet
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
            ) ENGINE=InnoDB
        """)
        conn.commit()
    except mysql.connector.Error as err:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("MySQL init error (core tables): %s", err)
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    # Ensure monthly expenses schema (separate connection inside function)
    ensure_monthly_expenses_table_schema()

    # Second connection: ingredients, transactions, and raw uploads
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create ingredients inventory table
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
            ) ENGINE=InnoDB
        """)
        ensure_ingredient_active_column(cursor)

        # Create ingredient transactions log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingredient_transactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                ingredient_id INT NOT NULL,
                change_amount DECIMAL(14,4) NOT NULL,
                transaction_type ENUM('in','out') NOT NULL,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ingredient_txn (ingredient_id),
                CONSTRAINT fk_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
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
            WHERE h.id IS NULL AND i.is_active = 1
        """)

        try:
            cursor.execute("SHOW COLUMNS FROM sales_data LIKE 'expense_amount'")
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE sales_data ADD COLUMN expense_amount DECIMAL(10,2) NOT NULL DEFAULT 0.0")
        except mysql.connector.Error as err:
            logger.debug("sales_data expense_amount repair warning: %s", err)

        # Create a raw uploads table to preserve original CSV uploads (no unique constraint)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_sales_uploads (
                id INT AUTO_INCREMENT PRIMARY KEY,
                date DATE,
                bread_type VARCHAR(200),
                products_produced INT DEFAULT 0,
                actual_qty_sold INT DEFAULT 0,
                waste_returns INT DEFAULT 0,
                price_per_product DECIMAL(10,2) DEFAULT 0.0,
                temperature DECIMAL(5,2) DEFAULT 30.0,
                is_holiday TINYINT(1) DEFAULT 0,
                is_promotion TINYINT(1) DEFAULT 0,
                sacks_used INT DEFAULT 0,
                plates_used INT DEFAULT 0,
                expense_amount DECIMAL(10,2) DEFAULT 0.0,
                is_historical TINYINT(1) NOT NULL DEFAULT 0,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_raw_uploaded_at (uploaded_at)
            ) ENGINE=InnoDB
        """)
        cursor.execute("SHOW COLUMNS FROM raw_sales_uploads LIKE 'is_historical'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE raw_sales_uploads ADD COLUMN is_historical TINYINT(1) NOT NULL DEFAULT 0"
            )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales_data_recovery_state (
                id TINYINT PRIMARY KEY,
                raw_upload_id_cutoff BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
        """)
        conn.commit()
        logger.info("Database tables initialized successfully")
    except mysql.connector.Error as err:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("MySQL init error (ingredients/raw uploads): %s", err)
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def fetch_sales_from_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, date, bread_type, products_produced, actual_qty_sold,
                   waste_returns, price_per_product, temperature, is_historical,
                   is_holiday, is_promotion, sacks_used, plates_used, expense_amount
            FROM sales_data
            ORDER BY date, bread_type
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [normalize_record(row) for row in rows]
    except mysql.connector.Error as err:
        print("MySQL fetch error:", err)
        return None


def parse_month(value):
    if not value:
        return None
    try:
        year, month = map(int, value.split("-"))
        return date(year, month, 1)
    except Exception:
        return None


def parse_week(value):
    if not value:
        return None
    try:
        year, week = value.split("-W")
        return int(year), int(week)
    except Exception:
        return None


def get_expense_scope_context(scope_type="monthly", selected_date=None, selected_week=None, selected_month=None):
    """Build a context object for the selected expense scope."""
    scope_type = (scope_type or "monthly").strip().lower()
    if scope_type == "daily":
        expense_date = parse_date(selected_date) or date.today()
        return {
            "scope_type": "daily",
            "period_key": expense_date.strftime("%Y-%m-%d"),
            "expense_date": expense_date,
            "expense_week": None,
            "expense_month": None,
        }
    if scope_type == "weekly":
        year, week = parse_week(selected_week) or (date.today().isocalendar().year, date.today().isocalendar().week)
        return {
            "scope_type": "weekly",
            "period_key": f"{year}-W{week}",
            "expense_date": None,
            "expense_week": week,
            "expense_month": None,
        }
    expense_month = parse_month(selected_month) or date.today().replace(day=1)
    return {
        "scope_type": "monthly",
        "period_key": expense_month.strftime("%Y-%m"),
        "expense_date": None,
        "expense_week": None,
        "expense_month": expense_month,
    }


def save_monthly_expense(month_value, category, amount, note="", scope_type="monthly", period_key=None):
    ensure_monthly_expenses_table_schema()
    scope_type = (scope_type or "monthly").strip().lower()
    if scope_type == "daily":
        print("month_value:", repr(month_value))
        print("period_key:", repr(period_key))

        expense_date = parse_date(period_key or month_value)

        print("parsed expense_date:", repr(expense_date))

        if expense_date is None:
            expense_date = date.today()

        print("final expense_date:", repr(expense_date))

        period_value = expense_date.strftime("%Y-%m-%d")
        expense_month_value = expense_date

        print("expense_month_value:", repr(expense_month_value))
    elif scope_type == "weekly":
        week_value = period_key or month_value
        if isinstance(week_value, str) and "-W" in week_value:
            period_value = week_value
        else:
            period_value = f"{date.today().year}-W{date.today().isocalendar().week:02d}"
        # For weekly, set expense_month_value to the ISO week start (Monday)
        try:
            y, w = parse_week(week_value) or (date.today().isocalendar().year, date.today().isocalendar().week)
            expense_month_value = date.fromisocalendar(int(y), int(w), 1)
        except Exception:
            expense_month_value = None
    else:
        expense_month_value = parse_month(period_key or month_value) or date.today().replace(day=1)
        period_value = expense_month_value.strftime("%Y-%m")

    if not USE_MYSQL:
        bucket = MONTHLY_EXPENSES.setdefault((scope_type, period_value), {})
        bucket[category] = {"amount": float(amount), "note": note or ""}
        return True

    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            # Diagnostic: print current database for hosted checks
            try:
                cursor.execute("SELECT DATABASE()")
                print("DB:", cursor.fetchone())
                # Also fetch and print the CREATE TABLE output for diagnostics on Render
                try:
                    cursor.execute("SHOW CREATE TABLE monthly_expenses")
                    table = cursor.fetchone()
                    print("TABLE FROM RENDER:")
                    print(table)
                except Exception:
                    # If SHOW CREATE TABLE fails, continue without blocking saves
                    pass
            except Exception:
                pass

            params = (scope_type, period_value, expense_month_value, category, float(amount), note)
            print("Saving monthly expense, params:", repr(params))
            cursor.execute(
                """
                INSERT INTO monthly_expenses (scope_type, period_key, expense_month, category, amount, note)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE amount = VALUES(amount), note = VALUES(note)
                """,
                params,
            )
            print("Rows affected:", getattr(cursor, 'rowcount', None))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except mysql.connector.Error as err:
            # Return error message so caller can show it
            print("MySQL monthly expense save error:", err)
            return False


def fetch_monthly_expenses(month_value, scope_type="monthly", period_key=None):
    ensure_monthly_expenses_table_schema()
    scope_type = (scope_type or "monthly").strip().lower()
    if scope_type == "daily":
        expense_date = parse_date(period_key or month_value) or date.today()
        period_value = expense_date.strftime("%Y-%m-%d")
    elif scope_type == "weekly":
        period_value = period_key or month_value
    else:
        expense_month_value = parse_month(period_key or month_value) or date.today().replace(day=1)
        period_value = expense_month_value.strftime("%Y-%m")

    if not USE_MYSQL:
        bucket = MONTHLY_EXPENSES.get((scope_type, period_value), {})
        return {
            category: {"amount": float(entry.get("amount", 0.0)), "note": entry.get("note", "")}
            for category, entry in bucket.items()
        }

    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT category, amount, note FROM monthly_expenses WHERE scope_type = %s AND period_key = %s",
                (scope_type, period_value)
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return {row['category']: {'amount': float(row['amount']), 'note': row['note'] or ''} for row in rows}
        except mysql.connector.Error as err:
            print("MySQL monthly expense fetch error:", err)
    return {}


def fetch_monthly_expense_total(month_value, scope_type="monthly", period_key=None):
    expenses = fetch_monthly_expenses(month_value, scope_type=scope_type, period_key=period_key)
    return sum(item['amount'] for item in expenses.values())


def build_expense_history_rows(entries, limit=None):
    """Return expense rows sorted newest-first for the history UI and tests."""
    normalized = []
    for entry in entries or []:
        created_at_raw = entry.get("created_at")
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00")) if created_at_raw else datetime.min
        except ValueError:
            created_at = datetime.min

        normalized.append({
            "scope_type": entry.get("scope_type", "monthly"),
            "period_key": entry.get("period_key", ""),
            "category": entry.get("category", ""),
            "amount": float(entry.get("amount", 0.0)),
            "note": entry.get("note", ""),
            "created_at": created_at,
        })

    normalized.sort(key=lambda item: item["created_at"], reverse=True)
    if limit is not None:
        normalized = normalized[:limit]

    return [
        {
            "scope_type": item["scope_type"],
            "period_key": item["period_key"],
            "category": item["category"],
            "amount": item["amount"],
            "note": item["note"],
            "created_at": item["created_at"].isoformat(),
        }
        for item in normalized
    ]


def fetch_all_expenses_total():
    """Fetch total expenses for all months."""
    if not USE_MYSQL:
        return sum(
            float(entry.get("amount", 0.0))
            for bucket in MONTHLY_EXPENSES.values()
            for entry in bucket.values()
        )

    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(amount) FROM monthly_expenses")
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            if result and result[0] is not None:
                return float(result[0])
            return 0.0
        except mysql.connector.Error as err:
            print("MySQL fetch all expenses error:", err)
            return 0.0
    else:
        # For in-memory, we don't have monthly expenses stored
        return 0.0


def get_future_demand_forecast(sales_data, days_ahead=30):
    """Generate future demand forecast for the next N days."""
    from datetime import date, timedelta
    
    today = date.today()
    forecast_data = []
    
    for i in range(days_ahead):
        target_date = today + timedelta(days=i+1)  # Start from tomorrow
        
        # Get day of week and determine if it's a weekend
        is_weekend = target_date.weekday() >= 5
        
        # Estimate temperature (rough seasonal approximation)
        # This is a simple approximation - could be improved with actual weather data
        month = target_date.month
        if month in [12, 1, 2]:  # Winter
            base_temp = 25
        elif month in [3, 4, 5]:  # Spring
            base_temp = 28
        elif month in [6, 7, 8]:  # Summer
            base_temp = 32
        else:  # Fall
            base_temp = 30
        
        temperature = base_temp + random.randint(-3, 3)
        
        # Check if it's a holiday (simplified - you might want to add actual holiday calendar)
        is_holiday = False  # Could be enhanced with holiday API
        
        # Predict demand for all bread types combined
        results = predict_demand("All", target_date, temperature, is_holiday, False, sales_data)
        total_predicted = sum(item["predictedDemand"] for item in results)
        
        forecast_data.append({
            "date": target_date.strftime("%Y-%m-%d"),
            "date_label": target_date.strftime("%b %d"),
            "predicted_demand": total_predicted,
            "temperature": temperature,
            "is_weekend": is_weekend,
            "is_holiday": is_holiday
        })
    
    return forecast_data


def get_monthly_analytics(sales_data, months=12):
    """Generate monthly analytics for sales, expenses, and profit."""
    from collections import defaultdict
    import calendar
    
    # Get current date
    today = date.today()
    
    # Start from January 2026
    start_year = 2026
    start_month = 1
    current_date = today.replace(day=1)
    
    # Calculate number of months from Jan 2026 to current month
    current_year = today.year
    current_month = today.month
    months_diff = (current_year - start_year) * 12 + (current_month - start_month)
    actual_months = min(months_diff + 1, months)  # Include current month
    
    # Initialize monthly data
    monthly_data = []
    sales_with_waste = sales_rows_with_batch_waste(sales_data)
    
    for i in range(actual_months):
        # Calculate target year and month by properly incrementing months
        target_month = start_month + i
        target_year = start_year + (target_month - 1) // 12
        target_month = ((target_month - 1) % 12) + 1
        
        target_date = date(target_year, target_month, 1)
        
        # Don't go beyond current month
        if target_date > current_date:
            break
            
        month_key = target_date.strftime("%Y-%m")
        month_name = target_date.strftime("%b %Y")
        
        # Filter sales for this month
        month_sales = [row for row in sales_with_waste if str(row["date"]).startswith(month_key)]
        
        # Calculate sales and revenue for this month
        monthly_sales = sum(row["actual_qty_sold"] for row in month_sales)
        monthly_revenue = sum(row["actual_qty_sold"] * row["price_per_product"] for row in month_sales)
        
        # Get expenses for this month
        monthly_expenses = fetch_monthly_expense_total(month_key)
        waste_cost = sum(row["waste_cost"] for row in month_sales)
        
        # Calculate profit
        monthly_profit = monthly_revenue - monthly_expenses
        
        monthly_data.append({
            "month": month_name,
            "month_key": month_key,
            "sales": monthly_sales,
            "revenue": monthly_revenue,
            "expenses": monthly_expenses,
            "waste_cost": waste_cost,
            "profit": monthly_profit
        })
    
    return monthly_data


def save_cutoff_sales_records(records):
    """Save sold-only entries for the new outcome policy without rebuilding old batches."""
    normalized = []
    for row_number, source in enumerate(records or [], start=1):
        target_date = parse_date(source.get("date"))
        bread_type = str(source.get("bread_type") or "").strip()
        if not target_date or not bread_type or len(bread_type) > 100:
            return False, f"Record {row_number} has an invalid date or bread type."
        if target_date < PRODUCTION_OUTCOME_CUTOFF:
            return False, "Sales before 2026-10-11 are read-only."
        try:
            sold_value = source.get("actual_qty_sold")
            if sold_value in (None, ""):
                sold = 0
            elif isinstance(sold_value, int) and not isinstance(sold_value, bool):
                sold = sold_value
            elif str(sold_value).strip().isdecimal():
                sold = int(str(sold_value).strip())
            else:
                raise ValueError("Sold quantity must be a whole number.")
            price = float(source.get("price_per_product") or 0)
        except (TypeError, ValueError):
            return False, f"Record {row_number} has an invalid sold quantity or price."
        if sold < 0 or price < 0:
            return False, f"Record {row_number} has a negative sold quantity or price."
        if sold > 2147483647:
            return False, f"Record {row_number} sold quantity exceeds the supported limit."
        normalized.append({
            "date": target_date.isoformat(),
            "bread_type": bread_type,
            "actual_qty_sold": sold,
            "price_per_product": price,
        })

    if not USE_MYSQL:
        rows_by_key = {
            (str(row.get("date") or "")[:10], row.get("bread_type")): row
            for row in SALES_DATA
        }
        for record in normalized:
            existing = rows_by_key.get((record["date"], record["bread_type"]), {})
            produced = int(existing.get("products_produced") or 0)
            if produced <= 0:
                return False, (
                    f'Enter actual production first for {record["bread_type"]} '
                    f'on {record["date"]}.'
                )
            if record["actual_qty_sold"] > produced:
                return False, (
                    f'Sold quantity for {record["bread_type"]} on {record["date"]} '
                    f'cannot exceed actual production ({produced}).'
                )
        for record in normalized:
            existing = rows_by_key.get((record["date"], record["bread_type"]))
            if existing is None:
                existing = {
                    "date": record["date"],
                    "bread_type": record["bread_type"],
                    "products_produced": 0,
                    "actual_qty_sold": 0,
                    "price_per_product": record["price_per_product"],
                    "waste_returns": 0,
                }
                SALES_DATA.append(existing)
                rows_by_key[(record["date"], record["bread_type"])] = existing
            existing["actual_qty_sold"] = record["actual_qty_sold"]
            existing["price_per_product"] = record["price_per_product"]
        return True, None

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        for record in normalized:
            cursor.execute(
                """SELECT products_produced FROM sales_data
                   WHERE date = %s AND bread_type = %s FOR UPDATE""",
                (record["date"], record["bread_type"]),
            )
            existing = cursor.fetchone()
            produced = int(existing["products_produced"] or 0) if existing else 0
            if produced <= 0:
                conn.rollback()
                return False, (
                    f'Enter actual production first for {record["bread_type"]} '
                    f'on {record["date"]}.'
                )
            if record["actual_qty_sold"] > produced:
                conn.rollback()
                return False, (
                    f'Sold quantity for {record["bread_type"]} on {record["date"]} '
                    f'cannot exceed actual production ({produced}).'
                )
            cursor.execute(
                """INSERT INTO sales_data (
                       date, bread_type, products_produced, actual_qty_sold,
                       waste_returns, price_per_product, temperature,
                       is_holiday, is_promotion, sacks_used, plates_used,
                       expense_amount, is_historical
                   ) VALUES (%s, %s, 0, %s, 0, %s, 30, 0, 0, 0, 0, 0, 0)
                   ON DUPLICATE KEY UPDATE
                       actual_qty_sold = VALUES(actual_qty_sold),
                       price_per_product = VALUES(price_per_product),
                       is_historical = 0""",
                (
                    record["date"],
                    record["bread_type"],
                    record["actual_qty_sold"],
                    record["price_per_product"],
                ),
            )
        conn.commit()
        return True, None
    except mysql.connector.Error:
        if conn:
            conn.rollback()
        logger.exception("Could not save sold quantities")
        return False, "Could not save these sales records. Please try again."
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _fetch_shelf_life_days(cursor):
    cursor.execute(
        "SELECT id, bread_type, shelf_life_days, default_price FROM bread_type_catalog"
    )
    return {
        row["bread_type"]: {
            "days": max(1, int(row["shelf_life_days"] or 5)),
            "price": float(row["default_price"] or 0),
            "id": row["id"],
        }
        for row in cursor.fetchall()
    }


def _rebuild_production_batches(cursor, bread_type):
    """Recalculate one product's batches and FIFO allocations in the caller's transaction."""
    cursor.execute(
        """SELECT id, date, bread_type, products_produced, actual_qty_sold,
                  is_historical,
                  price_per_product
           FROM sales_data WHERE bread_type = %s ORDER BY date, id""",
        (bread_type,),
    )
    rows = cursor.fetchall()
    products = _fetch_shelf_life_days(cursor)
    product = products.get(bread_type, {"days": 5, "price": 0, "id": None})
    inventory = calculate_production_batches(
        rows,
        {bread_type: product["days"]},
        as_of=datetime.now(MANILA_TZ).date(),
    )

    cursor.execute(
        """DELETE FROM sales_batch_allocations
           WHERE batch_id IN (
               SELECT id FROM production_batches WHERE bread_type = %s
           )""",
        (bread_type,),
    )
    cursor.execute("DELETE FROM production_batches WHERE bread_type = %s", (bread_type,))

    batch_ids = {}
    for batch in inventory["batches"]:
        unit_price = batch["unit_price"] or product["price"]
        cursor.execute(
            """INSERT INTO production_batches (
                   product_id, bread_type, production_date, quantity_produced,
                   quantity_sold, leftover_quantity, waste_quantity, expiry_date,
                   unit_price, source_sales_id
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                product["id"],
                bread_type,
                batch["production_date"],
                batch["quantity_produced"],
                batch["quantity_sold"],
                batch["leftover_quantity"],
                batch["waste_quantity"],
                batch["expiry_date"],
                unit_price,
                batch["source_sales_id"],
            ),
        )
        batch_ids[batch["source_index"]] = cursor.lastrowid

    for allocation in inventory["allocations"]:
        batch_id = batch_ids.get(allocation["batch_index"])
        if allocation["sale_id"] and batch_id:
            cursor.execute(
                """INSERT INTO sales_batch_allocations (
                       sale_id, batch_id, allocated_quantity
                   ) VALUES (%s, %s, %s)""",
                (allocation["sale_id"], batch_id, allocation["quantity"]),
            )


def save_historical_sales_records(records):
    """Insert historical sales without validating or rebuilding production stock."""
    if not USE_MYSQL:
        raise RuntimeError("Historical sales upload requires the configured MySQL database.")
    for row_number, source_record in enumerate(records, start=1):
        record_date = parse_date(source_record.get("date"))
        if not record_date:
            raise ValueError(
                f"Record {row_number} has a missing or invalid date."
            )
        if record_date < PRODUCTION_OUTCOME_CUTOFF:
            raise ValueError(
                "Sales records before 2026-10-11 are read-only and cannot be imported."
            )

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        conn.start_transaction()
        imported = 0
        skipped = 0

        for row_number, source_record in enumerate(records, start=1):
            record_date = parse_date(source_record.get("date"))
            bread_type = str(source_record.get("bread_type") or "").strip()
            if not record_date or not bread_type:
                missing = "date" if not record_date else "bread type"
                raise ValueError(
                    f"Record {row_number} has a missing or invalid {missing}."
                )
            if record_date < PRODUCTION_OUTCOME_CUTOFF:
                raise ValueError(
                    "Sales records before 2026-10-11 are read-only and cannot be imported."
                )

            try:
                sold = int(source_record.get("actual_qty_sold") or 0)
                price = float(source_record.get("price_per_product") or 0.0)
                expense = float(source_record.get("expense_amount") or 0.0)
            except (TypeError, ValueError) as err:
                raise ValueError(
                    f"Record {row_number} has an invalid sales quantity, price, or expense."
                ) from err
            if sold < 0 or price < 0 or expense < 0:
                raise ValueError(
                    f"Record {row_number} has a negative sales quantity, price, or expense."
                )

            record = {
                "date": record_date.isoformat(),
                "bread_type": bread_type,
                "products_produced": 0,
                "actual_qty_sold": sold,
                "waste_returns": 0,
                "price_per_product": price,
                "temperature": source_record.get("temperature", 30.0),
                "is_holiday": source_record.get("is_holiday", 0),
                "is_promotion": source_record.get("is_promotion", 0),
                "sacks_used": 0,
                "plates_used": 0,
                "expense_amount": expense,
                "is_historical": 1,
            }

            cursor.execute(
                "SELECT id FROM sales_data WHERE date = %s AND bread_type = %s FOR UPDATE",
                (record["date"], record["bread_type"]),
            )
            if cursor.fetchone():
                skipped += 1
                continue

            cursor.execute(
                """INSERT INTO sales_data (
                       date, bread_type, products_produced, actual_qty_sold,
                       waste_returns, price_per_product, temperature,
                       is_holiday, is_promotion, sacks_used, plates_used,
                       expense_amount, is_historical
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    record["date"],
                    record["bread_type"],
                    record["products_produced"],
                    record["actual_qty_sold"],
                    record["waste_returns"],
                    record["price_per_product"],
                    record["temperature"],
                    record["is_holiday"],
                    record["is_promotion"],
                    record["sacks_used"],
                    record["plates_used"],
                    record["expense_amount"],
                    record["is_historical"],
                ),
            )
            cursor.execute(
                """INSERT INTO raw_sales_uploads (
                       date, bread_type, products_produced, actual_qty_sold,
                       waste_returns, price_per_product, temperature,
                       is_historical, is_holiday, is_promotion, sacks_used,
                       plates_used, expense_amount
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    record["date"],
                    record["bread_type"],
                    record["products_produced"],
                    record["actual_qty_sold"],
                    record["waste_returns"],
                    record["price_per_product"],
                    record["temperature"],
                    record["is_historical"],
                    record["is_holiday"],
                    record["is_promotion"],
                    record["sacks_used"],
                    record["plates_used"],
                    record["expense_amount"],
                ),
            )
            imported += 1

        conn.commit()
        return imported, skipped
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Could not import historical sales transaction")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def save_sales_records(
    records,
    allow_overstock=False,
    raise_db_errors=False,
    raise_validation_errors=False,
):
    """Save sales and refresh affected FIFO batches atomically."""
    if not USE_MYSQL:
        if raise_validation_errors:
            raise ValueError("MySQL is disabled; uploaded records cannot be saved.")
        return False, []

    if any(
        (record_date := parse_date(record.get("date"))) is None
        or record_date < PRODUCTION_OUTCOME_CUTOFF
        for record in records
    ):
        if raise_validation_errors:
            raise ValueError("Sales records before 2026-10-11 are read-only.")
        return False, []

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        bread_types = sorted({record["bread_type"] for record in records})
        if not bread_types:
            return True, []
        placeholders = ", ".join(["%s"] * len(bread_types))
        cursor.execute(
            f"""SELECT id, date, bread_type, products_produced, actual_qty_sold,
                       is_historical, price_per_product
                FROM sales_data WHERE bread_type IN ({placeholders})
                ORDER BY date, id FOR UPDATE""",
            tuple(bread_types),
        )
        existing_rows = cursor.fetchall()
        existing_by_key = {}
        for existing in existing_rows:
            existing_date = (
                existing["date"].isoformat()
                if isinstance(existing["date"], date)
                else str(existing["date"])[:10]
            )
            existing_by_key[(existing["bread_type"], existing_date)] = existing
        records = [dict(record) for record in records]
        for row_number, record in enumerate(records, start=1):
            record_date = parse_date(record.get("date"))
            if not record_date or not record.get("bread_type"):
                conn.rollback()
                if raise_validation_errors:
                    missing = "date" if not record_date else "bread type"
                    raise ValueError(
                        f"Record {row_number} has a missing or invalid {missing}."
                    )
                return False, []
            record["date"] = record_date.isoformat()
            current = existing_by_key.get((record["bread_type"], record["date"]), {})
            if record.get("products_produced") is None:
                record["products_produced"] = int(current.get("products_produced") or 0)
            if record.get("actual_qty_sold") is None:
                record["actual_qty_sold"] = int(current.get("actual_qty_sold") or 0)
            for field_name, converter, label in (
                ("products_produced", int, "produced quantity"),
                ("actual_qty_sold", int, "sold quantity"),
                ("price_per_product", float, "price per product"),
            ):
                try:
                    record[field_name] = converter(record[field_name])
                except (TypeError, ValueError) as err:
                    conn.rollback()
                    if raise_validation_errors:
                        raise ValueError(
                            f"Record {row_number} has an invalid {label}."
                        ) from err
                    return False, []
            if (
                record["products_produced"] < 0
                or record["actual_qty_sold"] < 0
                or record["price_per_product"] < 0
            ):
                conn.rollback()
                if raise_validation_errors:
                    raise ValueError(
                        f"Record {row_number} has a negative quantity or price."
                    )
                return False, []
        products = _fetch_shelf_life_days(cursor)
        shelf_life = {name: item["days"] for name, item in products.items()}
        stock_checked_records = [
            record for record in records
            if record.get("is_historical") not in (1, True, "1")
        ]
        warnings = sales_overstock_warnings(
            existing_rows, stock_checked_records, shelf_life
        ) if stock_checked_records else []
        if warnings and not allow_overstock:
            conn.rollback()
            return False, warnings

        for record in records:
            cursor.execute(
                """INSERT INTO sales_data (
                       date, bread_type, products_produced, actual_qty_sold,
                       waste_returns, price_per_product, temperature,
                       is_holiday, is_promotion, sacks_used, plates_used,
                       expense_amount, is_historical
                   ) VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       products_produced = VALUES(products_produced),
                       actual_qty_sold = VALUES(actual_qty_sold),
                       price_per_product = VALUES(price_per_product),
                       temperature = VALUES(temperature),
                       is_holiday = VALUES(is_holiday),
                       is_promotion = VALUES(is_promotion),
                       sacks_used = VALUES(sacks_used),
                       plates_used = VALUES(plates_used),
                       expense_amount = VALUES(expense_amount),
                       is_historical = VALUES(is_historical)""",
                (
                    record["date"],
                    record["bread_type"],
                    record["products_produced"],
                    record["actual_qty_sold"],
                    record["price_per_product"],
                    record["temperature"],
                    record["is_holiday"],
                    record["is_promotion"],
                    record["sacks_used"],
                    record["plates_used"],
                    record.get("expense_amount", 0.0),
                    record.get("is_historical", 0),
                ),
            )

        for bread_type in {record["bread_type"] for record in records}:
            _rebuild_production_batches(cursor, bread_type)
        conn.commit()
        return True, []
    except mysql.connector.Error:
        if conn:
            conn.rollback()
        logger.exception("Could not save sales and rebuild production batches")
        if raise_db_errors:
            raise
        return False, []
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def save_sales_record(record, allow_overstock=False):
    """Save one sales record using the same transaction as its FIFO allocation."""
    saved, _warnings = save_sales_records([record], allow_overstock=allow_overstock)
    return saved


def update_sales_record(record_id, record, allow_overstock=False):
    """Update cutoff-and-later sold values without rewriting production or old batches."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT id, date, bread_type, products_produced
               FROM sales_data WHERE id = %s FOR UPDATE""",
            (record_id,),
        )
        previous = cursor.fetchone()
        if not previous:
            conn.rollback()
            return False, []
        previous_date = parse_date(previous["date"])
        new_date = parse_date(record.get("date"))
        if (
            not previous_date
            or previous_date < PRODUCTION_OUTCOME_CUTOFF
            or not new_date
            or new_date < PRODUCTION_OUTCOME_CUTOFF
        ):
            conn.rollback()
            return False, []
        try:
            record["actual_qty_sold"] = int(record["actual_qty_sold"])
            record["price_per_product"] = float(record["price_per_product"])
        except (KeyError, TypeError, ValueError):
            conn.rollback()
            return False, []
        if (
            record["actual_qty_sold"] < 0
            or record["price_per_product"] < 0
        ):
            conn.rollback()
            return False, []

        cursor.execute(
            """SELECT products_produced FROM sales_data
               WHERE date = %s AND bread_type = %s FOR UPDATE""",
            (new_date.isoformat(), record["bread_type"]),
        )
        target = cursor.fetchone()
        target_production = int(target["products_produced"] or 0) if target else 0
        if record["actual_qty_sold"] > target_production:
            conn.rollback()
            return False, []

        cursor.execute(
            """UPDATE sales_data
               SET date = %s, bread_type = %s, actual_qty_sold = %s,
                   price_per_product = %s, expense_amount = %s
               WHERE id = %s""",
            (
                record["date"], record["bread_type"], record["actual_qty_sold"],
                record["price_per_product"],
                record.get("expense_amount", 0.0), record_id,
            ),
        )
        conn.commit()
        return True, []
    except mysql.connector.Error:
        if conn:
            conn.rollback()
        logger.exception("Could not update sale and rebuild production batches")
        return False, []
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def save_raw_upload_row(record):
    """Save the original uploaded row into raw_sales_uploads for recovery."""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO raw_sales_uploads (
                    date, bread_type, products_produced, actual_qty_sold,
                    waste_returns, price_per_product, temperature,
                    is_historical, is_holiday, is_promotion, sacks_used,
                    plates_used, expense_amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                record.get("date"),
                record.get("bread_type"),
                record.get("products_produced", 0),
                record.get("actual_qty_sold", 0),
                record.get("waste_returns", 0),
                record.get("price_per_product", 0.0),
                record.get("temperature", 30.0),
                record.get("is_historical", 0),
                record.get("is_holiday", 0),
                record.get("is_promotion", 0),
                record.get("sacks_used", 0),
                record.get("plates_used", 0),
                record.get("expense_amount", 0.0),
            ))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL raw upload save error:", err)


def fetch_raw_sales_uploads():
    """Fetch raw uploads newer than the last intentional sales-data clear."""
    if not USE_MYSQL:
        return []
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT raw_upload_id_cutoff FROM sales_data_recovery_state WHERE id = 1"
        )
        state = cursor.fetchone()
        raw_upload_id_cutoff = state["raw_upload_id_cutoff"] if state else 0
        cursor.execute("""
            SELECT id, date, bread_type, products_produced, actual_qty_sold,
                   waste_returns, price_per_product, temperature,
                   is_historical, is_holiday, is_promotion, sacks_used, plates_used, expense_amount,
                   uploaded_at
            FROM raw_sales_uploads
            WHERE id > %s
            ORDER BY uploaded_at, id
        """, (raw_upload_id_cutoff,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [normalize_record(row) for row in rows]
    except mysql.connector.Error as err:
        print("MySQL raw uploads fetch error:", err)
        return []


def restore_sales_data_from_raw_uploads():
    """Restore the sales_data table from raw uploaded records.

    Raw uploads can include multiple submissions for the same (date, bread_type)
    over time; keep only the most recent row for each key instead of summing all
    historical uploads. This preserves the app's current data model while avoiding
    inflated totals caused by repeated uploads.
    """
    if not USE_MYSQL:
        return 0

    raw_rows = fetch_raw_sales_uploads()
    if not raw_rows:
        return 0

    latest_by_key = {}
    for rec in raw_rows:
        key = (rec.get("date"), rec.get("bread_type"))
        if not key[0] or not key[1]:
            continue
        uploaded_at = rec.get("uploaded_at")
        current = latest_by_key.get(key)
        if current is None:
            latest_by_key[key] = rec
            continue
        current_time = current.get("uploaded_at")
        if uploaded_at is not None and current_time is not None:
            if uploaded_at >= current_time:
                latest_by_key[key] = rec
        elif uploaded_at is not None:
            latest_by_key[key] = rec
        elif current_time is None:
            latest_by_key[key] = rec

    deduped_rows = []
    for rec in latest_by_key.values():
        if not rec.get("date") or not rec.get("bread_type"):
            continue
        a = dict(rec)
        a["products_produced"] = int(rec.get("products_produced") or 0)
        a["actual_qty_sold"] = int(rec.get("actual_qty_sold") or 0)
        a["waste_returns"] = int(rec.get("waste_returns") or 0)
        a["expense_amount"] = float(rec.get("expense_amount") or 0.0)
        total_sold = int(a.get("actual_qty_sold") or 0)
        price = float(rec.get("price_per_product") or 0.0)
        if total_sold > 0:
            a["price_per_product"] = round((total_sold and (float(a.get("actual_qty_sold")) * price) / total_sold) or price, 2)
        else:
            a["price_per_product"] = round(price, 2)
        deduped_rows.append(a)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sales_batch_allocations")
        cursor.execute("DELETE FROM production_batches")
        cursor.execute("TRUNCATE TABLE sales_data")
        conn.commit()
        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        print("MySQL sales_data truncate error:", err)
        return 0

    save_sales_records(deduped_rows, allow_overstock=True)

    global SALES_DATA, WEEKLY_PLAN
    SALES_DATA = load_sales_data()
    WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
    retrain_model_async(SALES_DATA)

    return len(deduped_rows)


def cleanup_duplicate_sales():
    """Remove duplicate sales entries, keeping the first occurrence of each (date, bread_type) pair."""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            # Delete all duplicates except the first (lowest id) for each (date, bread_type)
            cursor.execute("""
                DELETE FROM sales_data
                WHERE id NOT IN (
                    SELECT MIN(id) FROM (
                        SELECT MIN(id) as id FROM sales_data GROUP BY date, bread_type
                    ) AS keep_ids
                )
            """)
            deleted_count = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            print(f"[DEBUG] Cleaned up {deleted_count} duplicate sales records")
            return deleted_count
        except mysql.connector.Error as err:
            print("MySQL cleanup duplicates error:", err)
            return 0
    return 0


def clear_sales_data():
    global SALES_DATA
    if not USE_MYSQL:
        SALES_DATA = []
        return True

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(MAX(id), 0) FROM raw_sales_uploads")
        raw_upload_id_cutoff = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO sales_data_recovery_state (id, raw_upload_id_cutoff)
            VALUES (1, %s)
            ON DUPLICATE KEY UPDATE raw_upload_id_cutoff = VALUES(raw_upload_id_cutoff)
        """, (raw_upload_id_cutoff,))
        cursor.execute("DELETE FROM sales_batch_allocations")
        cursor.execute("DELETE FROM production_batches")
        cursor.execute("DELETE FROM sales_data")
        conn.commit()
        SALES_DATA = []
        return True
    except mysql.connector.Error:
        if conn:
            conn.rollback()
        logger.exception("MySQL clear error")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def generate_sample_sales_records():
    """Return a list of sample sales records for testing and demo purposes."""
    today = date.today()
    sample_dates = [today - timedelta(days=i) for i in range(0, 7)]
    sample_breads = [
        "Pandesal",
        "Ube Pandesal",
        "Cheese Bread",
        "Ensaymada",
        "Mamon",
        "Loose Bread",
    ]
    records = []
    for index, sample_date in enumerate(sample_dates):
        bread_type = sample_breads[index % len(sample_breads)]
        produced = 50 + (index * 5)
        sold = produced - (index % 4) * 3
        price = 4.50 + (index % 3) * 0.25
        records.append({
            "date": sample_date.isoformat(),
            "bread_type": bread_type,
            "products_produced": produced,
            "actual_qty_sold": sold,
            "waste_returns": 0,
            "price_per_product": round(price, 2),
            "temperature": 28.0 + (index % 2),
            "is_holiday": 0,
            "is_promotion": 0,
            "sacks_used": 2 + (index % 2),
            "plates_used": 1 + (index % 3),
            "expense_amount": round(100.0 + index * 15.5, 2),
        })
    return records


def delete_sales_record(record_id):
    """Delete only cutoff-and-later sale rows without rebuilding historical batches."""
    if not USE_MYSQL:
        return False
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT date, bread_type, products_produced, actual_qty_sold
               FROM sales_data WHERE id = %s FOR UPDATE""",
            (record_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return False
        record_date = parse_date(row["date"])
        if not record_date or record_date < PRODUCTION_OUTCOME_CUTOFF:
            conn.rollback()
            return False
        if row["products_produced"] > 0:
            cursor.execute(
                """UPDATE sales_data
                   SET actual_qty_sold = 0, waste_returns = 0
                   WHERE id = %s""",
                (record_id,),
            )
        else:
            cursor.execute("DELETE FROM sales_data WHERE id = %s", (record_id,))
        conn.commit()
        return True
    except mysql.connector.Error:
        if conn:
            conn.rollback()
        logger.exception("Could not delete sales record %s", record_id)
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def load_sales_data():
    if USE_MYSQL:
        rows = fetch_sales_from_db()
        if rows is not None:
            if not rows:
                restored = restore_sales_data_from_raw_uploads()
                if restored > 0:
                    rows = fetch_sales_from_db() or []
            return deduplicate_sales_rows(rows)
        print("Falling back to JSON data because MySQL is unavailable.")
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, encoding="utf-8") as file:
        raw = json.load(file)
    return deduplicate_sales_rows(raw)


def save_model_performance(metrics):
    """Save model performance metrics to database"""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO model_performance (
                    r2_score, mae, accuracy, training_count, test_count
                ) VALUES (%s, %s, %s, %s, %s)
            """, (
                metrics.get("r2", 0),
                metrics.get("mae", 0),
                metrics.get("accuracy", 0),
                metrics.get("trainingCount", 0),
                metrics.get("testCount", 0),
            ))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL model_performance save error:", err)


def fetch_latest_model_performance():
    """Fetch latest model performance from database"""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT r2_score, mae, accuracy, training_count, test_count
                FROM model_performance
                ORDER BY created_at DESC LIMIT 1
            """)
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                return {
                    "r2": float(row["r2_score"]),
                    "mae": float(row["mae"]),
                    "accuracy": int(row["accuracy"]),
                    "trainingCount": int(row["training_count"]),
                    "testCount": int(row["test_count"]),
                }
        except mysql.connector.Error as err:
            print("MySQL model_performance fetch error:", err)
    return None


def save_predictions(results_list, target_date):
    """Save predictions to database."""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            for result in results_list:
                cursor.execute("""
                    INSERT INTO predictions (
                        bread_type, date_predicted, predicted_demand, buffer,
                        recommended_production, confidence
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    result["breadType"],
                    target_date,
                    result["predictedDemand"],
                    result["buffer"],
                    result["recommendedProduction"],
                    result["confidence"],
                ))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL predictions save error:", err)


def build_production_log_rows(prediction_rows, sales_rows, as_of=None):
    """Join predictions to sales data and derive cutoff-era daily outcomes."""
    sale_index = {}
    for sale in sales_rows or []:
        if sale.get("is_historical") in (1, True, "1"):
            continue
        sale_day = sale.get("date")
        if isinstance(sale_day, datetime):
            date_key = sale_day.date().isoformat()
        else:
            date_key = sale_day.isoformat() if isinstance(sale_day, date) else str(sale_day or "")[:10]
        bread_type = sale.get("bread_type") or ""
        if date_key and bread_type:
            sale_key = (date_key, bread_type)
            if sale_key not in sale_index:
                sale_index[sale_key] = dict(sale)
            else:
                sale_index[sale_key]["actual_qty_sold"] = (
                    int(sale_index[sale_key].get("actual_qty_sold") or 0)
                    + int(sale.get("actual_qty_sold") or 0)
                )
                sale_index[sale_key]["products_produced"] = (
                    int(sale_index[sale_key].get("products_produced") or 0)
                    + int(sale.get("products_produced") or 0)
                )

    prediction_index = {}
    for item in prediction_rows or []:
        predicted_day = item.get("date_predicted")
        if isinstance(predicted_day, datetime):
            date_key = predicted_day.date().isoformat()
        else:
            date_key = predicted_day.isoformat() if isinstance(predicted_day, date) else str(predicted_day or "")[:10]
        bread_type = item.get("bread_type") or ""
        if date_key and bread_type and (date_key, bread_type) not in prediction_index:
            prediction_index[(date_key, bread_type)] = item

    inventory = calculate_production_batches(
        sales_rows or [],
        _load_shelf_life_days(),
        as_of=as_of or datetime.now(MANILA_TZ).date(),
    )
    product_prices = _load_product_prices()
    batch_index = {
        (batch["production_date"].isoformat(), batch["bread_type"]): batch
        for batch in inventory["batches"]
    }
    keys = set(prediction_index) | set(sale_index)
    rows = []
    for date_key, bread_type in sorted(keys):
        prediction = prediction_index.get((date_key, bread_type), {})
        sale = sale_index.get((date_key, bread_type), {})
        batch = batch_index.get((date_key, bread_type), {})
        leftover = int(batch.get("leftover_quantity") or 0)
        actual_production = int(sale.get("products_produced") or 0)
        sold = int(sale.get("actual_qty_sold") or 0)
        if parse_date(date_key) >= PRODUCTION_OUTCOME_CUTOFF:
            waste = max(actual_production - sold, 0)
            waste_cost = waste * float(
                sale.get("price_per_product") or product_prices.get(bread_type, 0)
            )
        else:
            waste = int(batch.get("waste_quantity") or 0)
            waste_cost = waste * float(
                batch.get("unit_price") or product_prices.get(bread_type, 0)
            )
        rows.append({
            "bread_type": bread_type,
            "date": date_key,
            "predicted_demand": int(prediction.get("predicted_demand") or 0),
            "recommended_production": int(prediction.get("recommended_production") or 0),
            "actual_production": actual_production,
            "sold": sold,
            "leftover": leftover,
            "expiry_date": batch.get("expiry_date").isoformat() if batch.get("expiry_date") else None,
            "waste": waste,
            "waste_cost": waste_cost,
            "waste_pending": (
                parse_date(date_key) >= PRODUCTION_OUTCOME_CUTOFF
                and actual_production == 0
            ),
            "confidence": int(prediction.get("confidence") or 0),
        })
    return rows


def production_date_bounds(range_name, start_value="", end_value="", today=None):
    """Resolve a Production Log date preset into inclusive Manila-local dates."""
    today = today or datetime.now(MANILA_TZ).date()
    if range_name == "today":
        return today, today
    if range_name == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if range_name == "month":
        start = today.replace(day=1)
        next_month = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        return start, next_month - timedelta(days=1)
    if range_name == "custom":
        start = parse_date(start_value)
        end = parse_date(end_value)
        if not start or not end or start > end:
            raise ValueError("Choose a valid custom start and end date.")
        return start, end
    if range_name == "all":
        return None, None
    raise ValueError("Choose a valid Production Log date range.")


def fetch_production_record_keys(bread_type, start_date, end_date):
    """Fetch filtered Production Log keys without restricting FIFO source rows."""
    if not USE_MYSQL:
        return None

    sales_conditions = []
    prediction_conditions = []
    sales_params = []
    prediction_params = []
    if start_date and end_date:
        sales_conditions.append("date BETWEEN %s AND %s")
        prediction_conditions.append("date_predicted BETWEEN %s AND %s")
        sales_params.extend((start_date, end_date))
        prediction_params.extend((start_date, end_date))
    if bread_type:
        sales_conditions.append("bread_type = %s")
        prediction_conditions.append("bread_type = %s")
        sales_params.append(bread_type)
        prediction_params.append(bread_type)

    sales_where = " WHERE " + " AND ".join(sales_conditions) if sales_conditions else ""
    prediction_where = (
        " WHERE " + " AND ".join(prediction_conditions)
        if prediction_conditions else ""
    )
    query = f"""
        SELECT record_date, bread_type FROM (
            SELECT date AS record_date, bread_type
            FROM sales_data{sales_where}
            UNION
            SELECT date_predicted AS record_date, bread_type
            FROM predictions{prediction_where}
        ) AS production_records
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(query, tuple(sales_params + prediction_params))
        return {
            (
                row[0].isoformat() if isinstance(row[0], (date, datetime))
                else str(row[0])[:10],
                row[1],
            )
            for row in cursor.fetchall()
        }
    except mysql.connector.Error:
        logger.exception("Could not filter Production Log records")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def filter_production_log_rows(rows, bread_type, start_date, end_date, record_keys=None):
    """Filter calculated log rows while preserving their full-history FIFO values."""
    filtered = []
    for row in rows:
        row_date = parse_date(row.get("date"))
        if not row_date:
            continue
        if bread_type and row.get("bread_type") != bread_type:
            continue
        if start_date and row_date < start_date:
            continue
        if end_date and row_date > end_date:
            continue
        key = (row_date.isoformat(), row.get("bread_type"))
        if record_keys is not None and key not in record_keys:
            continue
        filtered.append(row)
    return sorted(filtered, key=lambda row: (row["date"], row["bread_type"]), reverse=True)


def production_log_summary(rows):
    """Summarize the currently filtered Production Log rows."""
    return {
        "recommended": sum(int(row.get("recommended_production") or 0) for row in rows),
        "actual": sum(int(row.get("actual_production") or 0) for row in rows),
        "sold": sum(int(row.get("sold") or 0) for row in rows),
        "leftover": sum(int(row.get("leftover") or 0) for row in rows),
        "waste": sum(int(row.get("waste") or 0) for row in rows),
        "waste_cost": sum(float(row.get("waste_cost") or 0) for row in rows),
    }


def production_filter_context(args):
    """Validate bookmarked filter arguments and build their template context."""
    today = datetime.now(MANILA_TZ).date()
    bread_type = (args.get("bread") or "").strip()
    range_name = (args.get("range") or "all").strip().lower()
    if range_name not in {"today", "week", "month", "all", "custom"}:
        range_name = "all"
    start_value = (args.get("start") or "").strip()
    end_value = (args.get("end") or "").strip()
    error = None
    try:
        start_date, end_date = production_date_bounds(
            range_name, start_value, end_value, today=today
        )
    except ValueError as exc:
        error = str(exc)
        range_name = "all"
        start_date, end_date = None, None
        start_value, end_value = "", ""

    active_products = load_active_product_rows()
    active_names = {product["bread_type"] for product in active_products}
    if bread_type and bread_type not in active_names:
        error = "That bread type is not currently active."
        bread_type = ""

    return {
        "active_products": active_products,
        "selected_bread": bread_type,
        "selected_range": range_name,
        "filter_start": start_value,
        "filter_end": end_value,
        "range_start": start_date,
        "range_end": end_date,
        "filter_error": error,
        "today": today.isoformat(),
    }


def get_filtered_production_rows(filter_context):
    """Build FIFO outcomes from full source data, then filter display rows."""
    record_keys = fetch_production_record_keys(
        filter_context["selected_bread"],
        filter_context["range_start"],
        filter_context["range_end"],
    )
    rows = build_production_log_rows(fetch_prediction_records(), SALES_DATA)
    return filter_production_log_rows(
        rows,
        filter_context["selected_bread"],
        filter_context["range_start"],
        filter_context["range_end"],
        record_keys=record_keys,
    )


def _load_shelf_life_days():
    if not USE_MYSQL:
        return {}
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT bread_type, shelf_life_days FROM bread_type_catalog")
        return {
            row["bread_type"]: max(1, int(row["shelf_life_days"] or 5))
            for row in cursor.fetchall()
        }
    except mysql.connector.Error:
        logger.exception("Could not load product shelf-life settings")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _load_product_prices():
    if not USE_MYSQL:
        return {name: float(price) for name, price in BREAD_PRICES.items()}
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT bread_type, default_price FROM bread_type_catalog")
        return {
            row["bread_type"]: float(row["default_price"] or 0)
            for row in cursor.fetchall()
        }
    except mysql.connector.Error:
        logger.exception("Could not load product prices")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def sales_rows_with_batch_waste(sales_rows):
    """Return display copies with legacy waste replaced by derived batch waste."""
    rows = [dict(row) for row in sales_rows or []]
    inventory = calculate_production_batches(
        rows,
        _load_shelf_life_days(),
        as_of=datetime.now(MANILA_TZ).date(),
    )
    product_prices = _load_product_prices()
    batches = {
        (batch["production_date"].isoformat(), batch["bread_type"]): batch
        for batch in inventory["batches"]
    }
    for row in rows:
        raw_day = row.get("date")
        if isinstance(raw_day, datetime):
            day_key = raw_day.date().isoformat()
        else:
            day_key = raw_day.isoformat() if isinstance(raw_day, date) else str(raw_day or "")[:10]
        batch = batches.get((day_key, row.get("bread_type")), {})
        row["waste_returns"] = int(batch.get("waste_quantity") or 0)
        row["waste_cost"] = row["waste_returns"] * float(
            batch.get("unit_price") or product_prices.get(row.get("bread_type"), 0)
        )
    return rows


def save_production_outcome(date_value, bread_type, field, value, allow_overstock=False):
    """Save actual production without rewriting FIFO history."""
    target_date = parse_date(date_value)
    if field != "actual_production" or not target_date or not bread_type:
        return False, "Invalid production record."

    if USE_MYSQL:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """SELECT actual_qty_sold FROM sales_data
                   WHERE date = %s AND bread_type = %s FOR UPDATE""",
                (target_date.isoformat(), bread_type),
            )
            target_row = cursor.fetchone()
            sold = int(target_row["actual_qty_sold"] or 0) if target_row else 0
            if sold > value:
                conn.rollback()
                return False, f"Sold quantity ({sold}) exceeds actual production ({value})."
            cursor.execute(f"""
                INSERT INTO sales_data (
                    date, bread_type, products_produced, actual_qty_sold,
                    waste_returns, price_per_product, temperature, is_holiday,
                    is_promotion, sacks_used, plates_used, expense_amount
                ) VALUES (%s, %s, %s, 0, 0, 0, 30, 0, 0, 0, 0, 0)
                ON DUPLICATE KEY UPDATE products_produced = VALUES(products_produced),
                    is_historical = 0
            """, (target_date.isoformat(), bread_type, value))
            conn.commit()
        except mysql.connector.Error as err:
            if conn:
                conn.rollback()
            logger.error("Could not update production outcome: %s", err)
            return False, "Could not save this value. Please try again."
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    else:
        target_row = next((
            item for item in SALES_DATA
            if str(item.get("date") or "")[:10] == target_date.isoformat()
            and item.get("bread_type") == bread_type
        ), None)
        sold = int(target_row.get("actual_qty_sold") or 0) if target_row else 0
        if sold > value:
            return False, f"Sold quantity ({sold}) exceeds actual production ({value})."

    sale = next((
        item for item in SALES_DATA
        if str(item.get("date") or "") == target_date.isoformat() and item.get("bread_type") == bread_type
    ), None)
    if sale is None:
        sale = {
            "date": target_date.isoformat(),
            "bread_type": bread_type,
            "products_produced": 0,
            "actual_qty_sold": 0,
            "waste_returns": 0,
            "price_per_product": 0.0,
            "temperature": 30.0,
            "is_holiday": 0,
            "is_promotion": 0,
            "sacks_used": 0,
            "plates_used": 0,
            "expense_amount": 0.0,
        }
        SALES_DATA.append(sale)
    sale["products_produced"] = value

    return True, None


def fetch_prediction_records(filter_date=None):
    """Return saved prediction records from the database."""
    if not USE_MYSQL:
        return []
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT bread_type, date_predicted, predicted_demand, buffer,
                   recommended_production, confidence, created_at
            FROM predictions
        """
        params = ()
        if filter_date:
            query += "\n            WHERE date_predicted = %s"
            params = (filter_date,)
        query += "\n            ORDER BY created_at DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                "bread_type": row["bread_type"],
                "date_predicted": row["date_predicted"].isoformat() if row["date_predicted"] else "",
                "predicted_demand": int(row["predicted_demand"]),
                "buffer": int(row["buffer"]),
                "recommended_production": int(row["recommended_production"]),
                "confidence": int(row["confidence"]),
                "created_at": row["created_at"].isoformat() if row["created_at"] else "",
            }
            for row in rows
        ]
    except mysql.connector.Error as err:
        print("MySQL prediction records fetch error:", err)
        return []


def save_production_plan(weekly_plan, actual_quantities=None, plan_date=None):
    """Save production plan and optional actual outcomes to the database."""
    if USE_MYSQL and weekly_plan:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            plan_timestamp = plan_date or datetime.now()
            for day in weekly_plan:
                day_date = day.get("day_date") or day.get("date")
                for bread in BREAD_TYPES:
                    qty = day.get(bread, 0)
                    if not qty:
                        continue
                    actual_qty = 0
                    if actual_quantities:
                        key = f"{day_date}:{bread}"
                        try:
                            actual_qty = int(actual_quantities.get(key, 0) or 0)
                        except (TypeError, ValueError):
                            actual_qty = 0
                    cursor.execute("""
                        INSERT INTO production_plan (
                            plan_date, bread_type, day_date, planned_quantity, actual_quantity
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            planned_quantity = VALUES(planned_quantity),
                            actual_quantity = VALUES(actual_quantity)
                    """, (plan_timestamp, bread, day_date, qty, actual_qty))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL production_plan save error:", err)


def parse_datetime(value):
    """Convert a string or datetime-like value to a datetime.datetime object."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        pass
    return None


def format_plan_session_label(plan_dt):
    return plan_dt.strftime("%b %d, %Y %I:%M %p") if plan_dt else ""


def fetch_production_plan_sessions():
    """Return distinct saved production plan sessions from the database."""
    if not USE_MYSQL:
        return []
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT DISTINCT plan_date FROM production_plan ORDER BY plan_date DESC")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                "value": row["plan_date"].strftime("%Y-%m-%d %H:%M:%S"),
                "label": format_plan_session_label(row["plan_date"]),
            }
            for row in rows
            if row.get("plan_date")
        ]
    except mysql.connector.Error as err:
        print("MySQL production_plan sessions error:", err)
        return []


def fetch_production_plan_rows(plan_date_value=None):
    """Return saved production plan rows for a specific plan session."""
    if not USE_MYSQL:
        return []
    target_plan_date = parse_datetime(plan_date_value) if plan_date_value else None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT bread_type, day_date, planned_quantity, actual_quantity
            FROM production_plan
        """
        params = ()
        if target_plan_date:
            query += "\n            WHERE plan_date = %s"
            params = (target_plan_date.strftime("%Y-%m-%d %H:%M:%S"),)
        query += "\n            ORDER BY day_date, bread_type"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                "bread_type": row["bread_type"],
                "day_date": row["day_date"],
                "planned_quantity": int(row["planned_quantity"]),
                "actual_quantity": int(row.get("actual_quantity") or 0),
            }
            for row in rows
        ]


    
    except mysql.connector.Error as err:
        print("MySQL production_plan fetch error:", err)
        return []


def build_production_history_grid(plan_rows):
    if not plan_rows:
        return {"columns": [], "rows": []}

    normalized_rows = []
    for row in plan_rows:
        day_date = row.get("day_date")
        parsed_day = parse_date(day_date) if day_date else None
        if not parsed_day or not row.get("bread_type"):
            continue
        normalized_rows.append({
            **row,
            "day_date": parsed_day,
            "planned_quantity": int(row.get("planned_quantity") or 0),
            "actual_quantity": int(row.get("actual_quantity") or 0),
        })

    if not normalized_rows:
        return {"columns": [], "rows": []}

    day_dates = sorted({row["day_date"] for row in normalized_rows})
    columns = [
        {
            "key": day_date.isoformat(),
            "day_name": day_date.strftime("%a").upper(),
            "day_label": day_date.strftime("%b %d").upper(),
        }
        for day_date in day_dates
    ]

    bread_types_in_plan = [
        bread for bread in BREAD_TYPES
        if any(row["bread_type"] == bread for row in normalized_rows)
    ]
    if not bread_types_in_plan:
        bread_types_in_plan = sorted({row["bread_type"] for row in normalized_rows})

    rows = []
    for bread in bread_types_in_plan:
        cells = []
        for col in columns:
            planned_quantity = 0
            actual_quantity = 0
            for plan in normalized_rows:
                if plan["bread_type"] == bread and plan["day_date"].isoformat() == col["key"]:
                    planned_quantity = int(plan.get("planned_quantity") or 0)
                    actual_quantity = int(plan.get("actual_quantity") or 0)
                    break
            cells.append({
                "day_key": col["key"],
                "planned": planned_quantity,
                "actual": actual_quantity,
            })
        rows.append({"bread_type": bread, "cells": cells})

    return {"columns": columns, "rows": rows}


def get_chart_data(sales_data):
    """Generate chart data for dashboard"""
    # Sales and production trend for last 30 days
    today = date.today()
    sales_trend_labels = []
    sales_trend_produced = []
    sales_trend_sold = []
    
    for i in range(29, -1, -1):
        target_date = today - timedelta(days=i)
        date_str = target_date.isoformat()
        daily_produced = sum(row["products_produced"] for row in sales_data if row["date"] == date_str)
        daily_sold = sum(row["actual_qty_sold"] for row in sales_data if row["date"] == date_str)
        sales_trend_labels.append(target_date.strftime("%b %d"))
        sales_trend_produced.append(daily_produced)
        sales_trend_sold.append(daily_sold)
    
    # Bread distribution data
    bread_totals = {}
    for row in sales_data:
        bread_totals[row["bread_type"]] = bread_totals.get(row["bread_type"], 0) + row["actual_qty_sold"]
    
    # Sort by sales volume (descending) to show all breads sorted by popularity
    sorted_breads = sorted(bread_totals.items(), key=lambda x: x[1], reverse=True)
    bread_labels = [label for label, value in sorted_breads]  # All bread types
    bread_data = [value for label, value in sorted_breads]
    
    # Weekly production vs sales data
    weekly_labels = []
    produced_data = []
    sold_data = []
    
    for i in range(6, -1, -1):
        target_date = today - timedelta(days=i)
        date_str = target_date.isoformat()
        weekly_labels.append(target_date.strftime("%a"))
        
        daily_produced = sum(row["products_produced"] for row in sales_data if row["date"] == date_str)
        daily_sold = sum(row["actual_qty_sold"] for row in sales_data if row["date"] == date_str)
        
        produced_data.append(daily_produced)
        sold_data.append(daily_sold)
    
    return {
        "sales_trend_labels": sales_trend_labels,
        "sales_trend_produced": sales_trend_produced,
        "sales_trend_sold": sales_trend_sold,
        "bread_labels": bread_labels,
        "bread_data": bread_data,
        "weekly_labels": weekly_labels,
        "produced_data": produced_data,
        "sold_data": sold_data,
    }


def get_dashboard_period_label(view_scope, view_date=None, view_week=None, view_month=None):
    """Return a display label for the selected dashboard scope and period."""
    if view_scope == 'daily' and view_date:
        d = parse_date(view_date)
        if d:
            return d.strftime("%b %d, %Y")
    elif view_scope == 'weekly' and view_week:
        try:
            # view_week format: YYYY-Www (e.g., 2026-W28)
            parts = view_week.split('-W')
            if len(parts) == 2:
                year = int(parts[0])
                week = int(parts[1])
                # Get Monday of that week
                jan4 = date(year, 1, 4)
                week_one_monday = jan4 - timedelta(days=jan4.weekday())
                monday = week_one_monday + timedelta(weeks=week - 1)
                sunday = monday + timedelta(days=6)
                return f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}"
        except Exception:
            pass
    elif view_scope == 'monthly' and view_month:
        try:
            # view_month format: YYYY-MM
            d = datetime.strptime(view_month, "%Y-%m").date()
            return d.strftime("%B %Y")
        except Exception:
            pass
    elif view_scope == 'all_data':
        return "All Data (Lifetime)"
    
    return "Current Period"


def filter_sales_by_scope(sales, view_scope, view_date=None, view_week=None, view_month=None):
    """Filter sales data by the selected scope and period."""
    if view_scope == 'daily' and view_date:
        d = parse_date(view_date)
        if d:
            target_date = d.isoformat()
            return [row for row in sales if row.get("date") == target_date]
    elif view_scope == 'weekly' and view_week:
        try:
            parts = view_week.split('-W')
            if len(parts) == 2:
                year = int(parts[0])
                week = int(parts[1])
                jan4 = date(year, 1, 4)
                week_one_monday = jan4 - timedelta(days=jan4.weekday())
                monday = week_one_monday + timedelta(weeks=week - 1)
                sunday = monday + timedelta(days=6)
                return [row for row in sales if monday.isoformat() <= row.get("date", "") <= sunday.isoformat()]
        except Exception:
            pass
    elif view_scope == 'monthly' and view_month:
        try:
            d = datetime.strptime(view_month, "%Y-%m").date()
            month_start = d.replace(day=1).isoformat()
            if d.month == 12:
                month_end = d.replace(year=d.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                month_end = d.replace(month=d.month + 1, day=1) - timedelta(days=1)
            month_end = month_end.isoformat()
            return [row for row in sales if month_start <= row.get("date", "") <= month_end]
        except Exception:
            pass
    elif view_scope == 'all_data':
        return sales
    
    # Default: return all sales
    return sales


def compute_kpis(view_date, sales):
    # Calculate totals for all data instead of just the selected date
    total_sales = sum(row["actual_qty_sold"] for row in sales)
    total_revenue = sum(row["actual_qty_sold"] * row["price_per_product"] for row in sales)

    # Get total expenses for all months
    total_expense = fetch_all_expenses_total()

    sales_with_waste = sales_rows_with_batch_waste(sales)
    total_waste = sum(row["waste_returns"] for row in sales_with_waste)
    unique_days = len({row["date"] for row in sales if row["date"]}) or 1
    avg_daily_waste = round(total_waste / unique_days)
    avg_daily_demand = round(sum(row["actual_qty_sold"] for row in sales) / len(sales)) if sales else 0

    return {
        "totalSalesToday": total_sales,
        "totalRevenueToday": total_revenue,
        "totalExpenseToday": total_expense,
        "totalProfitToday": total_revenue - total_expense,
        "avgDailyWaste": avg_daily_waste,
        "wasteReductionPercent": 15,
        "modelAccuracy": MODEL_METRICS.get("accuracy", 0) if MODEL_METRICS else 0,
        "totalRecords": len(sales),
        "avgDailyDemand": avg_daily_demand,
    }


def get_bread_distribution(sales):
    totals = {}
    for row in sales:
        totals[row["bread_type"]] = totals.get(row["bread_type"], 0) + row["actual_qty_sold"]
    items = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    return [{"name": name, "value": value} for name, value in items]


def get_weekly_plan(sales, days=7):
    averages = {}
    for bread in BREAD_TYPES:
        values = [row["actual_qty_sold"] for row in sales if row["bread_type"] == bread]
        if values:
            averages[bread] = sum(values) / len(values)
    if not averages:
        return []
    plan = []
    today = date.today()
    for i in range(days):
        current = today + timedelta(days=i)
        row = {
            "day": current.strftime("%a"),
            "date": current.strftime("%b %d").replace(" 0", " "),
            "day_date": current.isoformat(),
        }
        for bread, avg in averages.items():
            row[bread] = max(1, int(round(avg * (1.05 + random.random() * 0.08))))
        plan.append(row)
    return plan





def predict_demand(bread_type, target_date, temperature, is_holiday, is_promotion, sales):
    """Generate demand predictions for a given date and bread type(s)."""
    selected = [bread_type] if bread_type != "All" else BREAD_TYPES
    results = []
    # Build per-bread historical sales lookup for lag feature generation
    history = {}
    for row in sales or []:
        bread = row.get("bread_type") or "Pandesal"
        date_key = row.get("date")
        if not date_key:
            continue
        history.setdefault(bread, {})[date_key] = int(row.get("actual_qty_sold") or 0)

    for bread in selected:
        model_features = {
            "bread_type": bread,
            "date": target_date.isoformat(),
            "price_per_product": 4.5,
            "temperature": temperature,
            "is_holiday": int(is_holiday),
            "is_promotion": int(is_promotion),
            "sacks_used": 0,
            "plates_used": 0,
        }
        # Compute historical lag features for the prediction date
        model_features.update(compute_lag_features(model_features, history))
        rolling_features = build_rolling_baseline_features(model_features, history, bread)
        model_features.update(rolling_features)
        if MODEL is not None:
            feature_vector = build_feature_vector(model_features)
            try:
                predicted_value = MODEL.predict([feature_vector])[0]
                predicted = max(10, int(round(np.expm1(predicted_value))))
            except Exception:
                predicted = 120
        else:
            values = [row["actual_qty_sold"] for row in sales if row["bread_type"] == bread]
            base = sum(values) / len(values) if values else 120
            prediction = base
            prediction += 60 if is_holiday else 0
            prediction += 40 if is_promotion else 0
            prediction += 30 if target_date.weekday() >= 5 else 0
            prediction += max(0, 30 - temperature) * 2
            prediction += max(0, rolling_features.get("rolling_trend_30", 0)) * 0.25
            predicted = max(10, int(round(prediction)))
        
        buffer_qty = int(round(predicted * 0.1))
        results.append({
            "breadType": bread,
            "predictedDemand": predicted,
            "recommendedProduction": predicted + buffer_qty,
            "buffer": buffer_qty,
            "confidence": min(99, max(75, 90 + random.randint(-8, 8))),
            "date": target_date.strftime("%Y-%m-%d"),
        })
    return results


def retrain_model(sales):
    metrics = train_model(sales)
    save_model_performance(metrics)
    return metrics


def retrain_model_async(sales):
    """Retrain the model in a background thread to avoid blocking web requests."""
    import threading

    def _worker():
        global MODEL_METRICS
        try:
            print('[DEBUG] retrain_model_async: started')
            metrics = retrain_model(sales)
            MODEL_METRICS = metrics
            print('[DEBUG] retrain_model_async: completed')
        except Exception as e:
            print('[DEBUG] retrain_model_async error:', e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def login_required(view):
    """Decorator that requires the user to be logged in before accessing a page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped



def parse_period_start(period):
    """Return the inclusive start date for a given display period."""
    today = date.today()
    if period == "day":
        return today
    if period == "week":
        return today - timedelta(days=6)
    if period == "month":
        return today - timedelta(days=29)
    return None


def filter_sales(sales, search, filter_date, filter_month, filter_bread, history_period="all"):
    """Filter sales records by search text, date, month, bread type, and history period."""
    filtered = []
    # If filter_month provided, parse it as (year, month)
    month_year = None
    if filter_month:
        try:
            year, month = map(int, filter_month.split("-"))
            month_year = (year, month)
        except ValueError:
            month_year = None

    # If a filter_date is provided and a history_period (daily/weekly/monthly/yearly)
    # compute an explicit start/end range based on that date so server-side
    # filtering uses the selected date as the reference (not "last N days").
    sel_date = parse_date(filter_date) if filter_date else None
    range_start = None
    range_end = None
    if sel_date and history_period in ("daily", "day", "weekly", "week", "monthly", "month", "yearly", "year"):
        # Normalize period names: accept both 'daily' and 'day', etc.
        p = history_period.lower()
        if p.startswith('d'):
            range_start = sel_date
            range_end = sel_date
        elif p.startswith('w'):
            # Week containing sel_date (Monday -> Sunday)
            weekday = sel_date.weekday()  # Monday=0
            monday = sel_date - timedelta(days=weekday)
            sunday = monday + timedelta(days=6)
            range_start = monday
            range_end = sunday
        elif p.startswith('m'):
            range_start = sel_date.replace(day=1)
            # compute last day of month
            next_month = (sel_date.replace(day=28) + timedelta(days=4)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            range_end = last_day
        elif p.startswith('y'):
            range_start = date(sel_date.year, 1, 1)
            range_end = date(sel_date.year, 12, 31)
    else:
        # Fallback to relative history period (e.g., last 7/30 days)
        start_date = parse_period_start(history_period)

    for row in sales:
        # Basic search filter
        if search and search.lower() not in row["bread_type"].lower() and search.lower() not in row.get("date", ""):
            continue

        # If explicit range (based on filter_date + history_period) was computed, use it
        if range_start and range_end:
            row_date = parse_date(row.get("date"))
            if not row_date:
                continue
            if row_date < range_start or row_date > range_end:
                continue
        else:
            # Exact-date filter when no range mode is requested
            if filter_date and row.get("date") != filter_date:
                continue
            # Month filter
            if month_year:
                row_date = parse_date(row.get("date"))
                if not row_date or (row_date.year, row_date.month) != month_year:
                    continue
            # Relative history period (e.g., last N days)
            if not range_start and 'start_date' in locals() and start_date:
                row_date = parse_date(row.get("date"))
                if not row_date or row_date < start_date:
                    continue

        # Bread type filter
        if filter_bread and filter_bread != "All" and row.get("bread_type") != filter_bread:
            continue

        filtered.append(row)

    return filtered


@app.route("/", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        entered_password = request.form.get("password", "").strip()
        try:
            if verify_access_code(entered_password):
                session["logged_in"] = True
                session["view_date"] = date.today().isoformat()
                return redirect(url_for("dashboard"))
            error = "Invalid access code. Please try again."
        except mysql.connector.Error:
            error = "Access code verification is temporarily unavailable. Please try again."
            logger.exception("Could not verify the bakery access code")
            return render_template("login.html", error=error), 503

    return render_template("login.html", error=error)


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    error = None
    success = None

    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        expected_token = session.get("_csrf_token", "")
        if (
            not submitted_token
            or not expected_token
            or not secrets.compare_digest(submitted_token, expected_token)
        ):
            error = "Your session expired. Refresh the page and try again."
        else:
            current_code = request.form.get("current_code", "").strip()
            new_code = request.form.get("new_code", "").strip()
            confirm_code = request.form.get("confirm_code", "").strip()
            try:
                if not verify_access_code(current_code):
                    error = "The current access code is incorrect."
                elif len(new_code) < 6:
                    error = "The new access code must be at least 6 characters."
                elif new_code != confirm_code:
                    error = "The new access codes do not match."
                elif new_code == current_code:
                    error = "The new access code must differ from the current code."
                elif not USE_MYSQL:
                    error = "Changing the access code requires MySQL to be enabled."
                else:
                    save_access_code_hash(generate_password_hash(new_code))
                    success = "Access code updated successfully."
            except mysql.connector.Error:
                logger.exception("Could not change the bakery access code")
                error = "The access code could not be changed. Please try again."

    return render_template("change_password.html", error=error, success=success)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    """Render the dashboard with KPIs, charts, and a weekly plan."""
    global WEEKLY_PLAN, MODEL_METRICS
    
    # Get scope and period parameters from request or session
    view_scope = request.values.get("view_scope") or session.get("view_scope") or "monthly"
    view_date = request.values.get("view_date") or session.get("view_date") or date.today().isoformat()
    view_week = request.values.get("view_week") or session.get("view_week") or ""
    view_month = request.values.get("view_month") or session.get("view_month") or date.today().strftime("%Y-%m")
    
    # Store in session
    session["view_scope"] = view_scope
    session["view_date"] = view_date
    session["view_week"] = view_week
    session["view_month"] = view_month

    if request.method == "POST" and request.form.get("action") == "generate_plan":
        WEEKLY_PLAN = get_weekly_plan(SALES_DATA)

    # Filter sales by scope
    filtered_sales = filter_sales_by_scope(SALES_DATA, view_scope, view_date, view_week, view_month)
    
    # Get the display label
    dashboard_period_label = get_dashboard_period_label(view_scope, view_date, view_week, view_month)
    
    kpis = compute_kpis(view_date, filtered_sales)
    distribution = get_bread_distribution(filtered_sales)[:4]
    chart_data = get_chart_data(filtered_sales)
    monthly_analytics = get_monthly_analytics(filtered_sales)
    future_forecast = get_future_demand_forecast(filtered_sales, days_ahead=14)  # 2 weeks forecast

    # Build ingredient-based alerts (absolute low-stock threshold: <= 5 units)
    ALERT_ABSOLUTE_THRESHOLD = 5
    alerts = []
    try:
        ingredients = fetch_ingredients_from_db() or []
        def fmt_qty(v):
            try:
                f = float(v or 0)
                if f.is_integer():
                    return str(int(f))
                return ('%.2f' % f).rstrip('0').rstrip('.')
            except Exception:
                return str(v or '')

        for ing in ingredients:
            try:
                current = float(ing.get('current_stock') or 0)
            except Exception:
                continue

            # Compute reorder threshold (fallback to absolute threshold when not set)
            try:
                reorder_thr = float(ing.get('reorder_threshold') or 0)
            except Exception:
                reorder_thr = 0

            # Use a safe denominator for progress calculation
            denom = reorder_thr if reorder_thr > 0 else ALERT_ABSOLUTE_THRESHOLD
            if denom <= 0:
                progress_pct = 0
            else:
                progress_pct = int(min(100, max(0, (current / denom) * 100)))

            unit = (ing.get('unit') or '').strip()
            unit_label = f" {unit}" if unit else ''
            current_label = f"{fmt_qty(current)}{unit_label}"
            threshold_label = f"{fmt_qty(reorder_thr if reorder_thr > 0 else ALERT_ABSOLUTE_THRESHOLD)}{unit_label}"

            if current <= 0:
                status_label = "Out of stock"
            elif current <= denom:
                status_label = "Low stock"
            else:
                status_label = "Sufficient"

            # Absolute alert when current stock is <= ALERT_ABSOLUTE_THRESHOLD
            if current <= ALERT_ABSOLUTE_THRESHOLD:
                title = f"Low stock: {ing.get('name')}"
                message = f"{ing.get('name')} stock ({current_label}) is at or below alert level ({threshold_label})."
                alerts.append({
                    "type": "low",
                    "title": title,
                    "message": message,
                    "ingredient": ing,
                    "current_label": current_label,
                    "threshold_label": threshold_label,
                    "progress_pct": progress_pct,
                    "status_label": status_label,
                })
    except Exception:
        alerts = []

    return render_template(
        "dashboard.html",
        active="dashboard",
        view_scope=view_scope,
        view_date=view_date,
        view_week=view_week,
        view_month=view_month,
        dashboard_period_label=dashboard_period_label,
        kpis=kpis,
        distribution=distribution,
        weekly_plan=WEEKLY_PLAN,
        monthly_analytics=monthly_analytics,
        future_forecast=future_forecast,
        alerts=alerts,
        **chart_data,
    )


@app.route("/predict", methods=["GET", "POST"])
@login_required
def predict():
    """Handle demand prediction requests and display prediction results."""
    results = []
    form = {
        "bread_type": "All",
        "date": (date.today() + timedelta(days=1)).isoformat(),
        "temperature": 28,
        "is_holiday": False,
        "is_peak day": False,
    }
    if request.method == "POST":
        form["bread_type"] = request.form.get("bread_type", "All")
        form["date"] = request.form.get("date", form["date"])
        form["temperature"] = int(request.form.get("temperature", 28))
        form["is_holiday"] = request.form.get("is_holiday") == "on"
        form["is_peak day"] = request.form.get("is_peak day") == "on"
        target_date = parse_date(form["date"]) or date.today()
        results = predict_demand(
            form["bread_type"],
            target_date,
            form["temperature"],
            form["is_holiday"],
            form["is_peak day"],
            SALES_DATA,
        )
        # Save predictions to database
        save_predictions(results, target_date)
    return render_template(
        "predict.html",
        active="predict",
        form=form,
        results=results,
        bread_types=load_active_bread_types(),
    )


@app.route("/sales-log", methods=["GET", "POST"])
@login_required
def sales_log():
    """Show sales records and allow manual entry, CSV upload, plus monthly expense management."""
    global WEEKLY_PLAN, MODEL_METRICS, SALES_DATA
    if USE_MYSQL and (not SALES_DATA or not app.config.get("TESTING", False)):
        SALES_DATA = load_sales_data()
    search = request.values.get("search", "")
    filter_date = request.values.get("filter_date", "")
    filter_month = request.values.get("filter_month", "")
    view_month = filter_month or date.today().strftime("%Y-%m")
    filter_bread = request.values.get("filter_bread", "All")
    history_period = request.values.get("history_period", "all")
    expense_categories = EXPENSE_CATEGORIES
    message = request.args.get("notice")
    wants_json = "application/json" in request.headers.get("Accept", "").lower()
    allow_overstock = request.form.get("allow_overstock") == "1"

    def overstock_response(warnings):
        details = "; ".join(
            f'{item["bread_type"]} on {item["date"]}: '
            f'{item["requested"]} requested, {item["available"]} available'
            for item in warnings
        )
        warning_message = (
            "This sale exceeds available unexpired stock. "
            f"{details}. Confirm explicitly if you still want to save."
        )
        if wants_json:
            return jsonify({
                "success": False,
                "overstock": True,
                "warnings": warnings,
                "message": warning_message,
            }), 409
        return warning_message

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_record":
            new_date = request.form.get("date", date.today().isoformat())
            new_bread = request.form.get("bread_type", "Pandesal")
            sold = int(request.form.get("sold", 0))
            price = float(request.form.get("price", 4.5))
            record = {
                "date": new_date,
                "bread_type": new_bread,
                "actual_qty_sold": sold,
                "price_per_product": price,
            }
            saved, error = save_cutoff_sales_records([record])
            if error:
                if wants_json:
                    return jsonify({"success": False, "error": error}), 400
                message = error
            elif saved:
                SALES_DATA = load_sales_data()
                WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                retrain_model_async(SALES_DATA)
                if wants_json:
                    return jsonify({"success": True, "message": "Manual record saved successfully."})
                message = "Manual record saved successfully."
            else:
                if wants_json:
                    return jsonify({"success": False, "error": "Failed to save manual record."}), 503
                message = "Failed to save manual record."
        elif action in ("upload_csv", "upload_excel"):
            uploaded_file = request.files.get("file")
            logger.info(
                "Sales upload received: content_type=%s file_keys=%s filename=%r",
                request.content_type,
                list(request.files.keys()),
                uploaded_file.filename if uploaded_file else None,
            )
            if not uploaded_file or uploaded_file.filename == "":
                if wants_json:
                    return jsonify({"success": False, "error": "Please choose a file to upload."}), 400
                message = "Please choose a file to upload."
            else:
                extension = uploaded_file.filename.rsplit(".", 1)[-1].lower()
                if extension not in {"csv", "xlsx", "xls"}:
                    error_message = "Choose a CSV, XLSX, or XLS file to upload."
                    if wants_json:
                        return jsonify({"success": False, "error": error_message}), 400
                    return redirect(url_for("sales_log", notice=error_message))

                try:
                    records = parse_csv_upload(uploaded_file)
                except ValueError as err:
                    logger.warning("Historical sales upload date validation failed: %s", err)
                    if wants_json:
                        return jsonify({"success": False, "error": str(err)}), 400
                    return redirect(url_for("sales_log", notice=str(err)))
                if records is None:
                    if wants_json:
                        return jsonify({
                            "success": False,
                            "error": "Failed to parse the uploaded file. Please check the file format.",
                        }), 400
                    message = "Failed to parse the uploaded file. Please check the file format."
                elif not records:
                    if wants_json:
                        return jsonify({
                            "success": False,
                            "error": "No valid records were found in the uploaded file.",
                        }), 400
                    message = "No valid records were found in the uploaded file."
                else:
                    try:
                        if any(
                            (record_date := parse_date(record.get("date"))) is None
                            or record_date < PRODUCTION_OUTCOME_CUTOFF
                            for record in records
                        ):
                            raise ValueError(
                                "Sales records before 2026-10-11 are read-only and cannot be imported."
                            )
                        sold_records = [{
                            "date": record["date"],
                            "bread_type": record["bread_type"],
                            "actual_qty_sold": record.get("actual_qty_sold", 0),
                            "price_per_product": record.get("price_per_product", 0.0),
                        } for record in records]
                        imported, error = save_cutoff_sales_records(sold_records)
                        if error:
                            raise ValueError(error)
                        skipped = 0
                    except mysql.connector.Error as err:
                        logger.exception(
                            "Historical sales upload failed while saving %d parsed record(s)",
                            len(records),
                        )
                        error_message = f"Could not save uploaded records: {err}"
                        if wants_json:
                            return jsonify({
                                "success": False,
                                "error": error_message,
                            }), 503
                        return redirect(url_for("sales_log", notice=error_message))
                    except ValueError as err:
                        logger.warning("Historical sales upload validation failed: %s", err)
                        if wants_json:
                            return jsonify({
                                "success": False,
                                "error": str(err),
                            }), 400
                        return redirect(url_for("sales_log", notice=str(err)))
                    except RuntimeError as err:
                        logger.error("Historical sales upload is unavailable: %s", err)
                        if wants_json:
                            return jsonify({
                                "success": False,
                                "error": str(err),
                            }), 503
                        return redirect(url_for("sales_log", notice=str(err)))
                    SALES_DATA = load_sales_data()
                    WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                    retrain_model_async(SALES_DATA)
                    message = f"{imported} imported, {skipped} skipped."
                    if wants_json:
                        return jsonify({"success": True, "message": message})
        elif action == "clear_db":
            message = "Clearing sales records is disabled to protect historical data."
        elif action == "restore_from_raw_uploads":
            message = "Restoring sales records is disabled to protect historical data."
        elif action == "seed_sample_data":
            message = "Replacing sales records is disabled to protect historical data."
        elif action == "delete_record":
            record_id = request.form.get("record_id")
            if record_id:
                if not delete_sales_record(record_id):
                    message = "The sales record could not be deleted."
                else:
                    SALES_DATA = load_sales_data()
                    WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                    retrain_model_async(SALES_DATA)
                    message = "Sale removed and production batches recalculated."
            else:
                message = "Error: Record ID not found."
        elif action == "delete_selected":
            selected_ids = request.form.get("selected_ids", "")
            deleted = 0
            if selected_ids:
                for rid in selected_ids.split(","):
                    rid = rid.strip()
                    if rid:
                        deleted += int(delete_sales_record(rid))
                SALES_DATA = load_sales_data()
                WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                retrain_model_async(SALES_DATA)
            message = (
                f"{deleted} selected sale(s) removed and batches recalculated."
                if deleted else "No selected records were deleted."
            )
        elif action == "save_batch_records":
            start_date = request.form.get("date", date.today().isoformat())
            try:
                scope_days = max(1, min(int(request.form.get("scope_days") or 1), 7))
            except ValueError:
                if wants_json:
                    return jsonify({"success": False, "error": "Choose a valid date scope."}), 400
                message = "Choose a valid date scope."
                scope_days = 0
            bread_names = request.form.getlist("bread_type")
            entry_dates = request.form.getlist("entry_date")
            sold_values = request.form.getlist("sold")
            price_values = request.form.getlist("price")

            if (
                scope_days == 0
                or not bread_names
                or len(entry_dates) != len(bread_names)
                or len(sold_values) != len(bread_names)
                or len(price_values) != len(bread_names)
            ):
                error = "Sales entries are incomplete."
                if wants_json:
                    return jsonify({"success": False, "error": error}), 400
                records = []
                saved = False
            else:
                records = [{
                    "date": entry_date,
                    "bread_type": bread_type,
                    "actual_qty_sold": sold,
                    "price_per_product": price,
                } for entry_date, bread_type, sold, price in zip(
                    entry_dates, bread_names, sold_values, price_values
                )]
                saved, error = save_cutoff_sales_records(records)
            if error:
                if wants_json:
                    return jsonify({"success": False, "error": error}), 400
                message = error
            elif saved:
                SALES_DATA = load_sales_data()
                WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                retrain_model_async(SALES_DATA)
                if wants_json:
                    return jsonify({
                        "success": True,
                        "message": f"{len(records)} record(s) saved for the selected {scope_days}-day scope.",
                    })
                message = f"{len(records)} record(s) saved for the selected {scope_days}-day scope."
            elif wants_json:
                return jsonify({"success": False, "error": "Could not save these sales records."}), 503
            else:
                message = "Could not save these sales records."
        elif action == "save_monthly_expenses":
            for category in expense_categories:
                amount = float(request.form.get(f"amount_{category}", 0.0) or 0.0)
                note = request.form.get(f"note_{category}", "").strip()
                if amount > 0:
                    save_monthly_expense(filter_month, category, amount, note)
            message = "Monthly expenses saved successfully."
        elif action == "clear_monthly_expenses":
            if USE_MYSQL:
                try:
                    month_date = parse_month(filter_month) or date.today().replace(day=1)
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM monthly_expenses WHERE expense_month = %s", (month_date,))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    message = "Monthly expenses for this month were cleared."
                except mysql.connector.Error as err:
                    print("MySQL clear monthly expenses error:", err)
                    message = "Error clearing monthly expenses."
    sales_view = [dict(row) for row in SALES_DATA]
    for row in sales_view:
        row["waste_returns"] = max(
            0,
            int(row.get("products_produced") or 0)
            - int(row.get("actual_qty_sold") or 0),
        )
    filtered = filter_sales(sales_view, search, filter_date, filter_month, filter_bread, history_period)
    total_dataset = len(SALES_DATA)
    total_filtered = len(filtered)

    # Summary totals should reflect the full uploaded dataset, not just the currently displayed table subset.
    full_sales_dataset = sales_view if sales_view else []
    sold_count = sum(row["actual_qty_sold"] for row in full_sales_dataset)
    produced_count = sum(row["products_produced"] for row in full_sales_dataset)
    total_sales_value = sum(row["actual_qty_sold"] * row["price_per_product"] for row in full_sales_dataset)
    total_waste = sum(row["waste_returns"] for row in full_sales_dataset)

    # Get expenses for the same period as sales data
    if filter_month:
        # If filtering by month, get expenses for that month
        monthly_expense = fetch_monthly_expense_total(filter_month)
        expense_scope = filter_month
    else:
        # If no month filter, calculate all expenses for overall totals
        monthly_expense = fetch_all_expenses_total()
        expense_scope = "All Time"

    current_expenses = fetch_monthly_expenses(view_month)  # For the form, always current month
    profit_value = total_sales_value - monthly_expense
    waste_rate = round((total_waste / produced_count * 100) if produced_count else 0, 1)

    # Debug: log counts and active filters to help diagnose missing data issues
    try:
        print(f"[DEBUG] SALES_DATA={len(SALES_DATA)} total_filtered={len(filtered)} filter_date={filter_date!r} filter_month={filter_month!r} filter_bread={filter_bread!r}")
        # Print a small sample of the loaded sales data to confirm structure
        sample = SALES_DATA[:5]
        try:
            print("[DEBUG] SAMPLE_SALES=", json.dumps(sample, default=str))
        except Exception:
            print("[DEBUG] SAMPLE_SALES (repr)=", repr(sample))
    except Exception:
        pass

    try:
        active_product_rows = load_active_product_rows()
    except mysql.connector.Error:
        logger.exception("Could not load active products for the sales page")
        abort(503, description="The active product list is temporarily unavailable.")
    active_bread_types = [row["bread_type"] for row in active_product_rows]
    active_bread_prices = {
        row["bread_type"]: row["default_price"] for row in active_product_rows
    }

    return render_template(
        "sales_log.html",
        active="sales",
        sales=filtered,
        bread_types=active_bread_types,
        bread_prices=active_bread_prices,
        products=active_product_rows,
        search=search,
        filter_date=filter_date,
        filter_bread=filter_bread,
        filter_month=filter_month,
        view_month=view_month,
        message=message,
        today=date.today().isoformat(),
        total_dataset=total_dataset,
        total_filtered=total_filtered,
        sold_count=sold_count,
        produced_count=produced_count,
        total_sales_value=total_sales_value,
        total_waste=total_waste,
        monthly_expense=monthly_expense,
        profit_value=profit_value,
        waste_rate=waste_rate,
        history_period=history_period,
        expense_categories=expense_categories,
        current_expenses=current_expenses,
        expense_scope=expense_scope,
    )


@app.route("/api/production/actual-quantities")
@login_required
def production_actual_quantities():
    start_date = parse_date(request.args.get("start_date") or "")
    bread_types = list(dict.fromkeys(
        bread_type.strip()
        for bread_type in request.args.getlist("bread_type")
        if bread_type.strip()
    ))
    try:
        scope_days = int(request.args.get("scope_days") or 1)
    except ValueError:
        return jsonify({"success": False, "error": "Choose a valid date scope."}), 400

    if (
        not start_date
        or scope_days < 1
        or scope_days > 7
        or not bread_types
        or any(len(bread_type) > 100 for bread_type in bread_types)
    ):
        return jsonify({"success": False, "error": "Choose a valid date, scope, and bread type."}), 400
    if start_date < PRODUCTION_OUTCOME_CUTOFF:
        return jsonify({
            "success": False,
            "error": "Sales before 2026-10-11 are read-only.",
        }), 400

    end_date = start_date + timedelta(days=scope_days - 1)
    placeholders = ", ".join(["%s"] * len(bread_types))
    production_by_key = {}
    if USE_MYSQL:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                f"""SELECT date, bread_type, products_produced
                    FROM sales_data
                    WHERE date BETWEEN %s AND %s
                      AND bread_type IN ({placeholders})
                      AND is_historical = 0""",
                (start_date, end_date, *bread_types),
            )
            production_by_key = {
                (
                    (parse_date(row["date"]) or start_date).isoformat(),
                    row["bread_type"],
                ): int(row["products_produced"] or 0)
                for row in cursor.fetchall()
            }
        except mysql.connector.Error:
            logger.exception("Could not load actual production for sales entry")
            return jsonify({
                "success": False,
                "error": "Could not load actual production. Please try again.",
            }), 503
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    else:
        production_by_key = {
            (str(row.get("date") or "")[:10], row.get("bread_type")):
                int(row.get("products_produced") or 0)
            for row in SALES_DATA
            if row.get("is_historical") not in (1, True, "1")
        }

    production = []
    for offset in range(scope_days):
        target_date = (start_date + timedelta(days=offset)).isoformat()
        for bread_type in bread_types:
            production.append({
                "date": target_date,
                "bread_type": bread_type,
                "quantity": production_by_key.get((target_date, bread_type), 0),
            })
    return jsonify({"success": True, "production": production})


@app.route("/api/production/availability")
@login_required
def production_stock_availability():
    bread_type = (request.args.get("bread_type") or "").strip()
    target_date = parse_date(request.args.get("date") or "")
    raw_produced = request.args.get("produced")
    if not bread_type or len(bread_type) > 100 or not target_date:
        return jsonify({"success": False, "error": "Choose a valid product and date."}), 400

    produced = None
    if raw_produced not in (None, ""):
        if not raw_produced.isdecimal() or int(raw_produced) > 2147483647:
            return jsonify({"success": False, "error": "Production must be a whole number 0 or greater."}), 400
        produced = int(raw_produced)

    if USE_MYSQL:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """SELECT id, date, bread_type, products_produced,
                          actual_qty_sold, is_historical, price_per_product
                   FROM sales_data WHERE bread_type = %s ORDER BY date, id""",
                (bread_type,),
            )
            sales_rows = cursor.fetchall()
            products = _fetch_shelf_life_days(cursor)
            shelf_life = {
                name: product["days"] for name, product in products.items()
            }
        except mysql.connector.Error:
            logger.exception("Could not calculate available product stock")
            return jsonify({"success": False, "error": "Stock is temporarily unavailable."}), 503
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    else:
        sales_rows = SALES_DATA
        shelf_life = {}

    available = available_stock_for_day(
        sales_rows,
        bread_type,
        target_date,
        produced=produced,
        shelf_life_days=shelf_life,
    )
    return jsonify({"success": True, "available": available})


@app.route("/sales-log/add-product", methods=["POST"])
@login_required
def add_sales_product():
    """Add a product to the persistent catalog for future sales entries."""
    global BREAD_TYPES, INACTIVE_BREAD_TYPES

    bread_type = " ".join(request.form.get("product_name", "").split())
    if not bread_type:
        return jsonify({"success": False, "error": "Enter a product name."}), 400
    if len(bread_type) > 100:
        return jsonify({"success": False, "error": "Product names must be 100 characters or fewer."}), 400
    try:
        default_price = float(request.form.get("product_price", ""))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Enter a valid product price."}), 400
    if not math.isfinite(default_price) or default_price < 0 or default_price > 99999999.99:
        return jsonify({"success": False, "error": "Enter a price from 0 to 99,999,999.99."}), 400
    default_price = round(default_price, 2)

    try:
        active_names = {name.casefold() for name in load_active_bread_types()}
    except mysql.connector.Error:
        logger.exception("Could not check the active product catalog")
        return jsonify({
            "success": False,
            "error": "The product list is temporarily unavailable.",
        }), 503
    if bread_type.casefold() in active_names:
        return jsonify({"success": False, "error": "That product already exists."}), 409

    saved_result = persist_custom_bread_type(bread_type, default_price)
    if len(saved_result) == 2:
        saved, error = saved_result
        product_id = None
        reactivated = False
    else:
        saved, error, product_id, reactivated = saved_result
    if not saved:
        status = 409 if error and "already exists" in error else 503
        return jsonify({"success": False, "error": error}), status

    if not any(existing.casefold() == bread_type.casefold() for existing in BREAD_TYPES):
        BREAD_TYPES.append(bread_type)
    INACTIVE_BREAD_TYPES = {
        inactive for inactive in INACTIVE_BREAD_TYPES
        if inactive.casefold() != bread_type.casefold()
    }
    BREAD_PRICES[bread_type] = default_price
    retrain_model_async(SALES_DATA)
    return jsonify({
        "success": True,
        "product": bread_type,
        "price": default_price,
        "product_id": product_id,
        "reactivated": reactivated,
        "delete_url": url_for("delete_sales_product", product_id=product_id) if product_id else "",
    })


@app.route("/products/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_sales_product(product_id):
    """Soft-delete a product from future-entry selectors without changing history."""
    submitted_token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected_token = session.get("_csrf_token", "")
    if (
        not submitted_token
        or not expected_token
        or not secrets.compare_digest(submitted_token, expected_token)
    ):
        return jsonify({
            "success": False,
            "error": "Your session expired. Refresh the page and try again.",
        }), 400

    if not USE_MYSQL:
        product = next(
            (
                row for row in load_active_product_rows()
                if int(row["id"]) == product_id
            ),
            None,
        )
        if not product:
            return jsonify({"success": False, "error": "Product not found."}), 404
        INACTIVE_BREAD_TYPES.add(product["bread_type"])
        return jsonify({
            "success": True,
            "product_id": product_id,
            "product": product["bread_type"],
            "message": f"{product['bread_type']} deleted",
        })

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, bread_type FROM bread_type_catalog "
            "WHERE id = %s AND is_active = 1 FOR UPDATE",
            (product_id,),
        )
        product = cursor.fetchone()
        if not product:
            conn.rollback()
            return jsonify({"success": False, "error": "Product not found or already deleted."}), 404
        cursor.execute(
            "UPDATE bread_type_catalog SET is_active = 0 WHERE id = %s AND is_active = 1",
            (product_id,),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return jsonify({"success": False, "error": "Product is no longer active."}), 404
        conn.commit()
        INACTIVE_BREAD_TYPES.add(product["bread_type"])
        return jsonify({
            "success": True,
            "product_id": product_id,
            "product": product["bread_type"],
            "message": f"{product['bread_type']} deleted",
        })
    except mysql.connector.Error:
        if conn:
            conn.rollback()
        logger.exception("Could not soft-delete product %s", product_id)
        return jsonify({
            "success": False,
            "error": "Could not delete this product. Please try again.",
        }), 503
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/sales-log/edit/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_sale(record_id):
    """Show and handle edits for a single sales record."""
    global SALES_DATA, WEEKLY_PLAN, MODEL_METRICS
    record = None

    # Try to load the record from the database when available
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT id, date, bread_type, products_produced, actual_qty_sold, waste_returns,
                       price_per_product, temperature, is_historical, is_holiday, is_promotion,
                       sacks_used, plates_used, expense_amount
                FROM sales_data WHERE id = %s
            """, (record_id,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                record = normalize_record(row)
        except mysql.connector.Error as err:
            print("MySQL fetch single record error:", err)
    else:
        # Fallback: find in-memory record by id
        for r in SALES_DATA:
            if r.get("id") == record_id:
                record = r.copy()
                break

    if not record:
        return redirect(url_for("sales_log"))

    if request.method == "POST":
        new_date = parse_date(request.form.get("date", record.get("date")))
        new_bread = request.form.get("bread_type", record.get("bread_type"))
        existing_date = parse_date(record.get("date"))
        if (
            not new_date
            or new_date < PRODUCTION_OUTCOME_CUTOFF
            or not existing_date
            or existing_date < PRODUCTION_OUTCOME_CUTOFF
        ):
            abort(400, description="Sales records before 2026-10-11 are read-only.")
        # Allow deletion via the edit form
        if request.form.get("action") == "delete_record":
            if not delete_sales_record(record_id):
                abort(503, description="Could not remove this sale.")
            SALES_DATA = load_sales_data()
            WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
            retrain_model_async(SALES_DATA)
            return redirect(url_for(
                "sales_log",
                notice="Sale removed; any production recorded for that date was preserved.",
            ))

        # Update record with submitted values
        sold = int(request.form.get("sold", record.get("actual_qty_sold", 0)))
        price = float(request.form.get("price", record.get("price_per_product", 0.0)))
        expense_amount = float(request.form.get("expense_amount", record.get("expense_amount", 0.0)))
        updated_record = {
            **record,
            "date": new_date.isoformat(),
            "bread_type": new_bread,
            "actual_qty_sold": sold,
            "price_per_product": price,
            "expense_amount": expense_amount,
        }
        allow_overstock = request.form.get("allow_overstock") == "1"
        if USE_MYSQL:
            saved, warnings = update_sales_record(
                record_id, updated_record, allow_overstock=allow_overstock
            )
            if warnings and not allow_overstock:
                warning_details = "; ".join(
                    f'{item["bread_type"]} on {item["date"]}: '
                    f'{item["requested"]} requested, {item["available"]} available'
                    for item in warnings
                )
                return render_template(
                    "edit_sale.html",
                    record=updated_record,
                    bread_types=load_active_bread_types(),
                    overstock_warning=(
                        "This sale exceeds available unexpired stock. "
                        f"{warning_details}. Confirm before saving anyway."
                    ),
                ), 409
            if not saved:
                abort(400, description="Sold quantity must not exceed actual production.")
        else:
            target_row = next((
                row for row in SALES_DATA
                if str(row.get("date") or "")[:10] == new_date.isoformat()
                and row.get("bread_type") == new_bread
            ), None)
            target_production = int(
                target_row.get("products_produced") or 0
            ) if target_row else 0
            if sold < 0 or sold > target_production:
                abort(400, description="Sold quantity must not exceed actual production.")
            for r in SALES_DATA:
                if r.get("id") == record_id:
                    r.update(updated_record)
                    break

        SALES_DATA = load_sales_data() if USE_MYSQL else SALES_DATA
        WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
        retrain_model_async(SALES_DATA)
        return redirect(url_for("sales_log"))

    # GET: render the edit form
    return render_template(
        "edit_sale.html",
        record=record,
        bread_types=load_active_bread_types(),
        overstock_warning=None,
    )


@app.route("/production-plan", methods=["GET", "POST"])
@login_required
def production_plan():
    """Show historical production-log records from saved demand predictions."""
    global WEEKLY_PLAN
    message = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "generate_plan":
            target_date = date.today() + timedelta(days=1)
            results = predict_demand("All", target_date, 28, False, False, SALES_DATA)
            save_predictions(results, target_date)
            message = "Prediction results saved to the production log."

    filter_context = production_filter_context(request.args)
    try:
        production_rows = get_filtered_production_rows(filter_context)
    except mysql.connector.Error:
        abort(503)
    summary = production_log_summary(production_rows)

    return render_template(
        "production_plan.html",
        active="plan",
        production_rows=production_rows,
        message=message,
        summary=summary,
        **filter_context,
    )


@app.route("/production-plan/update-outcome", methods=["POST"])
@login_required
def update_production_outcome():
    global SALES_DATA
    date_value = (request.form.get("date") or "").strip()
    bread_type = (request.form.get("bread_type") or "").strip()
    field = (request.form.get("field") or "").strip()
    raw_value = (request.form.get("value") or "").strip()

    if field != "actual_production":
        return jsonify({"success": False, "error": "Only actual production can be edited."}), 400
    if not parse_date(date_value) or not bread_type or len(bread_type) > 100:
        return jsonify({"success": False, "error": "Choose a valid production record."}), 400
    if not raw_value.isdecimal():
        return jsonify({"success": False, "error": "Enter a non-negative whole number."}), 400

    value = int(raw_value)
    if value > 2147483647:
        return jsonify({"success": False, "error": "Value is too large."}), 400

    allow_overstock = request.form.get("allow_overstock") == "1"
    saved, error = save_production_outcome(
        date_value, bread_type, field, value, allow_overstock=allow_overstock
    )
    if not saved:
        if error and error.startswith("Sold quantity"):
            return jsonify({"success": False, "error": error}), 409
        if error and error.startswith("OVERSTOCK:"):
            return jsonify({
                "success": False,
                "overstock": True,
                "error": error[len("OVERSTOCK: "):].strip(),
            }), 409
        status = 404 if error == "No matching sales record exists." else 500
        return jsonify({"success": False, "error": error}), status
    if USE_MYSQL:
        SALES_DATA = load_sales_data()
    updated_row = next(
        (
            row for row in build_production_log_rows([], SALES_DATA)
            if row["date"] == date_value and row["bread_type"] == bread_type
        ),
        {},
    )
    return jsonify({
        "success": True,
        "field": field,
        "value": value,
        "sold": updated_row.get("sold", 0),
        "leftover": updated_row.get("leftover", 0),
        "expiry_date": updated_row.get("expiry_date"),
        "waste": updated_row.get("waste", 0),
        "waste_cost": updated_row.get("waste_cost", 0),
        "waste_pending": updated_row.get("waste_pending", False),
    })


@app.route("/monthly-expenses", methods=["GET", "POST"])
@login_required
def monthly_expenses():
    """Manage expenses by daily, weekly, or monthly scope."""
    scope_type = (request.values.get("scope_type") or request.form.get("scope_type") or "monthly").strip().lower()
    selected_date = request.values.get("selected_date") or request.form.get("selected_date") or date.today().strftime("%Y-%m-%d")
    selected_week = request.values.get("selected_week") or request.form.get("selected_week") or f"{date.today().isocalendar().year}-W{date.today().isocalendar().week:02d}"
    selected_month = request.values.get("selected_month") or request.form.get("selected_month") or date.today().strftime("%Y-%m")
    message = None

    expense_categories = EXPENSE_CATEGORIES
    scope_context = get_expense_scope_context(scope_type, selected_date=selected_date, selected_week=selected_week, selected_month=selected_month)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "save_expenses":

            print("===== SAVE BUTTON CLICKED =====")
            print(request.form)

            for category in expense_categories:

                print("CATEGORY:", category)

                raw_amount = request.form.get(f"amount_{category}")
                print("RAW AMOUNT:", raw_amount)

                amount = float(raw_amount or 0.0)
                note = request.form.get(f"note_{category}", "").strip()

                print("AMOUNT:", amount)

                if amount > 0:
                    print("Saving...", category)

                    ok = save_monthly_expense(
                        selected_month,
                        category,
                        amount,
                        note,
                        scope_type=scope_type,
                        period_key=scope_context["period_key"],
                    )

                    print("SAVE RESULT:", ok)

            message = f"{scope_type.title()} expenses saved successfully."
        elif action == "clear_scope":
            if USE_MYSQL:
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "DELETE FROM monthly_expenses WHERE scope_type = %s AND period_key = %s",
                        (scope_type, scope_context["period_key"])
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    message = f"All {scope_type} expenses were cleared."
                except mysql.connector.Error as err:
                    print("MySQL clear monthly expenses error:", err)
                    message = "Error clearing expenses."

    current_expenses = fetch_monthly_expenses(selected_month, scope_type=scope_type, period_key=scope_context["period_key"])
    total_expense = sum(item['amount'] for item in current_expenses.values())

    return render_template(
        "monthly_expenses.html",
        active="expenses",
        scope_type=scope_type,
        selected_date=selected_date,
        selected_week=selected_week,
        selected_month=selected_month,
        expense_categories=expense_categories,
        current_expenses=current_expenses,
        total_expense=total_expense,
        message=message,
        scope_context=scope_context,
    )


@app.route("/production-history")
@login_required
def production_history():
    """Show saved production plan history with date filtering and print support."""
    selected_plan_date = request.args.get("plan_date", "")
    plan_sessions = fetch_production_plan_sessions()
    if not selected_plan_date and plan_sessions:
        selected_plan_date = plan_sessions[0]["value"]

    plan_rows = fetch_production_plan_rows(selected_plan_date) if selected_plan_date else []
    history_grid = build_production_history_grid(plan_rows)
    selected_session_label = next(
        (session["label"] for session in plan_sessions if session["value"] == selected_plan_date), ""
    )

    filter_context = production_filter_context(request.args)
    try:
        production_rows = get_filtered_production_rows(filter_context)
    except mysql.connector.Error:
        abort(503)
    summary = production_log_summary(production_rows)

    return render_template(
        "production_history.html",
        active="plan",
        production_rows=production_rows,
        plan_sessions=plan_sessions,
        selected_plan_date=selected_plan_date,
        selected_session_label=selected_session_label,
        summary=summary,
        **filter_context,
    )


@app.route("/prediction-records")
@login_required
def prediction_records():
    """Show saved prediction history records."""
    filter_date = request.args.get("filter_date", "")
    records = fetch_prediction_records(filter_date=filter_date)
    return render_template(
        "prediction_records.html",
        active="predictions",
        records=records,
        filter_date=filter_date,
    )


@app.route('/debug/sales-filtered')
def debug_sales_filtered():
    """Return filtered sales and counts using querystring filters for troubleshooting."""
    search = request.args.get('search', '')
    filter_date = request.args.get('filter_date', '')
    filter_month = request.args.get('filter_month', '')
    filter_bread = request.args.get('filter_bread', 'All')
    history_period = request.args.get('history_period', 'all')
    # optional: support returning a limited or full result set for printing/debugging
    limit = request.args.get('limit', None)
    full = request.args.get('all', '').lower() in ('1', 'true', 'yes')
    try:
        filtered = filter_sales(SALES_DATA, search, filter_date, filter_month, filter_bread, history_period)
        # determine how many records to include in the response
        if full:
            out_list = filtered
        elif limit:
            try:
                n = int(limit)
                out_list = filtered[:n]
            except Exception:
                out_list = filtered[:50]
        else:
            out_list = filtered[:50]

        return jsonify({
            'filtered_count': len(filtered),
            'sample_count': len(out_list),
            'sample': out_list,
            'applied': {
                'search': search,
                'filter_date': filter_date,
                'filter_month': filter_month,
                'filter_bread': filter_bread,
                'history_period': history_period,
            }
        })
    except Exception as err:
        return jsonify({'error': str(err)}), 500


@app.route("/model-performance", methods=["GET", "POST"])
@login_required
def model_performance():
    """Show ML model metrics and support retraining the model from sales data."""
    global MODEL_METRICS
    if request.method == "POST" and request.form.get("action") == "retrain":
        retrain_model_async(SALES_DATA)
    
    # Try to load latest metrics from database
    db_metrics = fetch_latest_model_performance()
    if db_metrics:
        MODEL_METRICS = db_metrics
    
    # Swap the displayed order/values for Holiday and Bread Type as requested
    feature_weights = [
        {"feature": "Bread Type", "weight": 62},
        {"feature": "Peak Day", "weight": 51},
        {"feature": "Weekend", "weight": 38},
        {"feature": "Temperature", "weight": 24},
        {"feature": "Holiday", "weight": 18},
    ]
    return render_template(
        "model_performance.html",
        active="model",
        metrics=MODEL_METRICS,
        feature_weights=feature_weights,
    )


@app.route("/about")
@login_required
def about():
    """Render the about page for the application."""
    return render_template("about.html", active="about")


# --- Ingredients inventory helpers and routes ---
def fetch_ingredients_from_db():
    """Fetch ingredients from the database (or fallback to in-memory list)."""
    if not USE_MYSQL:
        return [ingredient for ingredient in INGREDIENTS if ingredient.get("is_active", 1)]
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT id, name, current_stock, unit, reorder_threshold, last_restock
               FROM ingredients WHERE is_active = 1 ORDER BY name"""
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        # Normalize numeric/date types for JSON rendering
        for r in rows:
            try:
                r["current_stock"] = float(r["current_stock"]) if r.get("current_stock") is not None else 0
            except Exception:
                r["current_stock"] = 0
            try:
                r["reorder_threshold"] = float(r["reorder_threshold"]) if r.get("reorder_threshold") is not None else 0
            except Exception:
                r["reorder_threshold"] = 0
            if isinstance(r.get("last_restock"), datetime):
                r["last_restock"] = r["last_restock"].isoformat()
        return rows
    except mysql.connector.Error as err:
        print("MySQL fetch ingredients error:", err)
        return []


def has_any_ingredient_rows():
    """Prevent deleted inventory from being recreated by default seeding."""
    if not USE_MYSQL:
        return bool(INGREDIENTS)
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM ingredients LIMIT 1")
        return cursor.fetchone() is not None
    except mysql.connector.Error:
        logger.exception("Could not check whether ingredients exist")
        return True
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def insert_ingredient_history(cursor, ingredient_id, name, action_type, quantity_changed,
                              unit, stock_before, stock_after, remarks=None):
    cursor.execute(
        """INSERT INTO ingredient_history (
               ingredient_id, ingredient_name, action_type, quantity_changed,
               unit, stock_before, stock_after, remarks
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            ingredient_id,
            name,
            action_type,
            quantity_changed,
            unit or "",
            stock_before,
            stock_after,
            remarks or None,
        ),
    )


def append_in_memory_ingredient_history(ingredient, action_type, quantity_changed,
                                        stock_before, stock_after, remarks=None):
    INGREDIENT_HISTORY.append({
        "id": len(INGREDIENT_HISTORY) + 1,
        "ingredient_id": ingredient.get("id"),
        "ingredient_name": ingredient["name"],
        "action_type": action_type,
        "quantity_changed": float(quantity_changed),
        "unit": ingredient.get("unit") or "",
        "stock_before": float(stock_before),
        "stock_after": float(stock_after),
        "remarks": remarks or "",
        "created_at": datetime.now(MANILA_TZ),
    })


def ingredient_history_date_bounds(range_name, start_value="", end_value=""):
    local_today = datetime.now(MANILA_TZ).date()
    if range_name == "today":
        start_day = local_today
        end_day = local_today + timedelta(days=1)
    elif range_name == "yesterday":
        start_day = local_today - timedelta(days=1)
        end_day = local_today
    elif range_name == "week":
        start_day = local_today - timedelta(days=local_today.weekday())
        end_day = start_day + timedelta(days=7)
    elif range_name == "month":
        start_day = local_today.replace(day=1)
        end_day = (start_day.replace(day=28) + timedelta(days=4)).replace(day=1)
    elif range_name == "year":
        start_day = local_today.replace(month=1, day=1)
        end_day = local_today.replace(year=local_today.year + 1, month=1, day=1)
    elif range_name == "all":
        start_day = date(2000, 1, 1)
        end_day = local_today + timedelta(days=1)
    elif range_name == "custom":
        try:
            start_day = date.fromisoformat(start_value)
            selected_end = date.fromisoformat(end_value)
        except (TypeError, ValueError):
            raise ValueError("Choose valid start and end dates for the custom range.")
        if start_day > selected_end:
            raise ValueError("The custom start date must be on or before the end date.")
        end_day = selected_end + timedelta(days=1)
    else:
        raise ValueError("Choose a valid ingredient history date range.")

    start_at = datetime.combine(start_day, datetime.min.time())
    end_at = datetime.combine(end_day, datetime.min.time())
    return start_at, end_at


def fetch_ingredient_history(ingredient_id, range_name, start_value, end_value, page):
    start_at, end_at = ingredient_history_date_bounds(range_name, start_value, end_value)
    offset = (page - 1) * INGREDIENT_HISTORY_PAGE_SIZE

    if not USE_MYSQL:
        selected = []
        for row in INGREDIENT_HISTORY:
            created_at = row["created_at"]
            if created_at.tzinfo:
                created_at = created_at.astimezone(MANILA_TZ).replace(tzinfo=None)
            if not (start_at <= created_at < end_at):
                continue
            if ingredient_id is not None and row.get("ingredient_id") != ingredient_id:
                continue
            selected.append(row)
        selected.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)

        return {
            "rows": selected[offset:offset + INGREDIENT_HISTORY_PAGE_SIZE],
            "total_rows": len(selected),
            "start_at": start_at,
            "end_at": end_at,
        }

    conn = None
    cursor = None
    where_parts = ["created_at >= %s", "created_at < %s"]
    params = [start_at, end_at]
    if ingredient_id is not None:
        where_parts.append("ingredient_id = %s")
        params.append(ingredient_id)
    where_sql = " AND ".join(where_parts)
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SET time_zone = '+08:00'")
        cursor.execute(
            f"""SELECT id, ingredient_id, ingredient_name, action_type, quantity_changed,
                       unit, stock_before, stock_after, remarks, created_at
                FROM ingredient_history
                WHERE {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s""",
            tuple(params + [INGREDIENT_HISTORY_PAGE_SIZE, offset]),
        )
        history_rows = cursor.fetchall()
        cursor.execute(
            f"SELECT COUNT(*) AS total_rows FROM ingredient_history WHERE {where_sql}",
            tuple(params),
        )
        total_rows = cursor.fetchone()["total_rows"]
        return {
            "rows": history_rows,
            "total_rows": total_rows,
            "start_at": start_at,
            "end_at": end_at,
        }
    except mysql.connector.Error:
        logger.exception("Could not load ingredient history")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/ingredients/add", methods=["POST"])
@login_required
def add_ingredient():
    """Add an ingredient to the database-backed inventory."""
    global INGREDIENTS

    payload = request.get_json(silent=True)
    if payload is not None and not isinstance(payload, dict):
        return jsonify({"success": False, "error": "Invalid ingredient data."}), 400
    data = payload if payload is not None else request.form
    raw_name = data.get("name", "")
    raw_unit = data.get("unit", "")
    if not isinstance(raw_name, str) or not isinstance(raw_unit, str):
        return jsonify({"success": False, "error": "Ingredient name and unit must be text."}), 400
    name = " ".join(raw_name.split())
    unit = " ".join(raw_unit.split())
    if not name:
        return jsonify({"success": False, "error": "Enter an ingredient name."}), 400
    if len(name) > 200:
        return jsonify({"success": False, "error": "Ingredient names must be 200 characters or fewer."}), 400
    if len(unit) > 50:
        return jsonify({"success": False, "error": "Units must be 50 characters or fewer."}), 400

    try:
        current_stock = float(data.get("current_stock") or 0)
        reorder_threshold = float(data.get("reorder_threshold") or 0)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Enter valid stock and reorder threshold values."}), 400

    max_stock_value = 9999999999.9999
    if (
        not math.isfinite(current_stock)
        or not math.isfinite(reorder_threshold)
        or current_stock < 0
        or reorder_threshold < 0
        or current_stock > max_stock_value
        or reorder_threshold > max_stock_value
    ):
        return jsonify({"success": False, "error": "Stock values must be between 0 and 9,999,999,999.9999."}), 400

    ingredient = {
        "name": name,
        "current_stock": round(current_stock, 4),
        "unit": unit,
        "reorder_threshold": round(reorder_threshold, 4),
    }

    if USE_MYSQL:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SET time_zone = '+08:00'")
            cursor.execute(
                "SELECT id FROM ingredients WHERE LOWER(name) = LOWER(%s) AND is_active = 1 LIMIT 1",
                (name,),
            )
            if cursor.fetchone():
                return jsonify({"success": False, "error": "That ingredient already exists."}), 409

            cursor.execute(
                "INSERT INTO ingredients (name, current_stock, unit, reorder_threshold) VALUES (%s, %s, %s, %s)",
                (name, ingredient["current_stock"], unit, ingredient["reorder_threshold"]),
            )
            ingredient["id"] = cursor.lastrowid
            initial_stock = Decimal(str(ingredient["current_stock"])).quantize(STOCK_QUANTUM)
            insert_ingredient_history(
                cursor,
                ingredient["id"],
                name,
                "INITIAL",
                initial_stock,
                unit,
                Decimal("0"),
                initial_stock,
                "Initial stock",
            )
            conn.commit()
        except mysql.connector.Error:
            if conn:
                conn.rollback()
            logger.exception("Could not add ingredient to inventory")
            return jsonify({"success": False, "error": "Could not save this ingredient. Please try again."}), 503
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    else:
        if any(
            existing.get("is_active", 1)
            and str(existing.get("name", "")).casefold() == name.casefold()
            for existing in INGREDIENTS
        ):
            return jsonify({"success": False, "error": "That ingredient already exists."}), 409
        ingredient["id"] = max((int(existing.get("id", 0)) for existing in INGREDIENTS), default=0) + 1
        ingredient["last_restock"] = None
        INGREDIENTS.append(ingredient)
        append_in_memory_ingredient_history(
            ingredient,
            "INITIAL",
            ingredient["current_stock"],
            0,
            ingredient["current_stock"],
            "Initial stock",
        )

    return jsonify({"success": True, "ingredient": ingredient}), 201


def seed_default_ingredients_to_db():
    """Insert DEFAULT_INGREDIENTS into the ingredients table (or in-memory list).
    Returns (True, inserted_count) on success or (False, error_message) on failure.
    """
    inserted = 0
    if USE_MYSQL:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SET time_zone = '+08:00'")
            cursor.execute("SELECT name FROM ingredients WHERE is_active = 1")
            existing = {row[0].casefold() for row in cursor.fetchall()}
            for ing in DEFAULT_INGREDIENTS:
                name = ing.get('name')
                if name.casefold() in existing:
                    continue
                cursor.execute(
                    "INSERT INTO ingredients (name, current_stock, unit, reorder_threshold) VALUES (%s, %s, %s, %s)",
                    (name, ing.get('current_stock', 0), ing.get('unit', ''), ing.get('reorder_threshold', 0))
                )
                initial_stock = Decimal(str(ing.get('current_stock', 0))).quantize(STOCK_QUANTUM)
                insert_ingredient_history(
                    cursor,
                    cursor.lastrowid,
                    name,
                    "INITIAL",
                    initial_stock,
                    ing.get("unit", ""),
                    Decimal("0"),
                    initial_stock,
                    "Initial stock seeded",
                )
                existing.add(name.casefold())
                inserted += 1
            conn.commit()
            return True, inserted
        except mysql.connector.Error as err:
            if conn:
                conn.rollback()
            print('MySQL seed error:', err)
            return False, str(err)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    else:
        global INGREDIENTS
        max_id = max([int(i.get('id', 0)) for i in INGREDIENTS], default=0) if INGREDIENTS else 0
        for ing in DEFAULT_INGREDIENTS:
            if any(
                i.get("is_active", 1) and i.get("name") == ing.get("name")
                for i in INGREDIENTS
            ):
                continue
            max_id += 1
            row = {
                'id': max_id,
                'name': ing.get('name'),
                'current_stock': float(ing.get('current_stock', 0)),
                'unit': ing.get('unit', ''),
                'reorder_threshold': float(ing.get('reorder_threshold', 0)),
                'last_restock': None,
            }
            INGREDIENTS.append(row)
            append_in_memory_ingredient_history(
                row,
                "INITIAL",
                row["current_stock"],
                0,
                row["current_stock"],
                "Initial stock seeded",
            )
            inserted += 1
        return True, inserted


@app.route("/ingredients", methods=["GET", "POST"]) 
@login_required
def ingredients_inventory():
    """Render the Ingredients Inventory page with summary cards and allow saving ingredient expenses."""
    ingredients = fetch_ingredients_from_db()
    # Auto-seed default ingredients when the DB/list is empty so inline adjustments work
    if not ingredients and DEFAULT_INGREDIENTS and not has_any_ingredient_rows():
        ok, result = seed_default_ingredients_to_db()
        if ok:
            inserted = result
            if inserted:
                ingredients = fetch_ingredients_from_db()
                message = f"Seeded {inserted} default ingredient(s)."
        else:
            # result contains error message
            message = f"Failed to seed default ingredients: {result}"
    message = None

    scope_type = (request.values.get("scope_type") or request.form.get("scope_type") or "monthly").strip().lower()
    selected_date = request.values.get("selected_date") or request.form.get("selected_date") or date.today().strftime("%Y-%m-%d")
    selected_week = request.values.get("selected_week") or request.form.get("selected_week") or f"{date.today().isocalendar().year}-W{date.today().isocalendar().week:02d}"
    selected_month = request.values.get("selected_month") or request.form.get("selected_month") or date.today().strftime("%Y-%m")
    scope_context = get_expense_scope_context(scope_type, selected_date=selected_date, selected_week=selected_week, selected_month=selected_month)
    expense_categories = list(EXPENSE_CATEGORIES)
    expense_category_names = {category.casefold() for category in expense_categories}
    for ingredient in ingredients:
        ingredient_name = ingredient.get("name")
        if isinstance(ingredient_name, str) and ingredient_name.strip():
            if ingredient_name.casefold() not in expense_category_names:
                expense_categories.append(ingredient_name)
                expense_category_names.add(ingredient_name.casefold())

    if request.method == "POST":
        ensure_monthly_expenses_table_schema()
        action = request.form.get("action")
        if action == "save_expenses":
            for category in expense_categories:
                try:
                    amount = float(request.form.get(f"amount_{category}", 0.0) or 0.0)
                except Exception:
                    amount = 0.0
                note = (
                    request.form.get(f"note_{category}")
                    or request.form.get(f"unit_{category}")
                    or ""
                ).strip()
                if amount > 0:
                    ok = save_monthly_expense(
                        selected_month,
                        category,
                        amount,
                        note,
                        scope_type=scope_type,
                        period_key=scope_context["period_key"],
                    )
                    if not ok:
                        message = f"Error saving {category}: database error."
                        break
            message = f"{scope_type.title()} expenses saved successfully."
        elif action == "clear_expenses":
            if not USE_MYSQL:
                MONTHLY_EXPENSES.pop((scope_type, scope_context["period_key"]), None)
                message = f"All {scope_type} expenses were cleared."
            elif USE_MYSQL:
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "DELETE FROM monthly_expenses WHERE scope_type = %s AND period_key = %s",
                        (scope_type, scope_context["period_key"]),
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    message = f"All {scope_type} expenses were cleared."
                except mysql.connector.Error as err:
                    print("MySQL clear monthly expenses error:", err)
                    message = "Error clearing expenses."

    total_ingredients = len(ingredients)
    low_stock_count = sum(1 for i in ingredients if float(i.get("current_stock", 0)) <= float(i.get("reorder_threshold", 0)))
    category_units = {
        ingredient.get("name"): ingredient.get("unit") or "kg"
        for ingredient in ingredients
        if ingredient.get("name")
    }
    for default_ingredient in DEFAULT_INGREDIENTS:
        category_units.setdefault(default_ingredient.get("name"), default_ingredient.get("unit") or "kg")
    last_restock_dt = None
    for i in ingredients:
        if i.get("last_restock"):
            d = parse_datetime(i.get("last_restock"))
            if d and (last_restock_dt is None or d > last_restock_dt):
                last_restock_dt = d
    last_restock_label = format_date(last_restock_dt) if last_restock_dt else ""

    current_expenses = fetch_monthly_expenses(
        selected_month,
        scope_type=scope_type,
        period_key=scope_context["period_key"],
    )
    monthly_expense = fetch_monthly_expense_total(
        selected_month,
        scope_type=scope_type,
        period_key=scope_context["period_key"],
    )
    expense_scope = scope_context["period_key"]

    # Fetch expense history for the selected scope
    expense_history = []
    if not USE_MYSQL:
        # For in-memory storage, build history from MONTHLY_EXPENSES
        bucket = MONTHLY_EXPENSES.get((scope_type, scope_context["period_key"]), {})
        for category, entry in bucket.items():
            expense_history.append({
                "scope_type": scope_type,
                "period_key": scope_context["period_key"],
                "category": category,
                "amount": float(entry.get("amount", 0.0)),
                "note": entry.get("note", ""),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
    elif USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """SELECT scope_type, period_key, category, amount, note, created_at 
                   FROM monthly_expenses 
                   WHERE scope_type = %s AND period_key = %s 
                   ORDER BY created_at DESC""",
                (scope_type, scope_context["period_key"])
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            # Convert datetime objects to strings for template rendering
            for row in rows:
                if row.get('created_at'):
                    if isinstance(row['created_at'], datetime):
                        row['created_at'] = row['created_at'].strftime("%Y-%m-%d %H:%M:%S")
            expense_history = rows
        except mysql.connector.Error as err:
            print("MySQL fetch expense history error:", err)
            expense_history = []

    history_ingredient = request.args.get("ingredient", "all").strip()
    if history_ingredient in ("", "all"):
        history_ingredient_id = None
        history_ingredient = "all"
    else:
        try:
            history_ingredient_id = int(history_ingredient)
            if history_ingredient_id < 1:
                raise ValueError
        except ValueError:
            abort(400, description="Choose a valid ingredient for the history filter.")

    history_range = request.args.get("range", "today").strip().lower()
    history_start = request.args.get("start", "").strip()
    history_end = request.args.get("end", "").strip()
    try:
        history_page = max(1, int(request.args.get("history_page", "1")))
    except ValueError:
        abort(400, description="History page must be a positive number.")
    try:
        history_data = fetch_ingredient_history(
            history_ingredient_id,
            history_range,
            history_start,
            history_end,
            history_page,
        )
    except ValueError as err:
        abort(400, description=str(err))
    except mysql.connector.Error:
        abort(503, description="Ingredient history is temporarily unavailable.")

    history_page_count = max(
        1,
        math.ceil(history_data["total_rows"] / INGREDIENT_HISTORY_PAGE_SIZE),
    )
    history_page = min(history_page, history_page_count)
    if history_page != max(1, int(request.args.get("history_page", "1"))):
        history_data = fetch_ingredient_history(
            history_ingredient_id,
            history_range,
            history_start,
            history_end,
            history_page,
        )
    history_ingredient_name = next(
        (
            ingredient.get("name", "")
            for ingredient in ingredients
            if history_ingredient_id is not None
            and int(ingredient.get("id", 0)) == history_ingredient_id
        ),
        "",
    )
    if history_ingredient_id is not None and not history_ingredient_name:
        history_ingredient_name = next(
            (
                row.get("ingredient_name", "")
                for row in history_data["rows"]
                if int(row.get("ingredient_id", 0)) == history_ingredient_id
            ),
            "",
        )

    return render_template(
        "ingredients_inventory.html",
        active="ingredients",
        ingredients=ingredients,
        total_ingredients=total_ingredients,
        low_stock_count=low_stock_count,
        last_restock_label=last_restock_label,
        default_ingredients=DEFAULT_INGREDIENTS,
        expense_categories=expense_categories,
        current_expenses=current_expenses,
        selected_date=selected_date,
        selected_week=selected_week,
        selected_month=selected_month,
        scope_type=scope_type,
        scope_context=scope_context,
        monthly_expense=monthly_expense,
        expense_scope=expense_scope,
        expense_history=expense_history,
        category_units=category_units,
        ingredient_history=history_data["rows"],
        ingredient_history_total=history_data["total_rows"],
        ingredient_history_page=history_page,
        ingredient_history_page_count=history_page_count,
        ingredient_history_page_size=INGREDIENT_HISTORY_PAGE_SIZE,
        ingredient_history_ingredient=history_ingredient,
        ingredient_history_ingredient_name=history_ingredient_name,
        ingredient_history_range=history_range,
        ingredient_history_start=history_start,
        ingredient_history_end=history_end,
        message=message,
    )


@app.route('/ingredients/seed-default', methods=['POST'])
@login_required
def seed_default_ingredients():
    """Seed the ingredients table (or in-memory list) with DEFAULT_INGREDIENTS.
    Returns JSON with the number of inserted rows.
    """
    ok, result = seed_default_ingredients_to_db()
    if not ok:
        logger.error("Could not seed default ingredients: %s", result)
        return jsonify({'success': False, 'error': 'Could not seed default ingredients.'}), 500
    return jsonify({'success': True, 'inserted': result})


@app.route("/ingredients/delete/<int:ingredient_id>", methods=["POST"])
@login_required
def delete_ingredient(ingredient_id):
    """Soft-delete an ingredient while preserving its stock and history."""
    submitted_token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected_token = session.get("_csrf_token", "")
    if (
        not submitted_token
        or not expected_token
        or not secrets.compare_digest(submitted_token, expected_token)
    ):
        return jsonify({
            "success": False,
            "error": "Your session expired. Refresh the page and try again.",
        }), 400

    if USE_MYSQL:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, name FROM ingredients WHERE id = %s AND is_active = 1 FOR UPDATE",
                (ingredient_id,),
            )
            ingredient = cursor.fetchone()
            if not ingredient:
                conn.rollback()
                return jsonify({"success": False, "error": "Ingredient not found."}), 404
            cursor.execute(
                "UPDATE ingredients SET is_active = 0 WHERE id = %s AND is_active = 1",
                (ingredient_id,),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return jsonify({"success": False, "error": "Ingredient was already deleted."}), 404
            conn.commit()
            return jsonify({
                "success": True,
                "ingredient_id": ingredient_id,
                "name": ingredient["name"],
                "message": f"{ingredient['name']} deleted",
            })
        except mysql.connector.Error:
            if conn:
                conn.rollback()
            logger.exception("Could not delete ingredient %s", ingredient_id)
            return jsonify({
                "success": False,
                "error": "Could not delete this ingredient. Please try again.",
            }), 503
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    for ingredient in INGREDIENTS:
        if int(ingredient.get("id", 0)) == ingredient_id and ingredient.get("is_active", 1):
            ingredient["is_active"] = 0
            return jsonify({
                "success": True,
                "ingredient_id": ingredient_id,
                "name": ingredient["name"],
                "message": f"{ingredient['name']} deleted",
            })
    return jsonify({"success": False, "error": "Ingredient not found."}), 404


@app.route('/api/ingredients/<int:ingredient_id>/adjust', methods=['POST'])
@login_required
def adjust_ingredient(ingredient_id):
    """Adjust the stock level for an ingredient (stock-in or stock-out).
    Expects JSON: { amount: number, type: 'in'|'out', note?: string }
    """
    data = request.get_json(silent=True) or request.form or {}
    try:
        amount = Decimal(str(data.get('amount', 0))).quantize(
            STOCK_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid amount'}), 400
    if not amount.is_finite() or amount <= 0:
        return jsonify({'success': False, 'error': 'Enter a valid positive quantity.'}), 400
    if amount > Decimal("9999999999.9999"):
        return jsonify({'success': False, 'error': 'Quantity exceeds the supported stock limit.'}), 400
    ttype = str(data.get('type') or 'in').strip().lower()
    if ttype not in ("in", "out"):
        return jsonify({'success': False, 'error': 'Choose Add stock or Deduct stock.'}), 400
    note = str(data.get('note', '') or '').strip()

    if USE_MYSQL:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SET time_zone = '+08:00'")
            cursor.execute(
                """SELECT id, name, current_stock, unit, reorder_threshold, last_restock
                   FROM ingredients WHERE id = %s AND is_active = 1 FOR UPDATE""",
                (ingredient_id,),
            )
            row = cursor.fetchone()
            if not row:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Ingredient not found.'}), 404

            current_stock = Decimal(str(row.get('current_stock') or 0)).quantize(STOCK_QUANTUM)
            if ttype == "out" and amount > current_stock:
                conn.rollback()
                return jsonify({
                    'success': False,
                    'error': f"Cannot deduct {amount:.4f} {row.get('unit') or ''}; only {current_stock:.4f} {row.get('unit') or ''} is in stock.".strip(),
                }), 400

            change_amount = amount if ttype == "in" else -amount
            new_stock = current_stock + change_amount
            if new_stock > Decimal("9999999999.9999"):
                conn.rollback()
                return jsonify({'success': False, 'error': 'The resulting stock exceeds the supported limit.'}), 400
            cursor.execute(
                "UPDATE ingredients SET current_stock = %s, last_restock = CASE WHEN %s = 'in' THEN %s ELSE last_restock END WHERE id = %s",
                (new_stock, ttype, datetime.now(MANILA_TZ).replace(tzinfo=None), ingredient_id),
            )
            cursor.execute(
                "INSERT INTO ingredient_transactions (ingredient_id, change_amount, transaction_type, note) VALUES (%s, %s, %s, %s)",
                (ingredient_id, change_amount, ttype, note),
            )
            insert_ingredient_history(
                cursor,
                ingredient_id,
                row["name"],
                "ADD" if ttype == "in" else "DEDUCT",
                change_amount,
                row.get("unit") or "",
                current_stock,
                new_stock,
                note,
            )
            conn.commit()
            cursor.execute(
                """SELECT id, name, current_stock, unit, reorder_threshold, last_restock
                   FROM ingredients WHERE id = %s AND is_active = 1""",
                (ingredient_id,),
            )
            row = cursor.fetchone()
            row["current_stock"] = float(row.get("current_stock") or 0)
            row["reorder_threshold"] = float(row.get("reorder_threshold") or 0)
            if isinstance(row.get("last_restock"), datetime):
                row["last_restock"] = row["last_restock"].isoformat()
            return jsonify({'success': True, 'ingredient': row})
        except mysql.connector.Error as err:
            if conn:
                conn.rollback()
            logger.exception("MySQL ingredient adjustment failed")
            return jsonify({'success': False, 'error': 'Could not update stock. Please try again.'}), 500
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    else:
        # in-memory fallback
        for ing in INGREDIENTS:
            if int(ing.get('id', 0)) == int(ingredient_id) and ing.get("is_active", 1):
                current_stock = Decimal(str(ing.get("current_stock", 0))).quantize(STOCK_QUANTUM)
                if ttype == "out" and amount > current_stock:
                    return jsonify({
                        'success': False,
                        'error': f"Cannot deduct {amount:.4f} {ing.get('unit') or ''}; only {current_stock:.4f} {ing.get('unit') or ''} is in stock.".strip(),
                    }), 400
                change_amount = amount if ttype == "in" else -amount
                ing['current_stock'] = float(current_stock + change_amount)
                if ttype == 'in':
                    ing['last_restock'] = datetime.now().isoformat()
                append_in_memory_ingredient_history(
                    ing,
                    "ADD" if ttype == "in" else "DEDUCT",
                    change_amount,
                    current_stock,
                    ing["current_stock"],
                    note,
                )
                return jsonify({'success': True, 'ingredient': ing})
        return jsonify({'success': False, 'error': 'Ingredient not found'}), 404


def create_app():
    # Ensure DB/tables exist, clean duplicates, and populate module-level caches
    global SALES_DATA, WEEKLY_PLAN, MODEL_METRICS
    init_db()
    for bread_type, default_price in load_custom_bread_types().items():
        if not any(existing.casefold() == bread_type.casefold() for existing in BREAD_TYPES):
            BREAD_TYPES.append(bread_type)
        existing_bread_type = next(
            existing for existing in BREAD_TYPES if existing.casefold() == bread_type.casefold()
        )
        BREAD_PRICES[existing_bread_type] = default_price
    SALES_DATA = load_sales_data()
    WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
    db_metrics = fetch_latest_model_performance()
    if db_metrics:
        MODEL_METRICS = db_metrics
    else:
        MODEL_METRICS = retrain_model(SALES_DATA)

    # Keep compatibility with older /prediction_system URLs while serving the
    # app from the site root by default.
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, COMPAT_PREFIX)
    return app


if __name__ == "__main__":
    create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8501)), debug=app.config.get("DEBUG", True))


application = create_app()
