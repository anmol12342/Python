import pymongo

# Connect to local MongoDB
client = pymongo.MongoClient("mongodb://localhost:27017/")
db = client["ai_agent_db"]

# Drop existing collections for a fresh start
db.users.drop()
db.orders.drop()
db.products.drop()
db.reviews.drop()

# Insert Users
users_data = [
    {"_id": 1, "name": "Anmol", "age": 25, "city": "Delhi"},
    {"_id": 2, "name": "Rahul", "age": 30, "city": "Mumbai"},
    {"_id": 3, "name": "Sneha", "age": 22, "city": "Delhi"}
]
db.users.insert_many(users_data)

# Insert Products (NEW)
products_data = [
    {"_id": 201, "name": "Laptop", "category": "Electronics", "price": 1200.0},
    {"_id": 202, "name": "Smartphone", "category": "Electronics", "price": 800.0},
    {"_id": 203, "name": "Desk Chair", "category": "Furniture", "price": 150.0}
]
db.products.insert_many(products_data)

# Insert Orders (Updated to include product_id)
orders_data = [
    {"_id": 101, "user_id": 1, "product_id": 201, "amount": 1200.0, "created_at": "2024-01-01"},
    {"_id": 102, "user_id": 1, "product_id": 203, "amount": 150.0, "created_at": "2024-01-02"},
    {"_id": 103, "user_id": 2, "product_id": 202, "amount": 800.0, "created_at": "2024-01-03"}
]
db.orders.insert_many(orders_data)

# Insert Reviews (NEW)
reviews_data = [
    {"_id": 301, "user_id": 1, "product_id": 201, "rating": 5, "comment": "Excellent laptop!"},
    {"_id": 302, "user_id": 2, "product_id": 202, "rating": 4, "comment": "Good phone, battery life is okay."}
]
db.reviews.insert_many(reviews_data)

print("MongoDB database created and populated with 4 collections!")