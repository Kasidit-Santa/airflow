import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2' 

import textwrap
import json
import ast 
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from collections import deque
import google.generativeai as genai
import chromadb
from sentence_transformers import SentenceTransformer, CrossEncoder
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import uvicorn
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8000))
CHROMA_COLLECTION_NAME = "covid_data_collection_multi_vector"
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CROSS_ENCODER_MODEL_NAME = 'cross-encoder/ms-marco-MiniLM-L6-v2' 
GEMINI_MODEL_NAME = "gemini-2.5-flash"

# --- Pydantic Models ---
class StructuredAnswer(BaseModel):
    summary: str
    provinces_mentioned: list[str]
    is_data_sufficient: bool
    confidence_level: str

class ChatRequest(BaseModel):
    query: str
    history: list[dict] = []

class ChatResponse(BaseModel):
    structuredAnswer: StructuredAnswer
    context: list[dict]

lifespan_globals = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    lifespan_globals["sentence_transformer_model"] = SentenceTransformer(EMBEDDING_MODEL_NAME)
    lifespan_globals["cross_encoder_model"] = CrossEncoder(CROSS_ENCODER_MODEL_NAME)
    print("[SYSTEM] Lifespan: Models loaded successfully.")
    
    print(f"[SYSTEM] Lifespan: Initializing ChromaDB client at {CHROMA_HOST}:{CHROMA_PORT}...")
    lifespan_globals["chroma_client"] = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    lifespan_globals["chroma_client"].heartbeat()
    print("[SYSTEM] Lifespan: ChromaDB client initialized and connected.")
    
    yield
    
    lifespan_globals.clear()
    print("[SYSTEM] Application shutdown complete.")

app = FastAPI(lifespan=lifespan)

