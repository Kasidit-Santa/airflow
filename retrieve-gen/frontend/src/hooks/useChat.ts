'use client';

import { useState, useCallback } from 'react';
import { ChatMessage, ChatRequest, ChatResponse } from '@/lib/types';
import { sendChatMessage } from '@/lib/api';
import { generateId } from '@/lib/utils';

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim()) return;

    const userMessage: ChatMessage = {
      id: generateId(),
      content: content.trim(),
      role: 'user',
      timestamp: new Date(),
    };

    // Use a temporary array for the request to include the new user message immediately
    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setIsLoading(true);
    setError(null);

    try {
      // --- FIX #1: Correctly format the history for the Python backend ---
      // The history should include all messages except the very last one (the current query)
      const historyForRequest = updatedMessages
        .slice(0, -1) // Exclude the last message
        .map(msg => ({
            role: msg.role,
            parts: [{ text: msg.content }]
        }));

      const request: ChatRequest = {
        query: content.trim(),
        history: historyForRequest,
      };

      // This function sends the request and should return the parsed JSON response
      const response: ChatResponse = await sendChatMessage(request);

      // --- FIX #2: Access the summary from the nested structuredAnswer object ---
      const assistantMessage: ChatMessage = {
        id: generateId(),
        content: response.structuredAnswer.summary,
        role: 'assistant',
        timestamp: new Date(),
        context: response.context,
      };

      setMessages(prev => [...prev, assistantMessage]);

    } catch (err: any) { // Use 'any' to inspect the object freely
      console.error("CAUGHT ERROR OBJECT:", err); // Keep this for easier debugging

      // --- FIX #3: Robustly parse different kinds of error objects ---
      let displayMessage = 'An unexpected error occurred. Please try again.';
      if (err.response && err.response.data && err.response.data.detail) {
        // Handles specific error details from FastAPI
        displayMessage = err.response.data.detail;
      } else if (err.message) {
        // Handles standard JavaScript Error objects
        displayMessage = err.message;
      }
      // --- End of error parsing ---

      setError(displayMessage); // Set the clean error message for the UI Alert

      // Create a user-friendly error message for the chat timeline
      const errorChatMessage: ChatMessage = {
        id: generateId(),
        content: `Sorry, something went wrong. Error: ${displayMessage}`,
        role: 'assistant',
        timestamp: new Date(),
      };
      setMessages(prev => [...prev, errorChatMessage]);

    } finally {
      setIsLoading(false);
    }
  }, [messages]); // Dependency on `messages` is correct here

  const clearChat = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return {
    messages,
    isLoading,
    error,
    sendMessage,
    clearChat,
  };
}