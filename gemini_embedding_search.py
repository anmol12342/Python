from __future__ import annotations

import json
import math
import os
import sys
from typing import Any


try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False


DOCUMENTS = [
    {
        "doc_id": "DOC001",
        "company": "TechNova Inc.",
        "section": "Executive Summary",
        "content": (
            "TechNova reported revenues of $4.2B in FY2023, a 23% YoY growth. "
            "Cloud division contributed $1.8B. Net income reached $620M with "
            "operating margin of 18.2%."
        ),
    },
    {
        "doc_id": "DOC002",
        "company": "TechNova Inc.",
        "section": "Risk Factors",
        "content": (
            "The company faces competition in cloud infrastructure. Currency "
            "fluctuation risks as 38% of revenues are in non-USD currencies. EU "
            "regulatory scrutiny on AI products may impact future launches."
        ),
    },
    {
        "doc_id": "DOC003",
        "company": "GlobalBank Corp.",
        "section": "Executive Summary",
        "content": (
            "GlobalBank delivered net interest income of $8.7B in FY2023, a 14% "
            "increase. Total deposits reached $142B. Non-performing loans ratio "
            "improved to 1.2% from 1.8% in FY2022."
        ),
    },
    {
        "doc_id": "DOC004",
        "company": "GlobalBank Corp.",
        "section": "Risk Factors",
        "content": (
            "Credit risk is concentrated in commercial real estate loans at 24% of "
            "total loan book. Cybersecurity investments of $380M made in 2023. "
            "Basel III rules may require $1.2B additional capital by 2026."
        ),
    },
    {
        "doc_id": "DOC005",
        "company": "GreenEnergy Ltd.",
        "section": "Executive Summary",
        "content": (
            "GreenEnergy achieved 8.4 GW installed renewable capacity in FY2023, "
            "up 34%. Revenue grew 28% to $3.6B. The company targets 15 GW by end "
            "of 2025."
        ),
    },
]


def _import_genai():
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "The Gemini embedding demo requires the 'google-genai' package. "
            "Install it with: pip install google-genai"
        ) from exc

    return genai, types


def create_gemini_client() -> tuple[Any, Any, str]:
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001").strip()

    if not api_key:
        raise ValueError("Missing required environment variable: GEMINI_API_KEY")

    genai, types = _import_genai()
    client = genai.Client(api_key=api_key)
    return client, types, model_name


def _extract_embedding_values(embedding_item: Any) -> list[float]:
    if hasattr(embedding_item, "values"):
        return list(embedding_item.values)
    if isinstance(embedding_item, dict) and "values" in embedding_item:
        return list(embedding_item["values"])
    if isinstance(embedding_item, list):
        return [float(value) for value in embedding_item]
    raise TypeError("Unexpected embedding response format from Gemini API.")


def generate_embedding(
    client: Any,
    types_module: Any,
    model_name: str,
    text: str,
    task_type: str,
) -> list[float]:
    response = client.models.embed_content(
        model=model_name,
        contents=[text],
        config=types_module.EmbedContentConfig(task_type=task_type),
    )
    return _extract_embedding_values(response.embeddings[0])


def build_embedding_store(
    documents: list[dict[str, str]],
    client: Any,
    types_module: Any,
    model_name: str,
) -> list[dict[str, Any]]:
    response = client.models.embed_content(
        model=model_name,
        contents=[document["content"] for document in documents],
        config=types_module.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )

    embedding_store = []
    for document, embedding_item in zip(documents, response.embeddings):
        record = dict(document)
        record["embedding"] = _extract_embedding_values(embedding_item)
        embedding_store.append(record)
    return embedding_store


def cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    dot_product = sum(left * right for left, right in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(value * value for value in vector_a))
    norm_b = math.sqrt(sum(value * value for value in vector_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def query_documents(
    question: str,
    embedding_store: list[dict[str, Any]],
    client: Any,
    types_module: Any,
    model_name: str,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    question_embedding = generate_embedding(
        client=client,
        types_module=types_module,
        model_name=model_name,
        text=question,
        task_type="RETRIEVAL_QUERY",
    )

    scored_results = []
    for record in embedding_store:
        score = cosine_similarity(question_embedding, record["embedding"])
        scored_results.append(
            {
                "doc_id": record["doc_id"],
                "company": record["company"],
                "section": record["section"],
                "content": record["content"],
                "score": round(score, 6),
            }
        )

    scored_results.sort(key=lambda item: item["score"], reverse=True)
    return scored_results[:top_k]


def build_demo_index() -> list[dict[str, Any]]:
    client, types_module, model_name = create_gemini_client()
    return build_embedding_store(DOCUMENTS, client, types_module, model_name)


def main() -> None:
    client, types_module, model_name = create_gemini_client()
    embedding_store = build_embedding_store(DOCUMENTS, client, types_module, model_name)

    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = input("Enter your question: ").strip()

    if not question:
        raise ValueError("A question is required to run the similarity search.")

    results = query_documents(
        question=question,
        embedding_store=embedding_store,
        client=client,
        types_module=types_module,
        model_name=model_name,
        top_k=2,
    )

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
