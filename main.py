from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import gemini_embedding_search as gemini_embedding_demo
import os
import pymongo
import json
import re
import math
import hashlib
import datetime
import sqlite3
import urllib.request
import urllib.parse
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("No API key found.")
genai.configure(api_key=api_key)

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATED_PAGES_DIR = os.path.join(BASE_DIR, "generated_pages")
GENERATED_PAGE_ASSETS_DIR = os.path.join(GENERATED_PAGES_DIR, "assets")
MCP_SIGNUP_DB_PATH = os.path.join(BASE_DIR, "mcp_signup.db")
USER_DATABASES_DIR = os.path.join(BASE_DIR, "user_databases")
CUSTOMER_SUPPORT_DB_PATH = os.path.join(BASE_DIR, "customer_support.db")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Prompt(BaseModel):
    text: str


class RagQuestion(BaseModel):
    question: str


class GeminiEmbeddingQueryRequest(BaseModel):
    question: str


class ExcelQuestion(BaseModel):
    question: str
    sheet_data: str | None = None


class ApiTestRequest(BaseModel):
    api_url: str | None = None
    prompt: str | None = None


class SocialPostRequest(BaseModel):
    topic: str
    audience: str | None = None
    tone: str | None = None


class SimpleChatRequest(BaseModel):
    message: str
    history: list[dict] | None = None


class McpSignupRequest(BaseModel):
    name: str
    email: str
    password: str

class DbFieldSpec(BaseModel):
    name: str
    type: str


class DbBuilderRequest(BaseModel):
    db_type: str
    db_name: str
    use_case: str | None = None
    entity_name: str
    fields: list[DbFieldSpec]
    sample: dict


class DbBuilderChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str


class SupportHandoffRequest(BaseModel):
    order_number: str | None = None
    customer_name: str | None = None
    issue: str
    transcript: list[dict] | None = None


class SupportHumanMessageRequest(BaseModel):
    ticket_id: str
    message: str
    sender: str | None = "customer"


class SupportOrderActionRequest(BaseModel):
    ticket_id: str | None = None
    note: str | None = None


