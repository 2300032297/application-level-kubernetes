"""Versioned order application and explicit, lossless 1.0 -> 2.0 converter."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import random
import sqlite3

VERSIONS = {"1.0": 1, "2.0": 2}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


def connect(path, readonly=True):
    path = Path(path).resolve()
    if readonly:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15,factory=ClosingConnection)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=15,factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def inspect(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (1, 2):
        raise ValueError(f"Unsupported application schema: {version}")
    required = {"customers": {"id", "name", "email", "city"},
                "products": {"id", "sku", "name", "price_minor"},
                "order_items": {"id", "order_id", "product_id", "quantity", "unit_price_minor"},
                "orders": {"id", "customer_email", "total_cents", "status", "created_at"} if version == 1 else
                          {"id", "customer_id", "total_minor", "currency", "status", "created_at"}}
    for table, columns in required.items():
        actual = {x[1] for x in db.execute(f"PRAGMA table_info({table})")}
        if actual != columns:
            raise ValueError(f"Schema fingerprint mismatch: {table}")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Database integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Broken foreign-key reference")
    if version == 1 and db.execute("SELECT 1 FROM orders o LEFT JOIN customers c ON c.email=o.customer_email WHERE c.id IS NULL LIMIT 1").fetchone():
        raise ValueError("Unresolved customer reference")
    if version == 2 and db.execute("SELECT 1 FROM orders WHERE currency!='INR' LIMIT 1").fetchone():
        raise ValueError("Unsupported currency in version 2")
    total = "total_cents" if version == 1 else "total_minor"
    if db.execute(f"SELECT 1 FROM orders o LEFT JOIN order_items i ON i.order_id=o.id GROUP BY o.id HAVING o.{total} != COALESCE(SUM(i.quantity*i.unit_price_minor),0) LIMIT 1").fetchone():
        raise ValueError("Order totals do not equal line-item totals")
    if db.execute("SELECT 1 FROM order_items WHERE quantity<=0 OR unit_price_minor<0 LIMIT 1").fetchone():
        raise ValueError("Invalid order-item amount")
    return f"{version}.0"


def canonical_rows(db):
    version = inspect(db)
    queries = {"customers": "SELECT id,name,email,city FROM customers ORDER BY id",
               "products": "SELECT id,sku,name,price_minor FROM products ORDER BY id",
               "orders": ("SELECT id,customer_email,total_cents AS total_minor,'INR' AS currency,status,created_at FROM orders ORDER BY id" if version == "1.0" else
                          "SELECT o.id,c.email AS customer_email,o.total_minor,o.currency,o.status,o.created_at FROM orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id"),
               "order_items": "SELECT id,order_id,product_id,quantity,unit_price_minor FROM order_items ORDER BY id"}
    return {table: [dict(r) for r in db.execute(query)] for table, query in queries.items()}


def summary(path):
    with connect(path) as db:
        version = inspect(db)
        rows = canonical_rows(db)
        hashes = {k: hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest() for k, v in rows.items()}
        return {"application_version": version, "schema_version": VERSIONS[version],
                "counts": {k: len(v) for k, v in rows.items()}, "table_hashes": hashes,
                "semantic_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
                "total_minor": sum(r["total_minor"] for r in rows["orders"]), "currency": "INR",
                "bytes": Path(path).stat().st_size, "integrity": "ok"}


def seed(path, orders=5000, seed_value=101):
    if not 1 <= orders <= 100000:
        raise ValueError("Choose between 1 and 100000 orders")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("Refusing to overwrite an existing database")
    rng = random.Random(seed_value)
    cities = ["Hyderabad", "Vijayawada", "Bengaluru", "Chennai", "Pune", "Delhi", "Mumbai", "Kolkata"]
    names = ["Aarav", "Ananya", "Diya", "Kabir", "Meera", "Rohan", "Sana", "Vihaan"]
    categories = ["Notebook", "Keyboard", "Monitor", "Headset", "Desk lamp", "USB hub", "Webcam", "Backpack"]
    with connect(path, False) as db:
        db.executescript("""
        CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,city TEXT NOT NULL);
        CREATE TABLE products(id INTEGER PRIMARY KEY,sku TEXT UNIQUE NOT NULL,name TEXT NOT NULL,price_minor INTEGER NOT NULL CHECK(price_minor>=0));
        CREATE TABLE orders(id INTEGER PRIMARY KEY,customer_email TEXT NOT NULL REFERENCES customers(email),total_cents INTEGER NOT NULL CHECK(total_cents>=0),status TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE order_items(id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES orders(id),product_id INTEGER NOT NULL REFERENCES products(id),quantity INTEGER NOT NULL CHECK(quantity>0),unit_price_minor INTEGER NOT NULL CHECK(unit_price_minor>=0));
        PRAGMA user_version=1;
        """)
        nc = max(20, min(2000, orders // 10))
        db.executemany("INSERT INTO customers VALUES(?,?,?,?)", [(i, f"{names[i%8]} Customer {i:04}", f"customer{i:04}@example.test", cities[i%8]) for i in range(1, nc+1)])
        products = [(i, f"SKU-{i:04}", f"{categories[i%8]} {i:03}", rng.randint(15000, 850000)) for i in range(1, 81)]
        db.executemany("INSERT INTO products VALUES(?,?,?,?)", products)
        for i in range(1, orders+1):
            items = [(rng.choice(products), rng.randint(1, 5)) for _ in range(rng.randint(1, 5))]
            total = sum(p[3]*q for p,q in items)
            db.execute("INSERT INTO orders VALUES(?,?,?,?,?)", (i, f"customer{rng.randint(1,nc):04}@example.test", total,
                       ["pending","paid","shipped","delivered","cancelled"][i%5], f"2026-{1+(i%10):02}-{1+(i%28):02}T{(i%24):02}:00:00Z"))
            db.executemany("INSERT INTO order_items(order_id,product_id,quantity,unit_price_minor) VALUES(?,?,?,?)", [(i,p[0],q,p[3]) for p,q in items])
    return summary(path)


def compatibility(source, target):
    if source not in VERSIONS or target not in VERSIONS:
        raise ValueError("Unsupported application version; supported versions are 1.0 and 2.0")
    if source == "2.0" and target == "1.0":
        raise ValueError("Downgrade 2.0 -> 1.0 has no approved converter")
    return {"source_version": source, "target_version": target, "compatible": True,
            "converter": "identity" if source == target else "orders-v1-to-v2.1",
            "changes": [] if source == target else ["Resolve customer email to a foreign-key customer ID", "Rename total_cents to total_minor without rounding", "Add explicit INR currency", "Preserve every customer, product, order and line item"]}


def convert(path, target):
    with connect(path, False) as db:
        source = inspect(db)
        plan = compatibility(source, target)
        if source == target:
            return plan
        # SQLite DDL participates in this transaction; failed conversion rolls back.
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE orders_v2(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL REFERENCES customers(id),total_minor INTEGER NOT NULL CHECK(total_minor>=0),currency TEXT NOT NULL CHECK(currency='INR'),status TEXT NOT NULL,created_at TEXT NOT NULL)")
        db.execute("INSERT INTO orders_v2 SELECT o.id,c.id,o.total_cents,'INR',o.status,o.created_at FROM orders o JOIN customers c ON c.email=o.customer_email")
        # Rebuild both related tables together to preserve foreign-key semantics.
        db.execute("CREATE TABLE items_v2(id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES orders_v2(id),product_id INTEGER NOT NULL REFERENCES products(id),quantity INTEGER NOT NULL CHECK(quantity>0),unit_price_minor INTEGER NOT NULL CHECK(unit_price_minor>=0))")
        db.execute("INSERT INTO items_v2 SELECT * FROM order_items")
        db.execute("DROP TABLE order_items")
        db.execute("DROP TABLE orders")
        db.execute("ALTER TABLE orders_v2 RENAME TO orders")
        db.execute("ALTER TABLE items_v2 RENAME TO order_items")
        db.execute("PRAGMA user_version=2")
        inspect(db)
    return plan


def snapshot(source, destination):
    with connect(source) as src, connect(destination, False) as dst:
        inspect(src)
        src.backup(dst)
    return summary(destination)


def records(path, table="orders", search="", page=1, size=20):
    if table not in {"orders", "customers", "products", "order_items"}:
        raise ValueError("Unknown table")
    page, size = max(1, int(page)), min(100, max(1, int(size)))
    with connect(path) as db:
        version = inspect(db)
        base = ("SELECT o.id,c.name AS customer,c.email,o.total_cents AS total_minor,'INR' AS currency,o.status,o.created_at FROM orders o JOIN customers c ON c.email=o.customer_email" if version == "1.0" else
                "SELECT o.id,c.name AS customer,c.email,o.total_minor,o.currency,o.status,o.created_at FROM orders o JOIN customers c ON c.id=o.customer_id") if table == "orders" else f"SELECT * FROM {table}"
        cols = [r[0] for r in db.execute(base+" LIMIT 0").description]
        where = " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in cols)
        query = f"SELECT * FROM ({base}) WHERE ({where})"
        params = ["%"+search+"%"]*len(cols)
        count = db.execute(f"SELECT COUNT(*) FROM ({query})", params).fetchone()[0]
        rows = [dict(r) for r in db.execute(query+" ORDER BY id LIMIT ? OFFSET ?",params+[size,(page-1)*size])]
        return {"rows":rows,"total":count,"page":page,"size":size,"columns":cols,"application_version":version}
