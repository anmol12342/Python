import sqlite3

conn = sqlite3.connect("test.db")
cursor = conn.cursor()

# Create tables
cursor.execute("""
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    age INTEGER,
    city TEXT
)
""")

cursor.execute("""
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    amount REAL,
    created_at TEXT
)
""")

# Insert sample data
cursor.executemany("INSERT INTO users (name, age, city) VALUES (?, ?, ?)", [
    ("Anmol", 25, "Delhi"),
    ("Rahul", 30, "Mumbai"),
    ("Sneha", 22, "Delhi")
])

cursor.executemany("INSERT INTO orders (user_id, amount, created_at) VALUES (?, ?, ?)", [
    (1, 500.0, "2024-01-01"),
    (1, 200.0, "2024-01-02"),
    (2, 1000.0, "2024-01-03")
])

conn.commit()
conn.close()

print("Database created!")