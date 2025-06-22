# ===================================================================
# File: backend/main.py
# Purpose: Full RAG backend with FastAPI, ChromaDB, and Gemini,
#          including debugging helpers and CORS configuration.
# ===================================================================
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2' 

import textwrap
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from collections import deque
import google.generativeai as genai
import chromadb
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import uvicorn
from fastapi.middleware.cors import CORSMiddleware # <-- ★★★ 1. IMPORT THIS ★★★

# --- Load Configuration & Models ---
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8000))
CHROMA_COLLECTION_NAME = "covid_data_collection_multi_vector"
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
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

# --- Lifespan Event Handler ---
lifespan_globals = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[SYSTEM] Lifespan: Loading SentenceTransformer model...")
    lifespan_globals["sentence_transformer_model"] = SentenceTransformer(EMBEDDING_MODEL_NAME)
    print("[SYSTEM] Lifespan: Model loaded successfully.")
    
    print(f"[SYSTEM] Lifespan: Initializing ChromaDB client at {CHROMA_HOST}:{CHROMA_PORT}...")
    lifespan_globals["chroma_client"] = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    lifespan_globals["chroma_client"].heartbeat()
    print("[SYSTEM] Lifespan: ChromaDB client initialized and connected.")
    
    yield
    
    lifespan_globals.clear()
    print("[SYSTEM] Application shutdown complete.")


# --- FastAPI App Initialization ---
app = FastAPI(lifespan=lifespan)


# --- ★★★ 2. ADD THE CORS MIDDLEWARE BLOCK HERE ★★★ ---
# This block must be added to allow your frontend (running on localhost:3000)
# to communicate with your backend (running on localhost:8001).
origins = [
    "http://localhost",
    "http://localhost:3000", # The origin for your Next.js app
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Allows all methods (GET, POST, etc.)
    allow_headers=["*"], # Allows all headers
)
# --- ★★★ END OF CORS BLOCK ★★★ ---


# --- RAG Core Logic ---
def retrieve_context_from_chroma(query: str, top_k: int = 5):
    # ... (rest of your code is unchanged)
    client = lifespan_globals["chroma_client"]
    model = lifespan_globals["sentence_transformer_model"]
    try:
        collection = client.get_collection(name=CHROMA_COLLECTION_NAME)
        query_embedding = model.encode(query, normalize_embeddings=True).tolist()
        semantic_results = collection.query(query_embeddings=[query_embedding], n_results=top_k, include=['metadatas'])

        final_context = {}
        if semantic_results and semantic_results.get('metadatas') and semantic_results['metadatas'][0]:
            for meta in semantic_results['metadatas'][0]:
                final_context[meta['record_id']] = meta['full_text']
        
        return [{"document": text} for text in final_context.values()]
    except Exception as e:
        print(f"[ERROR] Error retrieving context from ChromaDB: {e}")
        return []

def generate_response_with_gemini(query: str, context: list, conversation_history: deque):
    # ... (rest of your code is unchanged)
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

# --- API Endpoint ---
@app.post("/api/chat", response_model=ChatResponse)
async def chat_handler(request: ChatRequest):
    try:
        history = deque(request.history, maxlen=6)
        context = retrieve_context_from_chroma(request.query)
        structured_answer = generate_response_with_gemini(request.query, context, history)
        return ChatResponse(structuredAnswer=structured_answer, context=context)
    except Exception as e:
        print(f"[ERROR] Error in chat handler: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- ★★★ Uvicorn Runner for Direct Debugging ★★★ ---
if __name__ == "__main__":
    print("[SYSTEM] Starting Uvicorn server for direct debugging...")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)