origins = [
    "http://localhost",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def generate_sub_queries_with_gemini(query: str, history_str: str) -> list[str]:
    """Generates alternative queries using Gemini to broaden the search."""
    model_genai = genai.GenerativeModel(GEMINI_MODEL_NAME)
    
    prompt = f"""Based on the user's query and the conversation history, generate 3 to 5 alternative queries to improve document retrieval.
    These queries should explore different facets of the original question, including potential synonyms, related concepts, and underlying questions.
    Return ONLY a Python list of strings in valid Python list format (e.g., ["query 1", "query 2"]).

    [Conversation History]
    {history_str if history_str else "No previous conversation."}

    [User's Original Query]
    "{query}"

    [Example Output]
    ["alternative query 1", "related concept query", "more specific version of the query"]
    """
    
    try:
        response = model_genai.generate_content(prompt)
        cleaned_response = response.text.strip().replace("```python", "").replace("```", "")
        sub_queries = ast.literal_eval(cleaned_response)
        if isinstance(sub_queries, list):
            return sub_queries
        return []
    except (Exception, SyntaxError) as e:
        print(f"[WARN] Could not generate sub-queries: {e}")
        return []

def retrieve_expanded_context_from_chroma(query: str, history_str: str, top_k: int = 10):
    """Retrieves context by generating sub-queries and searching for all of them."""
    client = lifespan_globals["chroma_client"]
    model = lifespan_globals["sentence_transformer_model"]
    
    sub_queries = generate_sub_queries_with_gemini(query, history_str)
    all_queries = list(set([query] + sub_queries))
    print(f"[INFO] Performing expanded search with queries: {all_queries}")

    query_embeddings = model.encode(all_queries, normalize_embeddings=True).tolist()
    
    try:
        collection = client.get_collection(name=CHROMA_COLLECTION_NAME)
        semantic_results = collection.query(
            query_embeddings=query_embeddings, 
            n_results=top_k, 
            include=['metadatas', 'documents']
        )

        final_context = {}
        if semantic_results and semantic_results.get('ids'):
            for i, result_ids_per_query in enumerate(semantic_results['ids']):
                for j, doc_id in enumerate(result_ids_per_query):
                    if doc_id not in final_context:
                        final_context[doc_id] = semantic_results['documents'][i][j]
                        
        return list(final_context.values())

    except Exception as e:
        print(f"[ERROR] Error retrieving expanded context from ChromaDB: {e}")
        return []

def rerank_documents_with_cross_encoder(query: str, documents: list[str], top_n: int = 5) -> list[dict]:
    """Re-ranks a list of documents against a query using a Cross-Encoder."""
    if not documents:
        return []
    cross_encoder = lifespan_globals["cross_encoder_model"]
    
    # 2. Create pairs (identical to your list comprehension)
    pairs = [[query, doc] for doc in documents]
    
    # 3. Predict scores (the exact same method call as your example)
    scores = cross_encoder.predict(pairs)
    
    # 4. This part just sorts the results and returns the best ones
    scored_docs = sorted(zip(scores, documents), key=lambda x: x[0], reverse=True)
    return [{"document": doc} for _, doc in scored_docs[:top_n]]

def generate_response_with_gemini(query: str, context: list, conversation_history: deque):
    """Generates the final structured JSON response using Gemini."""
    model_genai = genai.GenerativeModel(GEMINI_MODEL_NAME)
    
    history_parts = []
    for turn in conversation_history:
        role = "Assistant" if turn.get('role') == 'assistant' else "User"
        try:
            text = turn.get('parts', [{}])[0].get('text', '')
            history_parts.append(f"{role}: {text}")
        except (IndexError, AttributeError):
            continue
    history_str = "\n".join(history_parts)

    context_str = "\n---\n".join([d['document'] for d in context])

    json_schema = {
        "summary": "A concise summary of the answer based on the documents.",
        "provinces_mentioned": ["A list of provinces or key locations mentioned."],
        "is_data_sufficient": "True if documents had enough info, else False.",
        "confidence_level": "Enum: 'High', 'Medium', 'Low', 'N/A'."
    }

    prompt_template = f"""You are a data analysis AI. Your task is to answer the user's question based ONLY on the `Retrieved Documents`.
    Your response MUST be a single, valid JSON object. Do not add any text before or after the JSON.
    The JSON object must have the following structure:
    {json.dumps(json_schema, indent=2)}

    ---
    [Conversation History]
    {history_str if history_str else "No previous conversation."}
    ---
    [Retrieved Documents]
    {context_str if context else "No documents were found."}
    ---
    [User's Question]
    {query}
    ---

    Respond with ONLY the JSON object.
    """
    augmented_prompt = textwrap.dedent(prompt_template)
    response = model_genai.generate_content(augmented_prompt)
    try:
        json_response_text = response.text.strip().replace("```json", "").replace("```", "")
        parsed_json = json.loads(json_response_text)
        return parsed_json
    except (json.JSONDecodeError, Exception) as e:
        print(f"[ERROR] Failed to parse JSON from LLM: {e}. Falling back to text.")
        return {
            "summary": "Sorry, I encountered an issue processing the data. Please try again.",
            "provinces_mentioned": [],
            "is_data_sufficient": False,
            "confidence_level": "N/A"
        }

@app.post("/api/chat", response_model=ChatResponse)
async def chat_handler(request: ChatRequest):
    try:
        # 1. Prepare conversation history for context
        history = deque(request.history, maxlen=6)
        history_parts = []
        for turn in history:
            role = "Assistant" if turn.get('role') == 'assistant' else "User"
            try:
                text = turn.get('parts', [{}])[0].get('text', '')
                history_parts.append(f"{role}: {text}")
            except (IndexError, AttributeError):
                continue
        history_str = "\n".join(history_parts)

        # 2. Retrieve a broad set of documents using query expansion
        initial_documents = retrieve_expanded_context_from_chroma(request.query, history_str, top_k=10)

        # 3. Re-rank the documents to find the most relevant ones
        reranked_context = rerank_documents_with_cross_encoder(request.query, initial_documents, top_n=5)

        # 4. Generate the final answer with the highly relevant, re-ranked context
        structured_answer = generate_response_with_gemini(request.query, reranked_context, history)
        
        return ChatResponse(structuredAnswer=structured_answer, context=reranked_context)

    except Exception as e:
        print(f"[ERROR] Error in chat handler: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("[SYSTEM] Starting Uvicorn server for direct debugging...")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)