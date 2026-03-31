from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import pymongo
import json
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("No API key found.")
genai.configure(api_key=api_key)

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Prompt(BaseModel):
    text: str


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


@app.get("/")
def home():
    return FileResponse(os.path.join(BASE_DIR, "landing.html"))


@app.get("/app")
def dashboard():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

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
