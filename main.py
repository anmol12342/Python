from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
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

class McpSignupRequest(BaseModel):
    name: str
    email: str
    password: str

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


@app.get("/customer-support")
def customer_support_page():
    return FileResponse(os.path.join(BASE_DIR, "customer_support_representative.html"))


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


@app.post("/support-chat/query")
def support_chat_query(payload: RagQuestion):
    records = load_support_knowledge_base()
    matches = retrieve_rag_matches(payload.question, records)
    answer = generate_support_answer(payload.question, matches)
    return {
        "question": payload.question,
        "answer": answer,
        "nlp_result": answer,
        "matches": matches,
        "knowledge_base": {
            "name": "food-delivery-support-kb",
            "record_count": len(records),
        }
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
