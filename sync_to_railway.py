import os
import sys
import time
import mysql.connector
from mysql.connector import errors

LOCAL = dict(host="127.0.0.1", port=3306, user="root",
             password="", database="prediction_system")
REMOTE = dict(host=os.environ["R_HOST"], port=int(os.environ["R_PORT"]),
              user="root", password=os.environ["R_PASS"],
              database="railway", connect_timeout=60)
BATCH = 500
RETRIES = 4
INCREMENTAL = {"ingredient_transactions", "ingredient_history", "raw_sales_uploads"}
CREATABLE = {
    "ingredients",
    "ingredient_history",
    "bread_type_catalog",
    "production_batches",
    "sales_batch_allocations",
    "sales_data_recovery_state",
}
ADD_COLS = [
    ("ingredients", "is_active", "TINYINT(1) NOT NULL DEFAULT 1"),
    ("bread_type_catalog", "is_active", "TINYINT(1) NOT NULL DEFAULT 1"),
    ("bread_type_catalog", "shelf_life_days", "INT NOT NULL DEFAULT 5"),
    ("sales_data", "is_historical", "TINYINT(1) NOT NULL DEFAULT 0"),
    ("raw_sales_uploads", "is_historical", "TINYINT(1) NOT NULL DEFAULT 0"),
]
FULL = "--full" in sys.argv
NUM = {"tinyint", "smallint", "mediumint", "int", "bigint",
       "decimal", "numeric", "float", "double"}
TXT = {"char", "varchar", "tinytext", "text", "mediumtext", "longtext"}


def txt(v):
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


def meta(conn, table):
    cur = conn.cursor(buffered=True)
    try:
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        rows = cur.fetchall()
    finally:
        cur.close()
    return {txt(r[0]): r for r in rows}


def pk_col(conn, table):
    cur = conn.cursor(buffered=True)
    try:
        cur.execute(f"SHOW KEYS FROM `{table}` WHERE Key_name='PRIMARY'")
        rows = cur.fetchall()
    finally:
        cur.close()
    return txt(rows[0][4]) if len(rows) == 1 else None


def connect_remote():
    conn = mysql.connector.connect(**REMOTE)
    c = conn.cursor(buffered=True)
    c.execute("SET FOREIGN_KEY_CHECKS=0")
    c.execute("SET SESSION innodb_lock_wait_timeout=120")
    c.execute("SET SESSION net_write_timeout=600")
    c.execute("SET SESSION net_read_timeout=600")
    c.execute("SET SESSION wait_timeout=600")
    c.close()
    return conn


def sync_table(table):
    global dst, dc
    # 1. make sure the table exists on Railway
    try:
        rmeta = meta(dst, table)
    except errors.ProgrammingError:  # table does not exist
        if table not in CREATABLE:
            print(f"skipped {table}: not on Railway", flush=True)
            return
        sc = src.cursor(buffered=True)
        try:
            sc.execute(f"SHOW CREATE TABLE `{table}`")
            create_statement = sc.fetchone()[1]
        finally:
            sc.close()
        dc.execute(create_statement)
        dst.commit()
        rmeta = meta(dst, table)

    # 2. add columns that are missing on Railway
    for t, col, ddl in ADD_COLS:
        if table == t and col not in rmeta:
            dc.execute(f"ALTER TABLE `{t}` ADD COLUMN `{col}` {ddl}")
            dst.commit()
            rmeta = meta(dst, table)

    lmeta = meta(src, table)
    common = [c for c in lmeta if c in rmeta]

    defaults = {}
    for i, c in enumerate(common):
        if txt(rmeta[c][2]) == "NO":
            base = txt(rmeta[c][1]).lower().split("(")[0].split()[0]
            if base in NUM:
                defaults[i] = 0
            elif base in TXT:
                defaults[i] = ""

    names = ",".join(f"`{c}`" for c in common)
    marks = ",".join(["%s"] * len(common))
    rc = src.cursor(buffered=True)
    try:
        pk = pk_col(src, table) if table in INCREMENTAL and not FULL else None
        incremental = bool(pk and pk in common and
                           "int" in txt(lmeta[pk][1]).lower())
        params = ()
        query = f"SELECT {names} FROM `{table}`"
        if incremental:
            dc.execute(f"SELECT COALESCE(MAX(`{pk}`),0) FROM `{table}`")
            last = dc.fetchone()[0]
            query += f" WHERE `{pk}` > %s ORDER BY `{pk}`"
            params = (last,)
        else:
            try:
                dc.execute(f"TRUNCATE TABLE `{table}`")
            except errors.DatabaseError as e:
                if e.errno in (2006, 2013, 2055):  # connection lost
                    raise
                dc.execute(f"DELETE FROM `{table}`")

        rc.execute(query, params)
        count = 0
        while True:
            rows = rc.fetchmany(BATCH)
            if not rows:
                break
            if defaults:
                rows = [tuple(defaults.get(i, v) if v is None else v
                              for i, v in enumerate(row))
                        for row in rows]
            dc.executemany(
                f"INSERT INTO `{table}` ({names}) VALUES ({marks})", rows)
            dst.commit()  # commit every batch
            count += len(rows)
            print(f"  {table}: {count} rows so far", flush=True)
        kind = "new rows" if incremental else "rows"
        print(f"{table}: {count} {kind}", flush=True)
    finally:
        rc.close()


try:
    src = mysql.connector.connect(**LOCAL)
    dst = connect_remote()
except Exception:
    print("Cannot reach Railway. Check the internet and try again later.")
    print("Local data is safe and unchanged.")
    raise SystemExit(1)
print("Connected to both databases", flush=True)

dc = dst.cursor(buffered=True)

mc = src.cursor(buffered=True)
mc.execute("SHOW TABLES")
tables = [txt(r[0]) for r in mc.fetchall()]
mc.close()
failed = False

for table in tables:
    done = False
    for attempt in range(1, RETRIES + 1):
        try:
            sync_table(table)
            done = True
            break
        except Exception as e:
            print(f"{table}: attempt {attempt}/{RETRIES} failed: {e}", flush=True)
            try:
                dst.close()
            except Exception:
                pass
            time.sleep(5)
            try:
                dst = connect_remote()
                dc = dst.cursor(buffered=True)
            except Exception as e2:
                print(f"  could not reconnect: {e2}", flush=True)
    if not done:
        failed = True
        print(f"FAILED {table} after {RETRIES} attempts", flush=True)

try:
    dc.execute("SET FOREIGN_KEY_CHECKS=1")
except Exception:
    pass
print("Sync finished" + (" WITH ERRORS" if failed else " OK"), flush=True)
raise SystemExit(2 if failed else 0)