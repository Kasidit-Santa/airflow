// File: lib/types.ts

/**
 * Represents a single message object used for display in the UI chat timeline.
 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  context?: ContextDocument[]; // Context is optional, only for assistant messages
}

/**
 * Represents a single source document retrieved from the vector database.
 */
export interface ContextDocument {
  document: string;
}

/**
 * Represents the strongly-typed structure of the main answer object from the LLM.
 * This matches the Pydantic model in your Python backend.
 */
export interface StructuredAnswer {
  summary: string;
  provinces_mentioned: string[];
  is_data_sufficient: boolean;
  confidence_level: 'High' | 'Medium' | 'Low' | 'N/A';
}

/**
 * Represents the entire API response from your /api/chat endpoint.
 */
export interface ChatResponse {
  structuredAnswer: StructuredAnswer;
  context: ContextDocument[];
}

/**

 * Represents a single turn in the conversation history sent to the backend.
 * This matches the structure expected by the Gemini API.
 */
export interface HistoryItem {
  role: 'user' | 'assistant';
  parts: { text: string }[];
}

/**
 * Represents the request payload sent to your /api/chat endpoint.
 */
export interface ChatRequest {
  query: string;
  history: HistoryItem[];
}