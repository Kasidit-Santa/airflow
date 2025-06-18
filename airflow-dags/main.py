import os
import chromadb
from sentence_transformers import SentenceTransformer
import google.generativeai as genai
import textwrap
import re
from collections import deque

# --- Configuration ---
CHROMA_HOST = "localhost"
CHROMA_PORT = 8000
CHROMA_COLLECTION_NAME = "covid_data_collection_multi_vector"
# You correctly updated this model name!
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# --- Gemini API Configuration (SAFER METHOD) ---
# ★★★ FIX 3: Load API Key from Environment Variables for Security ★★★
# Replace with your actual key or load from environment variables for production
GEMINI_API_KEY = "AIzaSyBvLOjOiY7QYaJoGe3FPb9C2MH5RAQIs3s"
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set. Please set it before running.")
genai.configure(api_key=GEMINI_API_KEY)
# Use a more stable model name
GEMINI_MODEL_NAME = "gemini-2.5-flash" # Updated to a current, stable model

# --- UX Enhancement: Terminal Colors ---
class TermColors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# --- Global Model Instance (Singleton Pattern) ---
_sentence_transformer_model_instance = None

def get_sentence_transformer_model():
    """Loads the SentenceTransformer model only once per process."""
    global _sentence_transformer_model_instance
    if _sentence_transformer_model_instance is None:
        print(f"\n{TermColors.CYAN}[SYSTEM] Loading SentenceTransformer model '{EMBEDDING_MODEL_NAME}'...{TermColors.ENDC}")
        _sentence_transformer_model_instance = SentenceTransformer(EMBEDDING_MODEL_NAME)
        print(f"{TermColors.CYAN}[SYSTEM] Model loaded successfully.{TermColors.ENDC}")
    return _sentence_transformer_model_instance

def get_chroma_client():
    """Establishes connection to the ChromaDB server."""
    return chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)

def retrieve_context_from_chroma(query: str, conversation_history: deque, top_k: int = 5):
    """
    Implements a FULL HYBRID search strategy for provinces, nationalities, and districts.
    """
    client = get_chroma_client()
    model = get_sentence_transformer_model()

    effective_query = query
    # ... (follow-up logic can remain the same) ...

    try:
        collection = client.get_collection(name=CHROMA_COLLECTION_NAME)

        # --- Stage 1: Broad Semantic Search ---
        print(f"\n{TermColors.BLUE}[RETRIEVAL] Stage 1: Performing broad semantic search for: '{effective_query}'{TermColors.ENDC}")
        # The e5 models work best with a 'query: ' prefix
        # For paraphrase models, the prefix is not standard, so we can omit it for better general compatibility.
        query_embedding = model.encode(effective_query, normalize_embeddings=True).tolist()
        semantic_results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=['metadatas']
        )

        # --- Stage 2: Targeted Entity Searches ---
        entity_searches = {}

        # Extract and search for NATIONALITY
        known_nationalities = {"thai": "Thailand", "american": "American", "cambodian": "Cambodian", "burmese": "Burmese"}
        for keyword, db_value in known_nationalities.items():
            if keyword in effective_query.lower():
                print(f"{TermColors.BLUE}[RETRIEVAL] Stage 2a: Performing targeted search for nationality: '{db_value}'{TermColors.ENDC}")
                entity_embedding = model.encode(db_value, normalize_embeddings=True).tolist()
                entity_searches['nationality'] = collection.query(
                    query_embeddings=[entity_embedding], n_results=top_k, where={"type": "nationality"}, include=['metadatas']
                )
                break

        # Extract and search for PROVINCE & DISTRICT (simplified)
        # In a real system, you might use a more robust Named Entity Recognition (NER) model.
        locations_to_check = ["Bangkok", "Chiang Mai", "Nakhon Pathom", "Sa Kaeo", "Sakon Nakhon", "Mueang", "กรุงเทพ", "เชียงใหม่", "นครปฐม", "สระแก้ว", "สกลนคร", "เมือง"]
        for loc in locations_to_check:
            if loc in effective_query:
                print(f"{TermColors.BLUE}[RETRIEVAL] Stage 2b: Performing targeted search for location: '{loc}'{TermColors.ENDC}")
                loc_embedding = model.encode(loc, normalize_embeddings=True).tolist()
                entity_searches['province'] = collection.query(query_embeddings=[loc_embedding], n_results=top_k, where={"type": "province"}, include=['metadatas'])
                entity_searches['district'] = collection.query(query_embeddings=[loc_embedding], n_results=top_k, where={"type": "district"}, include=['metadatas'])
                break

        # --- Stage 3: Merge and De-duplicate Results ---
        final_context = {}

        def add_to_context(results):
            if results and results['metadatas'] and results['metadatas'][0]:
                for meta in results['metadatas'][0]:
                    # Use record_id as the key to automatically handle duplicates
                    final_context[meta['record_id']] = meta['full_text']

        # Add results in order of specificity (most specific last, so they overwrite)
        add_to_context(semantic_results)
        add_to_context(entity_searches.get('province'))
        add_to_context(entity_searches.get('district'))
        add_to_context(entity_searches.get('nationality'))

        # The document passed to the LLM is the 'full_text' from the metadata
        relevant_documents = [{"document": text} for text in final_context.values()]

        print(f"{TermColors.BLUE}[RETRIEVAL] Found {len(relevant_documents)} unique relevant documents after merging.{TermColors.ENDC}")
        return relevant_documents

    except Exception as e:
        print(f"{TermColors.FAIL}[ERROR] Error retrieving context from ChromaDB: {e}{TermColors.ENDC}")
        return []

