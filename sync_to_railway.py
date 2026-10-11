import os
import sys
import mysql.connector

LOCAL = dict(host="127.0.0.1", port=3306, user="root",
             password="", database="prediction_system")
REMOTE = dict(host=os.environ["R_HOST"], port=int(os.environ["R_PORT"]),
              user="root", password=os.environ["R_PASS"],
              database="railway", connect_timeout=30)
BATCH = 2000
INCREMENTAL = {"ingredient_transactions", "ingredient_history", "raw_sales_uploads"}
FULL = "--full" in sys.argv
NUM = {"tinyint", "smallint", "mediumint", "int", "bigint",
       "decimal", "numeric", "float", "double"}
TXT = {"char", "varchar", "tinytext", "text", "mediumtext", "longtext"}


def txt(v):
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


def meta(conn, table):
    cur = conn.cursor(buffered=True)
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    rows = cur.fetchall()
    cur.close()
    return {txt(r[0]): r for r in rows}


def pk_col(conn, table):
    cur = conn.cursor(buffered=True)
    cur.execute(f"SHOW KEYS FROM `{table}` WHERE Key_name='PRIMARY'")
    rows = cur.fetchall()
    cur.close()
    return txt(rows[0][4]) if len(rows) == 1 else None


try:
    src = mysql.connector.connect(**LOCAL)
    dst = mysql.connector.connect(**REMOTE)
except Exception:
    print("Cannot reach Railway. Check the internet and try again later.")
    print("Local data is safe and unchanged.")
    raise SystemExit(1)
print("Connected to both databases", flush=True)

dc = dst.cursor(buffered=True)
dc.execute("SET FOREIGN_KEY_CHECKS=0")
dc.execute("SET SESSION innodb_lock_wait_timeout=120")

mc = src.cursor(buffered=True)
mc.execute("SHOW TABLES")
tables = [txt(r[0]) for r in mc.fetchall()]
mc.close()
failed = False

for table in tables:
    try:
        try:
            rmeta = meta(dst, table)
        except Exception:
            if table not in {
                "ingredients",
                "ingredient_history",
                "bread_type_catalog",
                "production_batches",
                "sales_batch_allocations",
                "sales_data_recovery_state",
            }:
                print(f"skipped {table}: not on Railway", flush=True)
                continue
            schema_cursor = src.cursor(buffered=True)
            try:
                schema_cursor.execute(f"SHOW CREATE TABLE `{table}`")
                create_statement = schema_cursor.fetchone()[1]
            finally:
                schema_cursor.close()
            dc.execute(create_statement)
            dst.commit()
            rmeta = meta(dst, table)
        if table == "ingredients" and "is_active" not in rmeta:
            dc.execute(
                "ALTER TABLE `ingredients` ADD COLUMN `is_active` TINYINT(1) NOT NULL DEFAULT 1"
            )
            dst.commit()
            rmeta = meta(dst, table)
        if table == "bread_type_catalog" and "is_active" not in rmeta:
            dc.execute(
                "ALTER TABLE `bread_type_catalog` ADD COLUMN `is_active` TINYINT(1) NOT NULL DEFAULT 1"
            )
            dst.commit()
            rmeta = meta(dst, table)
        if table == "bread_type_catalog" and "shelf_life_days" not in rmeta:
            dc.execute(
                "ALTER TABLE `bread_type_catalog` ADD COLUMN `shelf_life_days` INT NOT NULL DEFAULT 5"
            )
            dst.commit()
            rmeta = meta(dst, table)
        if table in {"sales_data", "raw_sales_uploads"} and "is_historical" not in rmeta:
            dc.execute(
                f"ALTER TABLE `{table}` ADD COLUMN `is_historical` TINYINT(1) NOT NULL DEFAULT 0"
            )
            dst.commit()
            rmeta = meta(dst, table)
    except Exception as err:
        failed = True
        print(f"FAILED to prepare {table} on Railway: {err}", flush=True)
        continue
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
            except Exception:
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
            count += len(rows)
            if incremental:
                dst.commit()
            print(f"  {table}: {count} rows so far", flush=True)
        dst.commit()
        kind = "new rows" if incremental else "rows"
        print(f"{table}: {count} {kind}", flush=True)
    except Exception as e:
        dst.rollback()
        failed = True
        print(f"FAILED {table}: {e}", flush=True)
    finally:
        rc.close()

dc.execute("SET FOREIGN_KEY_CHECKS=1")
print("Sync finished", flush=True)
raise SystemExit(2 if failed else 0)