import io
from datetime import datetime

import pandas as pd
import pytest

from app import parse_csv_upload


class DummyFile:
    def __init__(self, content, filename="upload.csv"):
        self.stream = io.BytesIO(content.encode("utf-8"))
        self.filename = filename


def test_parse_csv_upload_aggregates_duplicates():
    csv = """date,bread_type,Products_Produced,Actual_Qty_Sold,Waste_Returns,Price_Per_Product,Expense_Amount
2026-06-01,Pandesal,100,60,10,10.0,50
2026-06-01,Pandesal,80,40,5,12.0,30
2026-06-01,Cheese Bread,50,30,3,20.0,20
"""

    dummy = DummyFile(csv, filename="test.csv")
    records = parse_csv_upload(dummy)

    # Expect two aggregated records: Pandesal and Cheese Bread
    assert isinstance(records, list)
    assert len(records) == 2

    by_key = {(r["date"], r["bread_type"]): r for r in records}

    p = by_key.get(("2026-06-01", "Pandesal"))
    assert p is not None
    assert p["products_produced"] == 180
    assert p["actual_qty_sold"] == 100
    assert p["waste_returns"] == 15
    assert round(p["expense_amount"], 2) == 80.0
    # Weighted price: (60*10 + 40*12) / 100 = 10.8
    assert float(p["price_per_product"]) == 10.8

    c = by_key.get(("2026-06-01", "Cheese Bread"))
    assert c is not None
    assert c["products_produced"] == 50
    assert c["actual_qty_sold"] == 30
    assert c["price_per_product"] == 20.0


def test_parse_excel_upload_xlsx_file():
    df = pd.DataFrame([
        {
            "date": "2026-06-02",
            "bread_type": "Cheese Bread",
            "Products_Produced": 40,
            "Actual_Qty_Sold": 20,
            "Waste_Returns": 2,
            "Price_Per_Product": 18.0,
            "Expense_Amount": 15.0,
        }
    ])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buffer.seek(0)

    class ExcelDummyFile(DummyFile):
        def __init__(self, bytes_content, filename="upload.xlsx"):
            self.stream = io.BytesIO(bytes_content)
            self.filename = filename

    dummy = ExcelDummyFile(buffer.read())
    records = parse_csv_upload(dummy)

    assert isinstance(records, list)
    assert len(records) == 1
    record = records[0]
    assert record["date"] == "2026-06-02"
    assert record["bread_type"] == "Cheese Bread"
    assert record["products_produced"] == 40
    assert record["actual_qty_sold"] == 20
    assert record["waste_returns"] == 2
    assert float(record["price_per_product"]) == 18.0
    assert float(record["expense_amount"]) == 15.0


def test_parse_csv_upload_uses_next_valid_date_column():
    csv = """production_date,sale_date,day_of_week,is_baking_day,bread_type,actual_qty_sold,waste_returns,products_produced,pcs_per_plate,price_per_product
not-a-date,10/10/2026,Saturday,1,Pandesal,8,1,10,12,4.50
"""

    records = parse_csv_upload(DummyFile(csv, filename="test.csv"))

    assert len(records) == 1
    assert records[0]["date"] == "2026-10-10"
    assert records[0]["bread_type"] == "Pandesal"


@pytest.mark.parametrize(
    ("header", "raw_date", "expected"),
    [
        ("Date", " 2026-10-11 ", "2026-10-11"),
        ("date", "10/11/2026", "2026-10-11"),
        ("DATE", "31/10/2026", "2026-10-31"),
        (" Date ", '"Oct 11, 2026"', "2026-10-11"),
    ],
)
def test_parse_csv_upload_accepts_supported_date_formats(header, raw_date, expected):
    csv_text = f"{header},bread_type,actual_qty_sold\n{raw_date},Pandesal,8\n"

    records = parse_csv_upload(DummyFile(csv_text, filename="dates.csv"))

    assert len(records) == 1
    assert records[0]["date"] == expected


def test_parse_csv_upload_skips_blank_rows_and_reports_invalid_date_source_row():
    csv_text = (
        "date,bread_type,actual_qty_sold\n"
        "\n"
        "not-a-date,Pandesal,8\n"
    )

    with pytest.raises(ValueError, match=r"Record 3.*'not-a-date'"):
        parse_csv_upload(DummyFile(csv_text, filename="dates.csv"))


def test_parse_csv_upload_skips_fully_blank_rows():
    csv_text = (
        "date,bread_type,actual_qty_sold\n"
        ",,,\n"
        "2026-10-11,Pandesal,8\n"
        "   ,  ,  \n"
    )

    records = parse_csv_upload(DummyFile(csv_text, filename="dates.csv"))

    assert len(records) == 1
    assert records[0]["date"] == "2026-10-11"


def test_parse_excel_upload_accepts_datetime_cell():
    df = pd.DataFrame([
        {
            "DATE": datetime(2026, 10, 11),
            "bread_type": "Pandesal",
            "actual_qty_sold": 8,
        }
    ])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buffer.seek(0)

    class ExcelDummyFile(DummyFile):
        def __init__(self, bytes_content, filename="upload.xlsx"):
            self.stream = io.BytesIO(bytes_content)
            self.filename = filename

    records = parse_csv_upload(ExcelDummyFile(buffer.read()))

    assert len(records) == 1
    assert records[0]["date"] == "2026-10-11"


def test_parse_csv_upload_reads_tab_delimited_rows():
    content = (
        "production_id\tsale_date\tday_of_week\tis_baking_day\tbread_type\t"
        "actual_qty_sold\twaste_returns\tproducts_produced\n"
        "1\t1/1/2026\tThursday\t1\tPandesal\t8\t1\t10\n"
    )

    records = parse_csv_upload(DummyFile(content, filename="test.csv"))

    assert len(records) == 1
    assert records[0]["date"] == "2026-01-01"
    assert records[0]["bread_type"] == "Pandesal"
    assert records[0]["actual_qty_sold"] == 8