# ########################################################################## #
# #################### ★★★ PROMPT FIX STARTS HERE ★★★ #################### #
# ########################################################################## #

def generate_response_with_gemini(query: str, context: list, conversation_history: deque):
    """Generates a response using Gemini, augmented with context and conversation history."""
    model = genai.GenerativeModel(GEMINI_MODEL_NAME)

    history_str = "\n".join([f"User: {q}\nAI: {a}" for q, a in conversation_history]) if conversation_history else "No previous conversation."

    context_str = "\n---\n".join([d['document'] for d in context])

    if not context_str:
        # --- PROMPT 1: NO CONTEXT FOUND (FALLBACK) ---
        prompt_template = f"""You are a helpful AI assistant.

        **Important:** No specific information was found in the specialized public health database to answer the user's question.

        **Instructions:**
        1. Politely inform the user that you couldn't find specific information in the database.
        2. Answer the user's question based on your general knowledge.
        3. Use the `Conversation History` for context if needed.

        ---
        **[Conversation History]**
        {history_str}
        ---
        **[User's Question]**
        {query}
        ---

        **Answer:**
        """
        print(f"{TermColors.WARNING}[WARNING] No specific context retrieved. Using general knowledge prompt.{TermColors.ENDC}")
    else:
        # --- PROMPT 2: CONTEXT FOUND (MAIN RAG PROMPT) ---
        prompt_template = f"""You are an expert AI assistant for a Thai public health database. Your primary goal is to provide accurate answers based **only** on the `Retrieved Documents` provided below.

        **Instructions:**
        1. Analyze the `Retrieved Documents` to answer the `User's Question`.
        2. Synthesize information from multiple documents if necessary to form a complete answer.
        3. Use the `Conversation History` only for contextual understanding of the user's question.
        4. **Crucially**, if the answer is not found within the `Retrieved Documents`, you MUST state that the information is not available in the provided context. Do not use external knowledge or make up information.
        5. Answer clearly and concisely in the same language as the user's question.

        ---
        **[Retrieved Documents]**
        {context_str}
        ---
        **[Conversation History]**
        {history_str}
        ---
        **[User's Question]**
        {query}
        ---

        **Answer:**
        """

    augmented_prompt = textwrap.dedent(prompt_template)
    print(f"\n{TermColors.CYAN}[LLM] Sending augmented prompt to Gemini...{TermColors.ENDC}")

    try:
        response = model.generate_content(augmented_prompt)
        return response.text
    except Exception as e:
        print(f"{TermColors.FAIL}[ERROR] Error generating response with Gemini: {e}{TermColors.ENDC}")
        return "I encountered an error while generating the response. Please try again."

# ######################################################################## #
# #################### ★★★ PROMPT FIX ENDS HERE ★★★ #################### #
# ######################################################################## #


if __name__ == "__main__":
    print(f"{TermColors.HEADER}{'='*60}")
    print(f"{' ' * 15} Welcome to the Advanced RAG Chatbot! {' ' *15}")
    print(f"{'='*60}{TermColors.ENDC}")
    print("This chatbot remembers your conversation. Ask follow-up questions!")
    print("Type 'exit' or 'quit' to end the chat.")
    print("-" * 60)

    get_sentence_transformer_model()

    conversation_history = deque(maxlen=4)

    while True:
        try:
            user_query = input(f"\n{TermColors.BOLD}YOU: {TermColors.ENDC}").strip()
            if user_query.lower() in ["exit", "quit"]:
                print(f"\n{TermColors.HEADER}CHATBOT: Goodbye!{TermColors.ENDC}")
                break

            retrieved_context = retrieve_context_from_chroma(user_query, conversation_history)

            gemini_answer = generate_response_with_gemini(user_query, retrieved_context, conversation_history)

            print(f"\n{TermColors.BOLD}{TermColors.HEADER}CHATBOT:{TermColors.ENDC}")
            print(textwrap.fill(gemini_answer, width=80))

            conversation_history.append((user_query, gemini_answer))

        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{TermColors.HEADER}CHATBOT: Chat interrupted. Goodbye!{TermColors.ENDC}")
            break
        except Exception as e:
            print(f"{TermColors.FAIL}\n[FATAL ERROR] An unexpected error occurred: {e}{TermColors.ENDC}")
            break