class SupportTicketUpdateRequest(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    priority: str | None = None
    resolution: str | None = None
    internal_note: str | None = None


DB_BUILDER_CONVERSATIONS: dict[str, dict] = {}
GEMINI_EMBEDDING_STORE_CACHE: list[dict] | None = None

SUPPORT_KNOWLEDGE_BASE = [
    {
        "title": "Late order handling",
        "text": (
            "If a food delivery order is delayed, the support representative should first apologize, confirm the order ID, "
            "check the live delivery status, and tell the customer the latest ETA. If the rider is near the restaurant, "
            "explain that food pickup is still pending. If the order is already picked up, explain that the rider is on the way. "
            "If the delay is significant, offer a support escalation and mention that compensation such as a coupon or partial refund "
            "may be considered depending on the final delay and order condition."
        ),
    },
    {
        "title": "Cancellation policy",
        "text": (
            "Customers can usually cancel an order only before the restaurant starts preparing it. Once preparation has started "
            "or the rider has been assigned, cancellation may be blocked or only a partial refund may be available. "
            "Support should explain the status clearly, avoid promising a full refund too early, and suggest waiting for delivery "
            "if the food is already close to dispatch."
        ),
    },
    {
        "title": "Refunds and missing items",
        "text": (
            "If an order arrives with missing, spilled, damaged, or incorrect items, the support bot should ask for a short description "
            "and request a photo when useful. The resolution can include a refund for the affected item, full refund in severe cases, "
            "or account credit. Support should tell the customer that refund timelines vary by payment method and that wallet credits are usually faster "
            "than card or bank refunds."
        ),
    },
    {
        "title": "Address changes",
        "text": (
            "Address changes after placing an order are only possible in limited situations. If the restaurant has not prepared the order "
            "and the delivery partner has not picked it up, support may attempt an address update. If the order is already in transit, "
            "the customer should usually contact support quickly so the team can check feasibility, but the change is not guaranteed."
        ),
    },
    {
        "title": "Payment issues",
        "text": (
            "If payment is deducted but the order is not confirmed, support should ask the customer to wait briefly for an automatic refresh. "
            "If the order still does not appear, the payment is usually reversed automatically within the bank timeline. "
            "If the order is confirmed and payment is successful, support should share the order status instead of treating it as a failed payment."
        ),
    },
    {
        "title": "Live tracking and rider contact",
        "text": (
            "Customers can use the live tracking screen to check order preparation, pickup, and rider location. "
            "Once the rider is assigned, contact options may be shown in the app. Support should avoid sharing personal numbers directly and "
            "should encourage customers to use in-app call or chat tools for privacy and faster coordination."
        ),
    },
]

SUPPORT_SAMPLE_ORDERS = [
    {
        "order_number": "FD1001",
        "customer_name": "Riya Sharma",
        "restaurant": "Biryani Bowl",
        "items": "Paneer biryani, masala chaas",
        "order_status": "Delayed",
        "issue_status": "Restaurant is still finishing packing; rider is waiting near pickup.",
        "payment_status": "Paid by UPI",
        "refund_status": "Not initiated",
        "rider_status": "Rider assigned and waiting at restaurant",
        "eta": "15 minutes",
        "total_amount": 348.00,
        "delivery_address": "Green Park, Delhi",
        "last_update": "2026-04-07 15:05 IST",
        "support_note": "Apologize, share ETA, monitor delay, and offer escalation if delay crosses 20 minutes.",
    },
    {
        "order_number": "FD1002",
        "customer_name": "Arjun Mehta",
        "restaurant": "Burger House",
        "items": "Classic burger meal, peri peri fries",
        "order_status": "Delivered",
        "issue_status": "Customer reported missing peri peri fries.",
        "payment_status": "Paid by card",
        "refund_status": "Partial refund approved for missing item",
        "rider_status": "Delivered by rider",
        "eta": "Delivered",
        "total_amount": 429.00,
        "delivery_address": "Saket, Delhi",
        "last_update": "2026-04-07 13:40 IST",
        "support_note": "Apologize for missing item, confirm refund for fries, and share card refund timeline.",
    },
    {
        "order_number": "FD1003",
        "customer_name": "Kavya Nair",
        "restaurant": "Dosa Corner",
        "items": "Masala dosa, filter coffee",
        "order_status": "Preparing",
        "issue_status": "Customer asked for cancellation after preparation started.",
        "payment_status": "Cash on delivery",
        "refund_status": "No refund needed",
        "rider_status": "Rider not assigned yet",
        "eta": "22 minutes",
        "total_amount": 215.00,
        "delivery_address": "Lajpat Nagar, Delhi",
        "last_update": "2026-04-07 14:15 IST",
        "support_note": "Explain cancellation may be blocked because preparation started; offer to check feasibility.",
    },
    {
        "order_number": "FD1004",
        "customer_name": "Aman Verma",
        "restaurant": "Pizza Palace",
        "items": "Farmhouse pizza, garlic bread",
        "order_status": "Payment failed",
        "issue_status": "Payment deducted but order was not confirmed.",
        "payment_status": "Deducted, reversal pending",
        "refund_status": "Auto reversal expected within bank timeline",
        "rider_status": "No rider assigned",
        "eta": "No active delivery",
        "total_amount": 612.00,
        "delivery_address": "Dwarka, Delhi",
        "last_update": "2026-04-07 12:25 IST",
        "support_note": "Explain that the order is not active and the deducted amount should reverse automatically.",
    },
    {
        "order_number": "FD1005",
        "customer_name": "Priya Singh",
        "restaurant": "Curry Express",
        "items": "Dal makhani, butter naan",
        "order_status": "In transit",
        "issue_status": "Customer requested address change after pickup.",
        "payment_status": "Paid by wallet",
        "refund_status": "Not initiated",
        "rider_status": "Rider is on the way to original address",
        "eta": "9 minutes",
        "total_amount": 286.00,
        "delivery_address": "Karol Bagh, Delhi",
        "last_update": "2026-04-07 15:20 IST",
        "support_note": "Explain address change is not guaranteed after pickup; route to human support if urgent.",
    },
]


def generate_nlp_summary(user_prompt: str, query_json: str, results):
    """Creates a concise natural-language explanation of the query outcome."""
    if not results:
        return "No matching records were found for this request."

    summary_prompt = f"""
    You are helping explain MongoDB query results to a non-technical user.

    User request:
    {user_prompt}

    Generated MongoDB query:
    {query_json}

    Query results:
    {json.dumps(results, default=str)}

    Write a short, clear natural-language summary with:
    - 2 to 4 sentences
    - the main takeaway
    - any obvious trend, top item, or count if visible
    - no markdown, no bullet points
    """

    try:
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content(summary_prompt)
        return response.text.strip()
    except Exception as exc:
        return f"Summary unavailable: {exc}"

def execute_mongo(query_json_str: str):
    """Parses the AI's JSON and executes it against MongoDB."""
    try:
        # Parse the AI's string output into a Python dictionary
        parsed_query = json.loads(query_json_str)
        collection_name = parsed_query.get("collection")
        pipeline = parsed_query.get("pipeline", [])

        # Connect to DB
        client = pymongo.MongoClient("mongodb://localhost:27017/")
        db = client["ai_agent_db"]
        
        if collection_name not in db.list_collection_names():
             return None, f"Collection '{collection_name}' does not exist."

        # Run the aggregation pipeline
        results = list(db[collection_name].aggregate(pipeline))
        
        # Convert MongoDB Data Types (like ObjectId) to string for JSON serialization
        for doc in results:
            if '_id' in doc:
                doc['_id'] = str(doc['_id'])
                
        return results, None
    except json.JSONDecodeError:
        return None, "AI failed to return valid JSON."
    except Exception as e:
        return None, str(e)


def extract_text_from_static_pdf(pdf_path: str) -> str:
    """Extracts text from a simple static PDF by reading text-drawing operators."""
    with open(pdf_path, "rb") as pdf_file:
        raw_text = pdf_file.read().decode("latin-1", errors="ignore")

    matches = re.findall(r"\((.*?)\)\s*Tj", raw_text, flags=re.DOTALL)
    extracted_lines = []
    for match in matches:
        cleaned = (
            match.replace("\\(", "(")
            .replace("\\)", ")")
            .replace("\\n", " ")
            .replace("\\r", " ")
            .replace("\\t", " ")
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            extracted_lines.append(cleaned)

    return " ".join(extracted_lines)


def sentence_split(text: str):
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def chunk_sentences(sentences, chunk_size: int = 2):
    chunks = []
    for index in range(0, len(sentences), chunk_size):
        chunk_sentences_list = sentences[index:index + chunk_size]
        chunks.append({
            "chunk_id": index // chunk_size + 1,
            "sentences": chunk_sentences_list,
            "text": " ".join(chunk_sentences_list)
        })
    return chunks


def tokenize(text: str):
    return re.findall(r"[a-zA-Z0-9']+", text.lower())


def embed_text(text: str, dimensions: int = 64):
    vector = [0.0] * dimensions
    tokens = tokenize(text)

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % dimensions
        sign = -1.0 if int(digest[8:10], 16) % 2 else 1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]


def cosine_similarity(vec_a, vec_b):
    return sum(left * right for left, right in zip(vec_a, vec_b))


def keyword_overlap_score(question: str, chunk_text: str):
    question_tokens = set(tokenize(question))
    chunk_tokens = set(tokenize(chunk_text))
    if not question_tokens:
        return 0.0
    overlap = question_tokens.intersection(chunk_tokens)
    return len(overlap) / len(question_tokens)


def build_vector_records(chunks):
    records = []
    for chunk in chunks:
        records.append({
            "chunk_id": chunk["chunk_id"],
            "text": chunk["text"],
            "sentences": chunk["sentences"],
            "embedding": embed_text(chunk["text"]),
        })
    return records


def write_vector_store(records):
    store_dir = os.path.join(BASE_DIR, "vector_store")
    os.makedirs(store_dir, exist_ok=True)
    store_path = os.path.join(store_dir, "sample_pdf_vectors.json")
    payload = {
        "database": "local-json-vector-store",
        "record_count": len(records),
        "records": records,
    }
    with open(store_path, "w", encoding="utf-8") as store_file:
        json.dump(payload, store_file, indent=2)
    return store_path


def load_rag_demo():
    pdf_path = os.path.join(BASE_DIR, "sample_rag.pdf")
    text = extract_text_from_static_pdf(pdf_path)
    sentences = sentence_split(text)
    chunks = chunk_sentences(sentences, chunk_size=2)
    records = build_vector_records(chunks)
    store_path = write_vector_store(records)
    return {
        "pdf_path": pdf_path,
        "text": text,
        "sentences": sentences,
        "chunks": chunks,
        "records": records,
        "store_path": store_path,
    }


def load_gemini_embedding_demo():
    global GEMINI_EMBEDDING_STORE_CACHE

    client, types_module, model_name = gemini_embedding_demo.create_gemini_client()
    if GEMINI_EMBEDDING_STORE_CACHE is None:
        GEMINI_EMBEDDING_STORE_CACHE = gemini_embedding_demo.build_embedding_store(
            gemini_embedding_demo.DOCUMENTS,
            client,
            types_module,
            model_name,
        )

    return {
        "client": client,
        "types_module": types_module,
        "model_name": model_name,
        "records": GEMINI_EMBEDDING_STORE_CACHE,
    }


def retrieve_rag_matches(question: str, records, top_k: int = 3):
    question_embedding = embed_text(question)
    scored = []

    for record in records:
        cosine_score = cosine_similarity(question_embedding, record["embedding"])
        keyword_score = keyword_overlap_score(question, record["text"])
        hybrid_score = round((0.75 * cosine_score) + (0.25 * keyword_score), 6)
        scored.append({
            "chunk_id": record["chunk_id"],
            "text": record["text"],
            "cosine_score": round(cosine_score, 6),
            "keyword_score": round(keyword_score, 6),
            "hybrid_score": hybrid_score,
        })

    scored.sort(key=lambda item: item["hybrid_score"], reverse=True)
    return scored[:top_k]


def build_rag_fallback_answer(question: str, matches):
    """Builds a readable answer when the model call is unavailable."""
    if not matches:
        return "I could not find a relevant answer in the PDF for that question."

    question_tokens = set(tokenize(question))
    candidate_sentences = []

    for match in matches:
        for sentence in sentence_split(match["text"]):
            overlap = len(question_tokens.intersection(set(tokenize(sentence))))
            candidate_sentences.append((overlap, sentence, match["hybrid_score"]))

    candidate_sentences.sort(key=lambda item: (item[0], item[2], len(item[1])), reverse=True)
    selected = []
    seen = set()

    for _, sentence, _ in candidate_sentences:
        if sentence not in seen:
            selected.append(sentence)
            seen.add(sentence)
        if len(selected) == 2:
            break

    if not selected:
        selected = [matches[0]["text"]]

    answer = " ".join(selected)
    if answer:
        answer = answer[0].upper() + answer[1:]
    lead_in = "Based on the PDF, "

    lowered_question = question.lower().strip()
    if lowered_question.startswith("what is") or lowered_question.startswith("who is"):
        return f"{lead_in}{answer}"

    return f"{lead_in}{answer}"


def generate_rag_answer(question: str, matches):
    # Keep the RAG page responsive by generating a grounded local answer
    # directly from the retrieved chunks instead of waiting on another model call.
    return build_rag_fallback_answer(question, matches)


def load_support_knowledge_base():
    records = []
    for index, item in enumerate(SUPPORT_KNOWLEDGE_BASE, start=1):
        text = item["text"].strip()
        records.append({
            "chunk_id": index,
            "title": item["title"],
            "text": text,
            "sentences": sentence_split(text),
            "embedding": embed_text(text),
        })
    return records


def ensure_customer_support_db():
    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS support_orders (
                order_number TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                restaurant TEXT NOT NULL,
                items TEXT NOT NULL,
                order_status TEXT NOT NULL,
                issue_status TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                refund_status TEXT NOT NULL,
                rider_status TEXT NOT NULL,
                eta TEXT NOT NULL,
                total_amount REAL NOT NULL,
                delivery_address TEXT NOT NULL,
                last_update TEXT NOT NULL,
                support_note TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS human_handoffs (
                ticket_id TEXT PRIMARY KEY,
                order_number TEXT,
                customer_name TEXT,
                issue TEXT NOT NULL,
                transcript TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_sqlite_column(connection, "human_handoffs", "priority", "TEXT NOT NULL DEFAULT 'Normal'")
        ensure_sqlite_column(connection, "human_handoffs", "assigned_to", "TEXT")
        ensure_sqlite_column(connection, "human_handoffs", "sla_due_at", "TEXT")
        ensure_sqlite_column(connection, "human_handoffs", "updated_at", "TEXT")
        ensure_sqlite_column(connection, "human_handoffs", "resolution", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS human_handoff_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(ticket_id) REFERENCES human_handoffs(ticket_id)
            )
            """
        )

        for order in SUPPORT_SAMPLE_ORDERS:
            connection.execute(
                """
                INSERT OR IGNORE INTO support_orders (
                    order_number, customer_name, restaurant, items, order_status, issue_status,
                    payment_status, refund_status, rider_status, eta, total_amount,
                    delivery_address, last_update, support_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order["order_number"],
                    order["customer_name"],
                    order["restaurant"],
                    order["items"],
                    order["order_status"],
                    order["issue_status"],
                    order["payment_status"],
                    order["refund_status"],
                    order["rider_status"],
                    order["eta"],
                    order["total_amount"],
                    order["delivery_address"],
                    order["last_update"],
                    order["support_note"],
                ),
            )
        connection.commit()


def ensure_sqlite_column(connection, table_name: str, column_name: str, definition: str):
    existing_columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing_columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def support_row_to_dict(row):
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def normalize_order_number(value: str):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def extract_order_number(text: str):
    normalized_text = (text or "").upper()
    prefixed = re.search(r"\b(?:FD|ORD|SWG)[-\s]?\d{3,8}\b", normalized_text)
    if prefixed:
        return normalize_order_number(prefixed.group(0))

    plain = re.search(r"\b\d{4,8}\b", normalized_text)
    if plain:
        return plain.group(0)

    return None


def lookup_support_order(order_number: str | None):
    if not order_number:
        return None

    ensure_customer_support_db()
    normalized = normalize_order_number(order_number)
    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT * FROM support_orders
            WHERE REPLACE(REPLACE(UPPER(order_number), '-', ''), ' ', '') = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None and normalized.isdigit():
            row = connection.execute(
                """
                SELECT * FROM support_orders
                WHERE REPLACE(REPLACE(UPPER(order_number), '-', ''), ' ', '') LIKE ?
                """,
                (f"%{normalized}",),
            ).fetchone()

    return support_row_to_dict(row)


def list_support_demo_orders():
    ensure_customer_support_db()
    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM support_orders
            ORDER BY order_number
            """
        ).fetchall()
    return [support_row_to_dict(row) for row in rows]


def is_order_specific_question(question: str):
    question_text = (question or "").lower()
    keywords = {
        "order", "late", "delay", "delayed", "missing", "refund", "cancel",
        "payment", "deducted", "address", "rider", "delivery", "item", "status"
    }
    return any(keyword in question_text for keyword in keywords)


def build_order_support_answer(question: str, order: dict, matches):
    policy_line = ""
    if matches:
        policy_sentences = sentence_split(matches[0]["text"])
        if policy_sentences:
            policy_line = f" Policy note: {policy_sentences[0]}"

    return (
        f"Order update for {order['order_number']}: customer name {order['customer_name']}, restaurant {order['restaurant']}. "
        f"Backend status: {order['order_status']}. Issue recorded: {order['issue_status']} "
        f"ETA: {order['eta']}. Payment status: {order['payment_status']}. Refund status: {order['refund_status']}. "
        f"Support action: {order['support_note']}{policy_line} You can also choose Chat with support agent to create a human support ticket for this order."
    )


def format_customer_message_for_representative(message: str, order_number: str | None = None):
    cleaned = re.sub(r"\s+", " ", (message or "").strip())
    if not cleaned:
        cleaned = "Customer requested support assistance."

    detected_order = normalize_order_number(order_number or extract_order_number(cleaned) or "")
    lowered = cleaned.lower()

    if "human" in lowered or "representative" in lowered or "agent" in lowered:
        issue_type = "Customer requested human support assistance"
        detail = "Please review the ticket and continue the conversation with the customer."
    elif any(word in lowered for word in ("late", "delay", "delayed", "eta", "where is")):
        issue_type = "Customer requested an update for a delayed order"
        detail = "Customer is asking for the current order status and revised delivery ETA."
    elif any(word in lowered for word in ("missing", "wrong", "incorrect", "spilled", "damaged")):
        issue_type = "Customer reported an item or order quality issue"
        detail = "Customer is asking support to review the affected items and provide an appropriate resolution."
    elif any(word in lowered for word in ("refund", "money", "amount", "charged")):
        issue_type = "Customer requested payment or refund assistance"
        detail = "Customer is asking support to review the payment/refund status and explain the next steps."
    elif any(word in lowered for word in ("payment", "deducted", "upi", "card", "wallet")):
        issue_type = "Customer reported a payment confirmation issue"
        detail = "Customer is asking support to verify whether the payment/order confirmation is successful."
    elif any(word in lowered for word in ("cancel", "cancellation")):
        issue_type = "Customer requested cancellation assistance"
        detail = "Customer is asking support to check whether cancellation is still possible for the order."
    elif "address" in lowered:
        issue_type = "Customer requested delivery address support"
        detail = "Customer is asking support to check whether the delivery address can be updated."
    else:
        issue_type = "Customer provided additional support information"
        detail = "Please review the saved conversation transcript for the complete customer context."

    order_part = f" for order {detected_order}" if detected_order else ""
    return f"{issue_type}{order_part}. {detail}"


def infer_ticket_priority(message: str, order: dict | None = None):
    text = (message or "").lower()
    order_status = (order or {}).get("order_status", "").lower()
    issue_status = (order or {}).get("issue_status", "").lower()

    if any(word in text for word in ("payment deducted", "deducted", "refund", "money", "charged")) or "payment failed" in order_status:
        return "High"
    if any(word in text for word in ("cancel", "address", "wrong address")):
        return "High"
    if any(word in text for word in ("late", "delay", "delayed", "rider")) or "delayed" in order_status or "delayed" in issue_status:
        return "High"
    if any(word in text for word in ("missing", "wrong", "incorrect", "spilled", "damaged")):
        return "Normal"
    return "Normal"


def calculate_sla_due_at(priority: str):
    priority_minutes = {
        "Urgent": 15,
        "High": 30,
        "Normal": 60,
        "Low": 120,
    }
    minutes = priority_minutes.get(priority, 60)
    return (datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds") + "Z"


def normalize_ticket_status(status: str | None):
    cleaned = (status or "").strip().lower().replace(" ", "_")
    allowed = {"waiting_for_representative", "in_progress", "resolved", "closed"}
    if cleaned not in allowed:
        raise ValueError("Invalid ticket status.")
    return cleaned


def normalize_ticket_priority(priority: str | None):
    cleaned = (priority or "").strip().title()
    allowed = {"Low", "Normal", "High", "Urgent"}
    if cleaned not in allowed:
        raise ValueError("Invalid ticket priority.")
    return cleaned


def create_support_handoff(payload: SupportHandoffRequest):
    ensure_customer_support_db()
    cleaned_order_number = normalize_order_number(payload.order_number or "") or None
    order = lookup_support_order(cleaned_order_number) if cleaned_order_number else None
    customer_name = (payload.customer_name or "").strip()
    if not customer_name and order:
        customer_name = order["customer_name"]

    created_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    ticket_id = f"CSR-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{os.urandom(3).hex().upper()}"
    transcript_json = json.dumps(payload.transcript or [], ensure_ascii=True)
    professional_issue = format_customer_message_for_representative(payload.issue, cleaned_order_number)
    priority = infer_ticket_priority(payload.issue, order)
    sla_due_at = calculate_sla_due_at(priority)

    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO human_handoffs (
                ticket_id, order_number, customer_name, issue, transcript, status,
                priority, assigned_to, sla_due_at, updated_at, resolution, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                cleaned_order_number,
                customer_name or None,
                professional_issue,
                transcript_json,
                "waiting_for_representative",
                priority,
                None,
                sla_due_at,
                created_at,
                None,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO human_handoff_messages (ticket_id, sender, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (ticket_id, "customer", professional_issue, created_at),
        )
        connection.commit()

    return {
        "success": True,
        "ticket_id": ticket_id,
        "status": "waiting_for_representative",
        "priority": priority,
        "sla_due_at": sla_due_at,
        "order": order,
        "message": (
            f"Your human support request is created. Ticket {ticket_id} is waiting for a support agent. "
            "You can keep typing here and your messages will be saved to this ticket."
        ),
    }


def add_support_human_message(payload: SupportHumanMessageRequest):
    ensure_customer_support_db()
    ticket_id = (payload.ticket_id or "").strip()
    message = (payload.message or "").strip()
    sender = (payload.sender or "customer").strip().lower()
    if sender not in {"customer", "representative", "internal_note"}:
        sender = "customer"

    if not ticket_id or not message:
        return {"success": False, "error": "Ticket id and message are required."}

    created_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        existing = connection.execute(
            "SELECT ticket_id, order_number FROM human_handoffs WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        if not existing:
            return {"success": False, "error": "Human support ticket not found."}
        if sender == "customer":
            message = format_customer_message_for_representative(message, existing[1])

        connection.execute(
            """
            INSERT INTO human_handoff_messages (ticket_id, sender, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (ticket_id, sender, message, created_at),
        )
        connection.execute(
            "UPDATE human_handoffs SET updated_at = ? WHERE ticket_id = ?",
            (created_at, ticket_id),
        )
        connection.commit()

    return {
        "success": True,
        "ticket_id": ticket_id,
        "message": "Message saved to the human support-agent ticket.",
    }


def update_support_ticket(ticket_id: str, payload: SupportTicketUpdateRequest):
    ensure_customer_support_db()
    ticket_id = (ticket_id or "").strip()
    if not ticket_id:
        return {"success": False, "error": "Ticket id is required."}

    updates = []
    params = []
    message_to_append = None
    updated_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    if payload.status is not None:
        status = normalize_ticket_status(payload.status)
        updates.append("status = ?")
        params.append(status)
        if status == "in_progress":
            message_to_append = "Ticket status updated: support agent is reviewing this case."
        elif status == "resolved":
            message_to_append = "Ticket status updated: this support case has been marked resolved."
        elif status == "closed":
            message_to_append = "Ticket status updated: this support case has been closed."

    if payload.assigned_to is not None:
        assigned_to = payload.assigned_to.strip() or None
        updates.append("assigned_to = ?")
        params.append(assigned_to)
        if assigned_to:
            message_to_append = f"Ticket assigned to support agent {assigned_to}."

    if payload.priority is not None:
        priority = normalize_ticket_priority(payload.priority)
        updates.append("priority = ?")
        params.append(priority)
        updates.append("sla_due_at = ?")
        params.append(calculate_sla_due_at(priority))

    if payload.resolution is not None:
        resolution = payload.resolution.strip() or None
        updates.append("resolution = ?")
        params.append(resolution)

    updates.append("updated_at = ?")
    params.append(updated_at)
    params.append(ticket_id)

    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        existing = connection.execute(
            "SELECT ticket_id FROM human_handoffs WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        if not existing:
            return {"success": False, "error": "Human support ticket not found."}

        connection.execute(
            f"UPDATE human_handoffs SET {', '.join(updates)} WHERE ticket_id = ?",
            tuple(params),
        )

        internal_note = (payload.internal_note or "").strip()
        if internal_note:
            connection.execute(
                """
                INSERT INTO human_handoff_messages (ticket_id, sender, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (ticket_id, "internal_note", internal_note, updated_at),
            )

        if message_to_append:
            connection.execute(
                """
                INSERT INTO human_handoff_messages (ticket_id, sender, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (ticket_id, "representative", message_to_append, updated_at),
            )

        connection.commit()

    return {
        "success": True,
        "ticket": get_support_handoff_messages(ticket_id),
    }


def current_support_timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M IST")


def update_support_order_action(order_number: str, action: str, ticket_id: str | None = None, note: str | None = None):
    ensure_customer_support_db()
    normalized_order_number = normalize_order_number(order_number)
    order = lookup_support_order(normalized_order_number)
    if not order:
        return {"success": False, "error": "Order not found in the support database."}

    action = (action or "").strip().lower()
    timestamp = current_support_timestamp()
    note_text = (note or "").strip()

    if action == "cancel":
        paid_order = "cash on delivery" not in (order.get("payment_status") or "").lower()
        updates = {
            "order_status": "Cancelled",
            "issue_status": "Order cancelled by support agent from representative inbox.",
            "refund_status": "Refund initiated after cancellation" if paid_order else "No refund needed for cash on delivery",
            "rider_status": "Delivery stopped after support cancellation",
            "eta": "Cancelled",
            "last_update": timestamp,
            "support_note": note_text or "Cancellation completed by support agent. Inform the customer that the order has been cancelled and any eligible refund is being processed.",
        }
        action_message = f"Support action completed: order {order['order_number']} was cancelled."
    elif action == "refund":
        updates = {
            "order_status": order["order_status"],
            "issue_status": "Refund initiated by support agent after ticket review.",
            "refund_status": "Refund initiated by support agent",
            "rider_status": order["rider_status"],
            "eta": order["eta"],
            "last_update": timestamp,
            "support_note": note_text or "Refund initiated by support agent. Share the expected refund timeline based on the customer's payment method.",
        }
        action_message = f"Support action completed: refund was initiated for order {order['order_number']}."
    else:
        return {"success": False, "error": "Unsupported action. Use cancel or refund."}

    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        connection.execute(
            """
            UPDATE support_orders
            SET order_status = ?,
                issue_status = ?,
                refund_status = ?,
                rider_status = ?,
                eta = ?,
                last_update = ?,
                support_note = ?
            WHERE REPLACE(REPLACE(UPPER(order_number), '-', ''), ' ', '') = ?
            """,
            (
                updates["order_status"],
                updates["issue_status"],
                updates["refund_status"],
                updates["rider_status"],
                updates["eta"],
                updates["last_update"],
                updates["support_note"],
                normalized_order_number,
            ),
        )

        if ticket_id:
            existing = connection.execute(
                "SELECT ticket_id FROM human_handoffs WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            if existing:
                action_time = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
                connection.execute(
                    """
                    INSERT INTO human_handoff_messages (ticket_id, sender, message, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (ticket_id, "representative", action_message, action_time),
                )
                connection.execute(
                    """
                    UPDATE human_handoffs
                    SET status = ?, updated_at = ?
                    WHERE ticket_id = ? AND status = ?
                    """,
                    ("in_progress", action_time, ticket_id, "waiting_for_representative"),
                )
        connection.commit()

    updated_order = lookup_support_order(normalized_order_number)
    return {
        "success": True,
        "action": action,
        "order": updated_order,
        "message": action_message,
    }


def list_support_handoffs():
    ensure_customer_support_db()
    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        tickets = connection.execute(
            """
            SELECT ticket_id, order_number, customer_name, issue, status, priority,
                   assigned_to, sla_due_at, updated_at, resolution, created_at
            FROM human_handoffs
            ORDER BY created_at DESC
            """
        ).fetchall()
        messages = connection.execute(
            """
            SELECT ticket_id, sender, message, created_at
            FROM human_handoff_messages
            ORDER BY created_at ASC
            """
        ).fetchall()

    grouped_messages = {}
    for message in messages:
        message_dict = support_row_to_dict(message)
        grouped_messages.setdefault(message_dict["ticket_id"], []).append(message_dict)

    result = []
    for ticket in tickets:
        ticket_dict = support_row_to_dict(ticket)
        ticket_dict["messages"] = grouped_messages.get(ticket_dict["ticket_id"], [])
        ticket_dict["order"] = lookup_support_order(ticket_dict.get("order_number"))
        result.append(ticket_dict)
    return result


def get_support_handoff_messages(ticket_id: str):
    ensure_customer_support_db()
    with sqlite3.connect(CUSTOMER_SUPPORT_DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        ticket = connection.execute(
            """
            SELECT ticket_id, order_number, customer_name, issue, status, priority,
                   assigned_to, sla_due_at, updated_at, resolution, created_at
            FROM human_handoffs
            WHERE ticket_id = ?
            """,
            (ticket_id,),
        ).fetchone()
        if not ticket:
            return None

        messages = connection.execute(
            """
            SELECT id, ticket_id, sender, message, created_at
            FROM human_handoff_messages
            WHERE ticket_id = ?
            ORDER BY id ASC
            """,
            (ticket_id,),
        ).fetchall()

    ticket_dict = support_row_to_dict(ticket)
    ticket_dict["messages"] = [support_row_to_dict(message) for message in messages]
    ticket_dict["order"] = lookup_support_order(ticket_dict.get("order_number"))
    return ticket_dict


def build_support_handoff_stats(handoffs):
    stats = {
        "total": len(handoffs),
        "waiting_for_representative": 0,
        "in_progress": 0,
        "resolved": 0,
        "closed": 0,
        "high_priority": 0,
        "unassigned": 0,
    }
    for ticket in handoffs:
        status = ticket.get("status") or "waiting_for_representative"
        if status in stats:
            stats[status] += 1
        if ticket.get("priority") in {"High", "Urgent"}:
            stats["high_priority"] += 1
        if not ticket.get("assigned_to"):
            stats["unassigned"] += 1
    return stats



def build_support_fallback_answer(question: str, matches):
    if not matches:
        return (
            "I could not find the right support policy yet. Please share whether your issue is about a delayed order, "
            "refund, missing item, payment, or address change."
        )

    top_match = matches[0]
    sentences = sentence_split(top_match["text"])
    answer = " ".join(sentences[:2]).strip() or top_match["text"]
    return f"I am here to help with your order. {answer}"


def generate_support_answer(question: str, matches):
    context = "\n\n".join(
        f"[Source {index}] {match['text']}"
        for index, match in enumerate(matches[:3], start=1)
    )
    prompt = f"""
    You are a helpful customer support representative for a food delivery company similar to Swiggy.

    Rules:
    - use only the grounded context
    - sound calm, warm, and practical
    - answer in 3 to 5 sentences
    - do not mention vector search, embeddings, or retrieval
    - if the context is limited, say so honestly and ask one short follow-up question

    Customer question:
    {question}

    Support context:
    {context}
    """

    try:
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content(prompt)
        answer = response.text.strip()
        return answer or build_support_fallback_answer(question, matches)
    except Exception:
        return build_support_fallback_answer(question, matches)


def ensure_mcp_signup_table():
    with sqlite3.connect(MCP_SIGNUP_DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def create_mcp_signup(payload: McpSignupRequest):
    normalized_name = payload.name.strip()
    normalized_email = payload.email.strip().lower()
    hashed_password = hashlib.sha256(payload.password.encode("utf-8")).hexdigest()
    created_at = datetime.datetime.utcnow().isoformat(timespec="seconds")

    ensure_mcp_signup_table()

    with sqlite3.connect(MCP_SIGNUP_DB_PATH) as connection:
        existing = connection.execute(
            "SELECT 1 FROM mcp_users WHERE email = ?",
            (normalized_email,),
        ).fetchone()
        if existing:
            return {"success": False, "error": "This account already exists."}

        connection.execute(
            "INSERT INTO mcp_users (name, email, password, created_at) VALUES (?, ?, ?, ?)",
            (normalized_name, normalized_email, hashed_password, created_at),
        )
        connection.commit()

    return {"success": True, "message": "Account created successfully."}

def ensure_user_databases_dir():
    os.makedirs(USER_DATABASES_DIR, exist_ok=True)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sanitize_identifier(value: str, label: str):
    cleaned = (value or "").strip()
    if not cleaned or not _IDENTIFIER_RE.match(cleaned):
        raise ValueError(f"Invalid {label}. Use letters, numbers, underscore, and do not start with a number.")
    return cleaned


def map_sqlite_type(type_name: str):
    t = (type_name or "").strip().lower()
    if t in {"text", "string", "str"}:
        return "TEXT"
    if t in {"int", "integer"}:
        return "INTEGER"
    if t in {"float", "double", "real", "number"}:
        return "REAL"
    if t in {"bool", "boolean"}:
        return "INTEGER"
    if t in {"date", "datetime", "timestamp"}:
        return "TEXT"
    return "TEXT"


def create_sqlite_database(payload: DbBuilderRequest):
    ensure_user_databases_dir()
    db_name = sanitize_identifier(payload.db_name, "database name")
    table_name = sanitize_identifier(payload.entity_name, "table name")

    columns = []
    field_specs = []
    seen = set()
    for field in payload.fields:
        field_name = sanitize_identifier(field.name, "field name")
        if field_name in seen:
            raise ValueError(f"Duplicate field name: {field_name}")
        seen.add(field_name)
        sqlite_type = map_sqlite_type(field.type)
        columns.append(f"{field_name} {sqlite_type}")
        field_specs.append((field_name, sqlite_type))

    if not field_specs:
        raise ValueError("Provide at least one field.")

    db_path = os.path.join(USER_DATABASES_DIR, f"{db_name}.db")

    with sqlite3.connect(db_path) as connection:
        connection.execute(f"CREATE TABLE IF NOT EXISTS {table_name} (id INTEGER PRIMARY KEY AUTOINCREMENT, {', '.join(columns)})")

        insert_fields = [name for name, _ in field_specs]
        placeholders = ", ".join(["?"] * len(insert_fields))
        values = []
        for field_name, field_type in field_specs:
            raw_value = payload.sample.get(field_name)
            if field_type == "INTEGER":
                if raw_value is None or raw_value == "":
                    values.append(None)
                else:
                    if isinstance(raw_value, str) and raw_value.strip().lower() in {"true", "false", "yes", "no"}:
                        values.append(1 if raw_value.strip().lower() in {"true", "yes"} else 0)
                    else:
                        values.append(int(raw_value))
            elif field_type == "REAL":
                if raw_value is None or raw_value == "":
                    values.append(None)
                else:
                    values.append(float(raw_value))
            else:
                values.append(None if raw_value is None else str(raw_value))

        connection.execute(
            f"INSERT INTO {table_name} ({', '.join(insert_fields)}) VALUES ({placeholders})",
            tuple(values),
        )
        connection.commit()

        row = connection.execute(f"SELECT * FROM {table_name} ORDER BY id DESC LIMIT 1").fetchone()

    return {
        "db_type": "sqlite",
        "db_path": db_path,
        "table": table_name,
        "inserted_row": row,
    }


def create_mongo_database(payload: DbBuilderRequest):
    db_name = sanitize_identifier(payload.db_name, "database name")
    collection_name = sanitize_identifier(payload.entity_name, "collection name")

    field_names = []
    seen = set()
    for field in payload.fields:
        field_name = sanitize_identifier(field.name, "field name")
        if field_name in seen:
            raise ValueError(f"Duplicate field name: {field_name}")
        seen.add(field_name)
        field_names.append(field_name)

    if not field_names:
        raise ValueError("Provide at least one field.")

    document = {name: payload.sample.get(name) for name in field_names}
    if payload.use_case and payload.use_case.strip():
        document["_use_case"] = payload.use_case.strip()

    client = pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2500)
    client.admin.command("ping")
    db = client[db_name]
    collection = db[collection_name]
    result = collection.insert_one(document)

    return {
        "db_type": "mongodb",
        "database": db_name,
        "collection": collection_name,
        "inserted_id": str(result.inserted_id),
        "inserted_document": document,
    }


def create_json_document_store(payload: DbBuilderRequest):
    """
    Local Mongo-style fallback when a MongoDB server is not available.
    Writes a small JSON file under ./user_databases.
    """
    ensure_user_databases_dir()
    db_name = sanitize_identifier(payload.db_name, "database name")
    collection_name = sanitize_identifier(payload.entity_name, "collection name")

    field_names = []
    seen = set()
    for field in payload.fields:
        field_name = sanitize_identifier(field.name, "field name")
        if field_name in seen:
            raise ValueError(f"Duplicate field name: {field_name}")
        seen.add(field_name)
        field_names.append(field_name)

    document = {name: payload.sample.get(name) for name in field_names}
    if payload.use_case and payload.use_case.strip():
        document["_use_case"] = payload.use_case.strip()
    document["_created_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    store_path = os.path.join(USER_DATABASES_DIR, f"{db_name}__{collection_name}.json")
    existing = []
    if os.path.exists(store_path):
        try:
            with open(store_path, "r", encoding="utf-8") as handle:
                existing = json.load(handle) or []
        except Exception:
            existing = []

    existing.append(document)
    with open(store_path, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2, ensure_ascii=True)

    return {
        "db_type": "json-document-store",
        "store_path": store_path,
        "database": db_name,
        "collection": collection_name,
        "inserted_document": document,
    }


def _new_conversation_id():
    return os.urandom(12).hex()


def _suggest_identifier(value: str):
    cleaned = (value or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", cleaned)
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip("_")
    if not cleaned:
        return None
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned if _IDENTIFIER_RE.match(cleaned) else None


def _parse_db_type(message: str):
    m = (message or "").lower()
    if "mongo" in m:
        return "mongodb"
    if "sqlite" in m or "sql lite" in m:
        return "sqlite"
    if "postgres" in m or "postgresql" in m:
        return "sqlite"
    if "mysql" in m:
        return "sqlite"
    return None


def _parse_fields_spec(message: str):
    """
    Accepts: "name:text, price:real, is_paid:boolean"
    Returns: list[{name,type}], errors(list[str])
    """
    raw = (message or "").strip()
    if not raw:
        return [], ["Please enter at least one field."]

    parts = [p.strip() for p in re.split(r"[,\n]+", raw) if p.strip()]
    fields = []
    errors = []
    for part in parts:
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:\-]\s*([A-Za-z0-9_]+)\s*$", part)
        if not match:
            errors.append(f"Could not parse: '{part}'. Use format name:type (example: total:real).")
            continue
        fields.append({"name": match.group(1), "type": match.group(2)})

    if not fields and not errors:
        errors.append("Please enter fields in format name:type, separated by commas.")

    return fields, errors


def _ensure_db_builder_state(conversation_id: str):
    if conversation_id not in DB_BUILDER_CONVERSATIONS:
        DB_BUILDER_CONVERSATIONS[conversation_id] = {
            "stage": "db_type",
            "db_type": None,
            "db_name": None,
            "use_case": None,
            "entity_name": None,
            "fields": [],
            "sample": {},
            "sample_index": 0,
            "updated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
    return DB_BUILDER_CONVERSATIONS[conversation_id]


def _db_builder_next_prompt(state: dict):
    stage = state.get("stage")
    if stage == "db_type":
        return "What kind of database do you want to create? Type 'sqlite' or 'mongodb'."
    if stage == "db_name":
        return "What should we name the database? Use only letters, numbers, and underscore (example: swiggy_support)."
    if stage == "use_case":
        return "What is the use case? Example: 'Food delivery app orders and payments'."
    if stage == "entity_name":
        return "What is the main entity (table/collection) name? Example: orders or customers."
    if stage == "fields":
        return "List the fields for this entity in format name:type, separated by commas. Example: customer_name:text, total:real, created_at:date."
    if stage == "sample":
        fields = state.get("fields") or []
        idx = int(state.get("sample_index") or 0)
        if idx < len(fields):
            field = fields[idx]
            return f"Enter a sample value for '{field['name']}' ({field['type']})."
        return "Creating your database and inserting the sample record..."
    if stage == "done":
        return "Done. Type 'restart' to create another database."
    return "Type 'restart' to start again."


def generate_excel_agent_response(question: str, sheet_data: str | None = None):
    context_block = sheet_data.strip() if sheet_data and sheet_data.strip() else "No worksheet data provided."
    prompt = f"""
    You are an expert Excel assistant. Help the user with Microsoft Excel tasks.

    Your responsibilities:
    - explain formulas clearly
    - suggest Excel functions when useful
    - help with pivots, lookups, charts, conditional formatting, and data cleaning
    - if worksheet data is provided, use it in your explanation
    - keep the answer practical and concise
    - when appropriate, include an Excel formula the user can copy
    - do not use markdown tables

    User question:
    {question}

    Worksheet data or notes:
    {context_block}
    """

    try:
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content(prompt)
        answer = response.text.strip()
    except Exception:
        answer = (
            "I can help with Excel formulas, lookups, pivots, charts, cleaning data, and worksheet logic. "
            "Share the goal or paste sample sheet data and I will suggest the right Excel steps."
        )

    formula_match = re.search(r"=(?:SUM|AVERAGE|COUNT|COUNTA|COUNTIF|COUNTIFS|SUMIF|SUMIFS|IF|IFS|XLOOKUP|VLOOKUP|INDEX|MATCH|FILTER|SORT|UNIQUE|TEXTJOIN|LEFT|RIGHT|MID|DATE|EOMONTH|TODAY|IFERROR)[A-Z0-9_(),:$<>=\"'\-\+\*/\s]*", answer, flags=re.IGNORECASE)
    suggested_formula = formula_match.group(0).strip() if formula_match else None

    return {
        "answer": answer,
        "nlp_result": answer,
        "suggested_formula": suggested_formula,
    }


def fetch_sample_https_api(api_url: str):
    request = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        }
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8", errors="replace")
        content_type = response.headers.get("Content-Type", "")
        status_code = getattr(response, "status", 200)
    return {
        "status_code": status_code,
        "content_type": content_type,
        "body": body,
    }


def generate_api_test_summary(api_url: str, api_result, user_prompt: str | None = None):
    summary_prompt = f"""
    You are validating an HTTPS API test and explaining the result to a developer.

    API URL:
    {api_url}

    Optional user instruction:
    {user_prompt or "No extra instruction."}

    HTTP status:
    {api_result['status_code']}

    Content type:
    {api_result['content_type']}

    Response body:
    {api_result['body'][:3000]}

    Write a short explanation that:
    - confirms whether the API call appears successful
    - summarizes the returned JSON or payload
    - mentions one practical observation
    - uses plain language
    - no markdown bullets
    """

    try:
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content(summary_prompt)
        return response.text.strip()
    except Exception as exc:
        return f"The HTTPS request completed, but Gemini summary generation failed: {exc}"


def build_social_post_fallback(topic: str, audience: str | None = None, tone: str | None = None):
    audience_text = audience.strip() if audience and audience.strip() else "professionals and social media readers"
    tone_text = tone.strip() if tone and tone.strip() else "clear and engaging"
    title = f"{topic.strip().title()}: a concise post idea"
    linkedin_post = (
        f"{topic.strip().title()} matters for {audience_text}. "
        f"This post can highlight why the topic matters now, one practical takeaway, and one action readers can try next. "
        f"Keep the tone {tone_text} and useful so it feels shareable on LinkedIn."
    )
    facebook_post = (
        f"Here is a simple take on {topic.strip()}: why it matters, what people should notice, and one helpful next step. "
        f"Make it friendly, direct, and easy to read for a wider Facebook audience."
    )
    hashtags = [
        f"#{''.join(ch for ch in topic.title() if ch.isalnum())}",
        "#Innovation",
        "#Marketing",
        "#ContentCreation",
        "#ProfessionalGrowth",
    ]
    image_ideas = [
        f"A clean editorial cover image representing {topic.strip()} in a modern professional setting.",
        f"A carousel-style visual with key insights and bold typography about {topic.strip()}.",
    ]
    return {
        "title": title,
        "summary": f"This post package is designed for {audience_text} with a {tone_text} tone.",
        "linkedin_post": linkedin_post,
        "facebook_post": facebook_post,
        "hashtags": hashtags,
        "image_ideas": image_ideas,
    }


def generate_social_post_package(topic: str, audience: str | None = None, tone: str | None = None):
    audience_text = audience.strip() if audience and audience.strip() else "professionals and decision-makers"
    tone_text = tone.strip() if tone and tone.strip() else "professional, warm, and high-impact"
    prompt = f"""
    You are an expert social media strategist and content writer.

    Create a content package for the topic below that can be posted on LinkedIn and Facebook.
    Return STRICTLY valid JSON with these keys:
    "title": string
    "summary": string
    "linkedin_post": string
    "facebook_post": string
    "hashtags": array of strings
    "image_ideas": array of 2 strings

    Requirements:
    - make the information accurate at a general level and practical
    - LinkedIn version should feel insightful and professional
    - Facebook version should feel simpler and more conversational
    - include 5 to 8 relevant hashtags
    - image ideas should describe visuals the user can create or source
    - no markdown fences

    Topic: {topic}
    Target audience: {audience_text}
    Tone: {tone_text}
    """

    try:
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content(prompt)
        raw_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw_text)

        return {
            "title": parsed.get("title") or topic.title(),
            "summary": parsed.get("summary") or f"A social media post package for {topic}.",
            "linkedin_post": parsed.get("linkedin_post") or "",
            "facebook_post": parsed.get("facebook_post") or "",
            "hashtags": parsed.get("hashtags") or [],
            "image_ideas": parsed.get("image_ideas") or [],
        }
    except Exception:
        return build_social_post_fallback(topic, audience, tone)


@app.get("/")
def home():
    return FileResponse(os.path.join(BASE_DIR, "landing.html"))


@app.get("/app")
def dashboard():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/rag")
def rag_page():
    return FileResponse(os.path.join(BASE_DIR, "rag.html"))


@app.get("/gemini-embedding-search")
@app.get("/azure-embedding-search")
def gemini_embedding_search_page():
    return FileResponse(os.path.join(BASE_DIR, "azure_embedding_search.html"))


@app.get("/it-agent")
def it_agent_page():
    return FileResponse(os.path.join(BASE_DIR, "excel_agent.html"))


@app.get("/excel-agent")
def excel_agent_page():
    return FileResponse(os.path.join(BASE_DIR, "excel_agent.html"))


@app.get("/gemini-api-test")
def gemini_api_test_page():
    return FileResponse(os.path.join(BASE_DIR, "gemini_api_test.html"))


@app.get("/social-post-generator")
def social_post_generator_page():
    return FileResponse(os.path.join(BASE_DIR, "social_post_generator.html"))


@app.get("/simple-chatbot")
def simple_chatbot_page():
    return FileResponse(os.path.join(BASE_DIR, "simple_chatbot.html"))


@app.get("/customer-support")
def customer_support_page():
    ensure_customer_support_db()
    return FileResponse(os.path.join(BASE_DIR, "customer_support_representative.html"))


@app.get("/support-representative")
def support_representative_page():
    ensure_customer_support_db()
    return FileResponse(os.path.join(BASE_DIR, "support_representative_inbox.html"))


@app.get("/db-builder")
def db_builder_page():
    return FileResponse(os.path.join(BASE_DIR, "db_builder.html"))


@app.get("/copilot-config")
def copilot_config():
    token_endpoint = os.getenv("COPILOT_STUDIO_TOKEN_ENDPOINT", "").strip()
    return {
        "agent_name": "IT Solutions Agent",
        "token_endpoint": token_endpoint,
        "configured": bool(token_endpoint),
    }


@app.get("/rag/demo")
def rag_demo():
    demo = load_rag_demo()
    return {
        "document_name": "sample_rag.pdf",
        "pdf_path": demo["pdf_path"],
        "extracted_text": demo["text"],
        "sentences": demo["sentences"],
        "chunks": demo["chunks"],
        "embedding_dimensions": len(demo["records"][0]["embedding"]) if demo["records"] else 0,
        "vector_database": {
            "name": "local-json-vector-store",
            "path": demo["store_path"],
            "record_count": len(demo["records"]),
        }
    }


@app.post("/rag/query")
def rag_query(payload: RagQuestion):
    demo = load_rag_demo()
    matches = retrieve_rag_matches(payload.question, demo["records"])
    answer = generate_rag_answer(payload.question, matches)
    return {
        "question": payload.question,
        "answer": answer,
        "nlp_result": answer,
        "matches": matches,
        "vector_database": {
            "name": "local-json-vector-store",
            "path": demo["store_path"],
            "record_count": len(demo["records"]),
        }
    }


@app.post("/gemini-embedding-search/query")
@app.post("/azure-embedding-search/query")
def gemini_embedding_search_query(payload: GeminiEmbeddingQueryRequest):
    question = (payload.question or "").strip()
    if not question:
        return {
            "question": None,
            "results": [],
            "error": "Please enter a question before running the search.",
        }

    try:
        demo = load_gemini_embedding_demo()
        results = gemini_embedding_demo.query_documents(
            question=question,
            embedding_store=demo["records"],
            client=demo["client"],
            types_module=demo["types_module"],
            model_name=demo["model_name"],
            top_k=2,
        )
        return {
            "question": question,
            "results": results,
            "document_count": len(demo["records"]),
            "search_mode": "gemini-embeddings",
            "embedding_model": demo["model_name"],
        }
    except Exception as exc:
        return {
            "question": question,
            "results": [],
            "error": str(exc),
        }


@app.post("/support-chat/query")
def support_chat_query(payload: RagQuestion):
    records = load_support_knowledge_base()
    matches = retrieve_rag_matches(payload.question, records)
    extracted_order_number = extract_order_number(payload.question)
    order = lookup_support_order(extracted_order_number)

    if order:
        answer = build_order_support_answer(payload.question, order, matches)
        mode = "order_lookup"
    elif extracted_order_number:
        answer = (
            f"I could not find order {extracted_order_number} in the support database. "
            "Please re-check the order number. For this demo, try FD1001, FD1002, FD1003, FD1004, or FD1005."
        )
        mode = "order_not_found"
    elif is_order_specific_question(payload.question):
        policy_answer = generate_support_answer(payload.question, matches)
        answer = (
            f"{policy_answer} To check the exact backend order data, please share your order number. "
            "For this demo, you can try FD1001 for a delayed order, FD1002 for a missing item, FD1004 for payment deduction, or FD1005 for address change."
        )
        mode = "needs_order_number"
    else:
        answer = generate_support_answer(payload.question, matches)
        mode = "knowledge_base"

    return {
        "question": payload.question,
        "answer": answer,
        "nlp_result": answer,
        "mode": mode,
        "order_number": extracted_order_number,
        "order": order,
        "matches": matches,
        "knowledge_base": {
            "name": "food-delivery-support-kb",
            "record_count": len(records),
        },
        "support_database": {
            "name": "customer-support-sqlite",
            "path": CUSTOMER_SUPPORT_DB_PATH,
        },
        "human_handoff_available": True,
    }


@app.get("/support-chat/orders")
def support_chat_orders():
    return {
        "orders": list_support_demo_orders(),
        "database": {
            "name": "customer-support-sqlite",
            "path": CUSTOMER_SUPPORT_DB_PATH,
        },
    }


@app.post("/support-chat/orders/{order_number}/{action}")
def support_chat_order_action(order_number: str, action: str, payload: SupportOrderActionRequest):
    try:
        return update_support_order_action(
            order_number=order_number,
            action=action,
            ticket_id=payload.ticket_id,
            note=payload.note,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/support-chat/handoff")
def support_chat_handoff(payload: SupportHandoffRequest):
    try:
        if not payload.issue.strip():
            return {"success": False, "error": "Please describe the issue before connecting to a support agent."}
        return create_support_handoff(payload)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/support-chat/human-message")
def support_chat_human_message(payload: SupportHumanMessageRequest):
    try:
        return add_support_human_message(payload)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/support-chat/handoffs")
def support_chat_handoffs():
    handoffs = list_support_handoffs()
    return {
        "handoffs": handoffs,
        "stats": build_support_handoff_stats(handoffs),
        "database": {
            "name": "customer-support-sqlite",
            "path": CUSTOMER_SUPPORT_DB_PATH,
        },
    }


@app.post("/support-chat/handoffs/{ticket_id}/update")
def support_chat_update_handoff(ticket_id: str, payload: SupportTicketUpdateRequest):
    try:
        return update_support_ticket(ticket_id, payload)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/support-chat/handoffs/{ticket_id}/messages")
def support_chat_handoff_messages(ticket_id: str):
    ticket = get_support_handoff_messages(ticket_id)
    if not ticket:
        return {"success": False, "error": "Human support ticket not found."}
    return {"success": True, "ticket": ticket}


@app.post("/db-builder/build")
def db_builder_build(payload: DbBuilderRequest):
    try:
        db_type = (payload.db_type or "").strip().lower()
        if db_type in {"sqlite", "postgres", "postgresql", "mysql"}:
            return create_sqlite_database(payload)
        if db_type in {"mongodb", "mongo"}:
            try:
                return create_mongo_database(payload)
            except Exception as exc:
                fallback = create_json_document_store(payload)
                fallback["warning"] = f"MongoDB was not reachable at mongodb://localhost:27017. Saved a local JSON store instead. Details: {exc}"
                return fallback
        return {"error": "Unsupported db_type. Use 'sqlite' or 'mongodb'."}
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/db-builder/chat")
def db_builder_chat(payload: DbBuilderChatRequest):
    raw_message = (payload.message or "").strip()
    conversation_id = payload.conversation_id or ""
    if not conversation_id:
        conversation_id = _new_conversation_id()

    if raw_message.lower() in {"restart", "reset"}:
        DB_BUILDER_CONVERSATIONS.pop(conversation_id, None)

    state = _ensure_db_builder_state(conversation_id)
    state["updated_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    if raw_message.lower() in {"start", ""} and state.get("stage") == "db_type":
        return {
            "conversation_id": conversation_id,
            "stage": state["stage"],
            "assistant": "Hi. I can help you design a small database (schema) and insert one sample record. " + _db_builder_next_prompt(state),
            "state": {k: v for k, v in state.items() if k != "sample"},
        }

    stage = state.get("stage")

    try:
        if stage == "db_type":
            parsed = _parse_db_type(raw_message)
            if not parsed:
                return {
                    "conversation_id": conversation_id,
                    "stage": state["stage"],
                    "assistant": "I did not catch the database type. Please type 'sqlite' or 'mongodb'.",
                    "state": {k: v for k, v in state.items() if k != "sample"},
                }

            state["db_type"] = parsed
            state["stage"] = "db_name"
            return {
                "conversation_id": conversation_id,
                "stage": state["stage"],
                "assistant": f"Great, we will create a {parsed} database. " + _db_builder_next_prompt(state),
                "state": {k: v for k, v in state.items() if k != "sample"},
            }

        if stage == "db_name":
            try:
                state["db_name"] = sanitize_identifier(raw_message, "database name")
            except Exception:
                suggestion = _suggest_identifier(raw_message)
                if suggestion:
                    return {
                        "conversation_id": conversation_id,
                        "stage": state["stage"],
                        "assistant": f"That name is not valid. Try: {suggestion}",
                        "state": {k: v for k, v in state.items() if k != "sample"},
                    }
                return {
                    "conversation_id": conversation_id,
                    "stage": state["stage"],
                    "assistant": "Please use only letters, numbers, and underscore (example: swiggy_support).",
                    "state": {k: v for k, v in state.items() if k != "sample"},
                }

            state["stage"] = "use_case"
            return {
                "conversation_id": conversation_id,
                "stage": state["stage"],
                "assistant": "Saved. " + _db_builder_next_prompt(state),
                "state": {k: v for k, v in state.items() if k != "sample"},
            }

        if stage == "use_case":
            state["use_case"] = raw_message
            state["stage"] = "entity_name"
            return {
                "conversation_id": conversation_id,
                "stage": state["stage"],
                "assistant": "Nice. " + _db_builder_next_prompt(state),
                "state": {k: v for k, v in state.items() if k != "sample"},
            }

        if stage == "entity_name":
            try:
                state["entity_name"] = sanitize_identifier(raw_message, "entity name")
            except Exception:
                suggestion = _suggest_identifier(raw_message)
                if suggestion:
                    return {
                        "conversation_id": conversation_id,
                        "stage": state["stage"],
                        "assistant": f"That name is not valid. Try: {suggestion}",
                        "state": {k: v for k, v in state.items() if k != "sample"},
                    }
                return {
                    "conversation_id": conversation_id,
                    "stage": state["stage"],
                    "assistant": "Please use letters, numbers, and underscore only (example: orders).",
                    "state": {k: v for k, v in state.items() if k != "sample"},
                }

            state["stage"] = "fields"
            return {
                "conversation_id": conversation_id,
                "stage": state["stage"],
                "assistant": "Great. " + _db_builder_next_prompt(state),
                "state": {k: v for k, v in state.items() if k != "sample"},
            }

        if stage == "fields":
            parsed_fields, errors = _parse_fields_spec(raw_message)
            if errors:
                return {
                    "conversation_id": conversation_id,
                    "stage": state["stage"],
                    "assistant": " ".join(errors),
                    "state": {k: v for k, v in state.items() if k != "sample"},
                }

            sanitized = []
            seen = set()
            for field in parsed_fields:
                name = sanitize_identifier(field["name"], "field name")
                if name in seen:
                    raise ValueError(f"Duplicate field name: {name}")
                seen.add(name)
                sanitized.append({"name": name, "type": field["type"]})

            state["fields"] = sanitized
            state["sample"] = {}
            state["sample_index"] = 0
            state["stage"] = "sample"
            return {
                "conversation_id": conversation_id,
                "stage": state["stage"],
                "assistant": "Perfect. Now we will add 1 sample record. " + _db_builder_next_prompt(state),
                "state": {k: v for k, v in state.items() if k != "sample"},
            }

        if stage == "sample":
            fields = state.get("fields") or []
            idx = int(state.get("sample_index") or 0)
            if idx < len(fields):
                field = fields[idx]
                state["sample"][field["name"]] = raw_message
                state["sample_index"] = idx + 1

            idx = int(state.get("sample_index") or 0)
            if idx < len(fields):
                return {
                    "conversation_id": conversation_id,
                    "stage": state["stage"],
                    "assistant": _db_builder_next_prompt(state),
                    "state": {k: v for k, v in state.items() if k != "sample"},
                }

            # Build it now
            db_payload = DbBuilderRequest(
                db_type=state["db_type"],
                db_name=state["db_name"],
                use_case=state.get("use_case"),
                entity_name=state["entity_name"],
                fields=[DbFieldSpec(**f) for f in fields],
                sample=state.get("sample") or {},
            )

            if state["db_type"] == "sqlite":
                result = create_sqlite_database(db_payload)
                state["stage"] = "done"
                return {
                    "conversation_id": conversation_id,
                    "stage": state["stage"],
                    "assistant": (
                        f"Created SQLite database at: {result['db_path']} . "
                        f"Table: {result['table']} . Inserted 1 sample row. Type 'restart' to build another."
                    ),
                    "result": result,
                    "state": {k: v for k, v in state.items() if k != "sample"},
                }

            try:
                result = create_mongo_database(db_payload)
                state["stage"] = "done"
                return {
                    "conversation_id": conversation_id,
                    "stage": state["stage"],
                    "assistant": (
                        f"Inserted 1 sample document into MongoDB database '{result['database']}', collection '{result['collection']}'. "
                        f"Type 'restart' to build another."
                    ),
                    "result": result,
                    "state": {k: v for k, v in state.items() if k != "sample"},
                }
            except Exception as exc:
                result = create_json_document_store(db_payload)
                state["stage"] = "done"
                return {
                    "conversation_id": conversation_id,
                    "stage": state["stage"],
                    "assistant": (
                        "I could not connect to MongoDB on this machine (mongodb://localhost:27017). "
                        f"I saved a local JSON document store instead at: {result['store_path']} . "
                        f"Details: {exc}. Type 'restart' to build another."
                    ),
                    "result": result,
                    "state": {k: v for k, v in state.items() if k != "sample"},
                }

        # done or unknown stage
        state["stage"] = "done"
        return {
            "conversation_id": conversation_id,
            "stage": state["stage"],
            "assistant": _db_builder_next_prompt(state),
            "state": {k: v for k, v in state.items() if k != "sample"},
        }
    except Exception as exc:
        return {
            "conversation_id": conversation_id,
            "stage": state.get("stage"),
            "assistant": f"Error: {exc}. Type 'restart' to try again.",
            "state": {k: v for k, v in state.items() if k != "sample"},
        }


@app.post("/excel-agent/ask")
def ask_excel_agent(payload: ExcelQuestion):
    result = generate_excel_agent_response(payload.question, payload.sheet_data)
    return {
        "question": payload.question,
        "sheet_data": payload.sheet_data,
        "answer": result["answer"],
        "nlp_result": result["nlp_result"],
        "suggested_formula": result["suggested_formula"],
    }


@app.post("/gemini-api-test/run")
def run_gemini_api_test(payload: ApiTestRequest):
    api_url = (payload.api_url or "https://jsonplaceholder.typicode.com/todos/1").strip()
    prompt = payload.prompt.strip() if payload.prompt else None

    try:
        api_result = fetch_sample_https_api(api_url)
        parsed_body = None
        try:
            parsed_body = json.loads(api_result["body"])
        except json.JSONDecodeError:
            parsed_body = api_result["body"]

        nlp_result = generate_api_test_summary(api_url, api_result, prompt)
        return {
            "api_url": api_url,
            "status_code": api_result["status_code"],
            "content_type": api_result["content_type"],
            "raw_response": parsed_body,
            "raw_text": api_result["body"],
            "nlp_result": nlp_result,
            "error": None
        }
    except Exception as exc:
        return {
            "api_url": api_url,
            "status_code": None,
            "content_type": None,
            "raw_response": None,
            "raw_text": None,
            "nlp_result": None,
            "error": str(exc)
        }


@app.post("/social-post-generator/create")
def create_social_post(payload: SocialPostRequest):
    result = generate_social_post_package(payload.topic, payload.audience, payload.tone)
    html_preview = f"""
<article class="social-post-card">
  <header>
    <p class="platform-tag">Generated Social Post</p>
    <h1>{result["title"]}</h1>
    <p class="post-summary">{result["summary"]}</p>
  </header>
  <section class="image-strip">
    {"".join(f'<div class="image-idea"><strong>Image Idea</strong><p>{idea}</p></div>' for idea in result["image_ideas"])}
  </section>
  <section class="post-body">
    <h2>LinkedIn</h2>
    <p>{result["linkedin_post"]}</p>
    <h2>Facebook</h2>
    <p>{result["facebook_post"]}</p>
  </section>
  <footer class="hashtag-row">
    {" ".join(result["hashtags"])}
  </footer>
</article>
""".strip()

    return {
        "topic": payload.topic,
        "audience": payload.audience,
        "tone": payload.tone,
        "title": result["title"],
        "summary": result["summary"],
        "linkedin_post": result["linkedin_post"],
        "facebook_post": result["facebook_post"],
        "hashtags": result["hashtags"],
        "image_ideas": result["image_ideas"],
        "html_preview": html_preview,
    }


def generate_html_from_image_bytes(image_bytes: bytes, mime_type: str):
    prompt = """
    You are an expert front-end web developer. 
    Please look at this image and generate a single, clean HTML file that reproduces the design as closely as possible.
    Include all CSS styling within a <style> tag in the <head>.
    Make the layout responsive.
    Important image rules:
    - Do not use remote image URLs from the internet.
    - If the screenshot contains image areas, recreate them with styled blocks, gradients, or inline SVG/data URI placeholders.
    - If a real photo is needed, use a local-looking placeholder with meaningful alt text instead of a broken image.
    - Keep all assets self-contained inside the returned HTML.
    Return STRICTLY valid HTML code. Do not include markdown formatting like ```html.
    """
    try:
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content([
            prompt,
            {"mime_type": mime_type, "data": image_bytes}
        ])
        html_code = response.text.strip().replace("```html", "").replace("```", "").strip()
        html_code = add_image_fallbacks(html_code)
        return html_code, None
    except Exception as exc:
        return None, str(exc)


def slugify_filename(value: str, fallback: str = "generated-page"):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def ensure_generated_page_dirs():
    os.makedirs(GENERATED_PAGES_DIR, exist_ok=True)
    os.makedirs(GENERATED_PAGE_ASSETS_DIR, exist_ok=True)


def build_generated_page_paths(original_filename: str | None):
    ensure_generated_page_dirs()
    source_name = os.path.splitext(original_filename or "uploaded-image")[0]
    slug = slugify_filename(source_name, fallback="uploaded-image")
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    html_filename = f"{slug}-{timestamp}.html"

    extension = os.path.splitext(original_filename or "")[1].lower()
    if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        extension = ".png"
    asset_filename = f"{slug}-{timestamp}{extension}"

    return {
        "html_filename": html_filename,
        "html_path": os.path.join(GENERATED_PAGES_DIR, html_filename),
        "asset_filename": asset_filename,
        "asset_path": os.path.join(GENERATED_PAGE_ASSETS_DIR, asset_filename),
    }


def build_svg_placeholder_data_uri(label: str = "Image Placeholder"):
    safe_label = re.sub(r"\s+", " ", label).strip() or "Image Placeholder"
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">
      <defs>
        <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#f5efe6"/>
          <stop offset="50%" stop-color="#e4edf2"/>
          <stop offset="100%" stop-color="#f7f9fc"/>
        </linearGradient>
      </defs>
      <rect width="1200" height="800" fill="url(#bg)"/>
      <circle cx="220" cy="180" r="120" fill="#d8643b" opacity="0.18"/>
      <circle cx="980" cy="220" r="160" fill="#1f6f73" opacity="0.16"/>
      <rect x="180" y="220" width="840" height="360" rx="32" fill="#ffffff" opacity="0.88"/>
      <text x="600" y="360" text-anchor="middle" font-family="Arial, sans-serif" font-size="54" font-weight="700" fill="#17202b">{safe_label}</text>
      <text x="600" y="430" text-anchor="middle" font-family="Arial, sans-serif" font-size="26" fill="#5e6a76">Generated placeholder image</text>
    </svg>
    """.strip()
    return "data:image/svg+xml;charset=UTF-8," + urllib.parse.quote(svg)


def add_image_fallbacks(html_code: str):
    def replace_img(match):
        tag = match.group(0)
        alt_match = re.search(r'alt=["\']([^"\']*)["\']', tag, flags=re.IGNORECASE)
        label = alt_match.group(1) if alt_match else "Image Placeholder"
        fallback_src = build_svg_placeholder_data_uri(label)
        onerror_code = f"this.onerror=null;this.src='{fallback_src}';this.style.objectFit='cover';this.style.background='#f7f4ee';"

        if re.search(r'onerror=', tag, flags=re.IGNORECASE):
            updated_tag = re.sub(r'onerror=["\'][^"\']*["\']', f'onerror="{onerror_code}"', tag, count=1, flags=re.IGNORECASE)
        else:
            updated_tag = tag.replace("<img", f'<img onerror="{onerror_code}"', 1)

        if not re.search(r'\bsrc=["\'][^"\']+["\']', updated_tag, flags=re.IGNORECASE):
            updated_tag = updated_tag.replace("<img", f'<img src="{fallback_src}"', 1)

        return updated_tag

    return re.sub(r"<img\b[^>]*>", replace_img, html_code, flags=re.IGNORECASE)


def inject_uploaded_image_references(html_code: str, image_url: str):
    def replace_img(match):
        tag = match.group(0)
        updated_tag = re.sub(
            r'\bsrc=["\'][^"\']*["\']',
            f'src="{image_url}"',
            tag,
            count=1,
            flags=re.IGNORECASE,
        )
        if updated_tag == tag:
            updated_tag = updated_tag.replace("<img", f'<img src="{image_url}"', 1)
        if not re.search(r'\balt=["\']', updated_tag, flags=re.IGNORECASE):
            updated_tag = updated_tag.replace("<img", '<img alt="Screenshot reference image"', 1)
        return updated_tag

    updated_html = re.sub(r"<img\b[^>]*>", replace_img, html_code, flags=re.IGNORECASE)

    if re.search(r"<img\b", updated_html, flags=re.IGNORECASE):
        return updated_html

    hero_image = f"""
    <section style="max-width: 1200px; margin: 24px auto; padding: 0 24px;">
      <img
        src="{image_url}"
        alt="Uploaded screenshot reference"
        style="display: block; width: 100%; max-height: 520px; object-fit: cover; border-radius: 24px; box-shadow: 0 24px 60px rgba(0, 0, 0, 0.12);"
      />
    </section>
    """.strip()

    if re.search(r"<body[^>]*>", updated_html, flags=re.IGNORECASE):
        return re.sub(r"(<body[^>]*>)", r"\1\n" + hero_image, updated_html, count=1, flags=re.IGNORECASE)

    return hero_image + "\n" + updated_html


NAVIGATION_ELEMENT_PATTERN = re.compile(
    r"<a\b[^>]*>.*?</a>|<button\b[^>]*>.*?</button>|<input\b[^>]*type=[\"']?(?:button|submit)[\"']?[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)


def strip_html_tags(value: str):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", text).strip()


def extract_navigation_label(fragment: str, tag_name: str):
    if tag_name == "input":
        for attribute in ("value", "aria-label", "title", "name"):
            match = re.search(rf'\b{attribute}=["\']([^"\']+)["\']', fragment, flags=re.IGNORECASE)
            if match and match.group(1).strip():
                return match.group(1).strip()
        return "Input Button"

    inner_match = re.search(rf"<{tag_name}\b[^>]*>(.*?)</{tag_name}>", fragment, flags=re.IGNORECASE | re.DOTALL)
    if inner_match:
        cleaned = strip_html_tags(inner_match.group(1))
        if cleaned:
            return cleaned

    for attribute in ("aria-label", "title", "name"):
        match = re.search(rf'\b{attribute}=["\']([^"\']+)["\']', fragment, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()

    return tag_name.title()


def annotate_navigation_targets(html_code: str):
    targets = []
    counter = 0

    def replace(match):
        nonlocal counter
        fragment = match.group(0)
        tag_name_match = re.match(r"<\s*(a|button|input)\b", fragment, flags=re.IGNORECASE)
        if not tag_name_match:
            return fragment

        tag_name = tag_name_match.group(1).lower()
        nav_id_match = re.search(r'\bdata-nav-id=["\']([^"\']+)["\']', fragment, flags=re.IGNORECASE)
        if nav_id_match:
            nav_id = nav_id_match.group(1)
        else:
            counter += 1
            nav_id = f"nav-{counter}"
            fragment = re.sub(
                rf"<\s*{tag_name}\b",
                f'<{tag_name} data-nav-id="{nav_id}"',
                fragment,
                count=1,
                flags=re.IGNORECASE,
            )

        href_match = re.search(r'\bhref=["\']([^"\']+)["\']', fragment, flags=re.IGNORECASE)
        targets.append({
            "id": nav_id,
            "tag": tag_name,
            "label": extract_navigation_label(fragment, tag_name),
            "href": href_match.group(1) if href_match else None,
        })
        return fragment

    updated_html = NAVIGATION_ELEMENT_PATTERN.sub(replace, html_code)
    return updated_html, targets


def update_navigation_target_link(html_code: str, target_id: str, target_url: str):
    quoted_target_url = json.dumps(target_url)
    onclick_value = f'window.location.href={quoted_target_url};'

    def set_attribute(fragment: str, attribute: str, value: str, quote: str = '"'):
        if re.search(rf'\b{attribute}=["\']([^"\']*)["\']', fragment, flags=re.IGNORECASE):
            return re.sub(
                rf'\b{attribute}=["\']([^"\']*)["\']',
                f"{attribute}={quote}{value}{quote}",
                fragment,
                count=1,
                flags=re.IGNORECASE,
            )

        closing = "/>" if fragment.rstrip().endswith("/>") else ">"
        insertion = f" {attribute}={quote}{value}{quote}"
        return fragment[:fragment.rfind(closing)] + insertion + closing

    def replace(match):
        tag_name = match.group("tag").lower()
        fragment = match.group(0)

        if tag_name == "a":
            fragment = set_attribute(fragment, "href", target_url)
        else:
            fragment = set_attribute(fragment, "onclick", onclick_value, quote="'")

            if re.search(r'\bstyle=["\']([^"\']*)["\']', fragment, flags=re.IGNORECASE):
                fragment = re.sub(
                    r'\bstyle=["\']([^"\']*)["\']',
                    lambda style_match: f'style="{style_match.group(1).rstrip("; ")}; cursor: pointer;"',
                    fragment,
                    count=1,
                    flags=re.IGNORECASE,
                )
            else:
                fragment = set_attribute(fragment, "style", "cursor: pointer;")

        fragment = set_attribute(fragment, "data-nav-child", target_url)

        return fragment

    pattern = re.compile(
        rf'<(?P<tag>a|button|input)\b(?=[^>]*\bdata-nav-id=["\']{re.escape(target_id)}["\'])[^>]*>',
        flags=re.IGNORECASE,
    )
    updated_html, count = pattern.subn(replace, html_code, count=1)
    return updated_html, count > 0


def read_generated_page_html(filename: str):
    file_path = safe_generated_file_path(GENERATED_PAGES_DIR, filename)
    if not file_path:
        return None, None

    with open(file_path, "r", encoding="utf-8") as html_file:
        return html_file.read(), file_path


def get_navigation_targets_for_page(filename: str):
    html_code, file_path = read_generated_page_html(filename)
    if html_code is None or file_path is None:
        return None

    updated_html, targets = annotate_navigation_targets(html_code)
    if updated_html != html_code:
        with open(file_path, "w", encoding="utf-8") as html_file:
            html_file.write(updated_html)
    return targets


def list_generated_image_to_html_pages():
    ensure_generated_page_dirs()
    pages = []
    for name in os.listdir(GENERATED_PAGES_DIR):
        file_path = os.path.join(GENERATED_PAGES_DIR, name)
        if not name.lower().endswith(".html") or not os.path.isfile(file_path):
            continue
        pages.append({
            "filename": name,
            "label": os.path.splitext(name)[0].replace("-", " "),
            "url": f"/image-to-html/pages/{name}",
            "created_at": os.path.getmtime(file_path),
        })

    pages.sort(key=lambda item: item["created_at"], reverse=True)
    return pages


def safe_generated_file_path(directory: str, filename: str):
    base_dir = os.path.abspath(directory)
    file_path = os.path.abspath(os.path.join(base_dir, filename))
    if not file_path.startswith(base_dir + os.sep):
        return None
    if not os.path.exists(file_path):
        return None
    return file_path


@app.get("/image-to-html")
def image_to_html_page():
    return FileResponse(os.path.join(BASE_DIR, "image_to_html.html"))


@app.get("/image-to-html/pages/{filename}")
def image_to_html_generated_page(filename: str):
    file_path = safe_generated_file_path(GENERATED_PAGES_DIR, filename)
    if not file_path:
        return {"error": "Generated page not found."}
    return FileResponse(file_path)


@app.get("/image-to-html/assets/{filename}")
def image_to_html_generated_asset(filename: str):
    file_path = safe_generated_file_path(GENERATED_PAGE_ASSETS_DIR, filename)
    if not file_path:
        return {"error": "Generated asset not found."}
    return FileResponse(file_path)


@app.get("/image-to-html/generated-pages")
def image_to_html_generated_pages():
    return {"pages": list_generated_image_to_html_pages()}


@app.get("/image-to-html/page-targets/{filename}")
def image_to_html_page_targets(filename: str):
    targets = get_navigation_targets_for_page(filename)
    if targets is None:
        return {"error": "Generated page not found.", "targets": []}
    return {"filename": filename, "targets": targets}


@app.post("/image-to-html/generate")
async def image_to_html_generate(file: UploadFile = File(...)):
    try:
        image_bytes = await file.read()
        html_code, error = generate_html_from_image_bytes(image_bytes, file.content_type)
        if error or not html_code:
            return {"html": None, "error": error or "No HTML was generated."}

        file_paths = build_generated_page_paths(file.filename)
        with open(file_paths["asset_path"], "wb") as asset_file:
            asset_file.write(image_bytes)

        image_url = f"/image-to-html/assets/{file_paths['asset_filename']}"
        html_code = inject_uploaded_image_references(html_code, image_url)
        html_code, nav_targets = annotate_navigation_targets(html_code)
        html_code = add_image_fallbacks(html_code)

        with open(file_paths["html_path"], "w", encoding="utf-8") as html_file:
            html_file.write(html_code)

        return {
            "html": html_code,
            "error": None,
            "filename": file_paths["html_filename"],
            "page_url": f"/image-to-html/pages/{file_paths['html_filename']}",
            "image_url": image_url,
            "nav_targets": nav_targets,
            "pages": list_generated_image_to_html_pages(),
        }
    except Exception as exc:
        return {"html": None, "error": str(exc)}


@app.post("/image-to-html/generate-linked-page")
async def image_to_html_generate_linked_page(
    parent_filename: str = Form(...),
    target_id: str = Form(...),
    file: UploadFile = File(...),
):
    try:
        parent_html, parent_path = read_generated_page_html(parent_filename)
        if parent_html is None or parent_path is None:
            return {"html": None, "error": "Parent page not found."}

        image_bytes = await file.read()
        html_code, error = generate_html_from_image_bytes(image_bytes, file.content_type)
        if error or not html_code:
            return {"html": None, "error": error or "No HTML was generated."}

        file_paths = build_generated_page_paths(file.filename)
        with open(file_paths["asset_path"], "wb") as asset_file:
            asset_file.write(image_bytes)

        image_url = f"/image-to-html/assets/{file_paths['asset_filename']}"
        html_code = inject_uploaded_image_references(html_code, image_url)
        html_code, child_nav_targets = annotate_navigation_targets(html_code)
        html_code = add_image_fallbacks(html_code)

        with open(file_paths["html_path"], "w", encoding="utf-8") as html_file:
            html_file.write(html_code)

        child_page_url = f"/image-to-html/pages/{file_paths['html_filename']}"
        updated_parent_html, updated = update_navigation_target_link(parent_html, target_id, child_page_url)
        if not updated:
            return {"html": None, "error": "Selected navigation target was not found in the parent page."}

        with open(parent_path, "w", encoding="utf-8") as parent_file:
            parent_file.write(updated_parent_html)

        return {
            "html": html_code,
            "error": None,
            "filename": file_paths["html_filename"],
            "page_url": child_page_url,
            "image_url": image_url,
            "parent_filename": parent_filename,
            "parent_page_url": f"/image-to-html/pages/{parent_filename}",
            "nav_targets": child_nav_targets,
            "parent_nav_targets": get_navigation_targets_for_page(parent_filename) or [],
            "pages": list_generated_image_to_html_pages(),
        }
    except Exception as exc:
        return {"html": None, "error": str(exc)}


@app.post("/mcp-signup")
def mcp_signup(payload: McpSignupRequest):
    try:
        if not payload.name.strip() or not payload.email.strip() or not payload.password:
            return {"success": False, "error": "Please fill in all fields."}
        if len(payload.password) < 6:
            return {"success": False, "error": "Password must be at least 6 characters."}

        return create_mcp_signup(payload)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/simple-chatbot/ask")
def simple_chatbot_ask(payload: SimpleChatRequest):
    message = (payload.message or "").strip()
    if not message:
        return {"reply": None, "error": "Please enter a message."}

    recent_history = []
    for item in (payload.history or [])[-8:]:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        role = "Assistant" if str(item.get("role") or "").lower() == "assistant" else "User"
        recent_history.append(f"{role}: {text}")

    conversation_context = "\n".join(recent_history) if recent_history else "No prior conversation."
    prompt = f"""
You are a friendly and helpful chatbot for a simple demo web app.
Keep responses concise, clear, and practical.
If the user asks for steps, prefer short numbered guidance.
If you are unsure about something, say so honestly.

Conversation so far:
{conversation_context}

User: {message}
Assistant:
"""

    try:
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content(prompt)
        reply = (response.text or "").strip()
        if not reply:
            reply = "I could not generate a reply just now. Please try again."

        return {"reply": reply, "error": None}
    except Exception as exc:
        return {"reply": None, "error": str(exc)}


@app.post("/ask")
def ask_ai(prompt: Prompt):
    # --- THIS IS THE UPDATED PROMPT FOR 4 COLLECTIONS ---
    system_prompt = """
    You are an expert MongoDB agent. Convert the user's natural language request into a MongoDB aggregation pipeline.
    
    Database Schema:
    - Collection 'users': { "_id": int, "name": string, "age": int, "city": string }
    - Collection 'orders': { "_id": int, "user_id": int, "product_id": int, "amount": float, "created_at": string (YYYY-MM-DD) }
    - Collection 'products': { "_id": int, "name": string, "category": string, "price": float }
    - Collection 'reviews': { "_id": int, "user_id": int, "product_id": int, "rating": int, "comment": string }
    
    Relationships:
    - 'orders' links to 'users' (orders.user_id = users._id)
    - 'orders' links to 'products' (orders.product_id = products._id)
    - 'reviews' links to 'users' (reviews.user_id = users._id)
    - 'reviews' links to 'products' (reviews.product_id = products._id)
    
    Instructions:
    - Return STRICTLY a valid JSON object. No explanations, no markdown blocks like ```json.
    - The JSON object must have two keys: "collection" (the base collection to start the query on) and "pipeline" (an array of aggregation stages).
    - If joining data, use `$lookup` followed by `$unwind` if necessary to flatten the results for charting.
    
    Example Output Format for a Join:
    {
      "collection": "users",
      "pipeline": [ 
        { 
          "$lookup": { 
            "from": "orders", 
            "localField": "_id", 
            "foreignField": "user_id", 
            "as": "order_details" 
          } 
        },
        { "$unwind": "$order_details" },
        { "$project": { "name": 1, "order_amount": "$order_details.amount" } }
      ]
    }
    
    User Request: 
    """
    
    try:
        full_prompt = system_prompt + prompt.text
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content(full_prompt)
        
        # Clean up any accidental markdown blocks
        raw_json = response.text.strip().replace("```json", "").replace("```", "").strip()

        # Execute
        db_results, db_error = execute_mongo(raw_json)
        nlp_summary = None if db_error else generate_nlp_summary(prompt.text, raw_json, db_results)

        return {
            "query": raw_json, # We return the JSON string so the frontend can display it
            "data": db_results,
            "error": db_error,
            "nlp_summary": nlp_summary
        }

    except Exception as e:
        print("ERROR:", e)
        return {
            "query": "Error generating query",
            "data": None,
            "error": str(e),
            "nlp_summary": None
        }
