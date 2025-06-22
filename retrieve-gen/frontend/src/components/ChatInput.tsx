'use client';

import React, { useState } from 'react';
import {
  Box,
  TextField,
  IconButton,
  Paper,
  Typography,
} from '@mui/material';
import { Send as SendIcon } from '@mui/icons-material';

interface ChatInputProps {
  onSendMessage: (message: string) => void;
  disabled?: boolean;
}

export function ChatInput({ onSendMessage, disabled }: ChatInputProps) {
  const [message, setMessage] = useState('');

  const handleSubmit = () => {
    if (message.trim() && !disabled) {
      onSendMessage(message);
      setMessage('');
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <Paper elevation={3} sx={{ p: 2, borderRadius: 0 }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-end' }}>
        <TextField
          fullWidth
          multiline
          maxRows={4}
          variant="outlined"
          placeholder="Ask about Thai health data..."
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyPress={handleKeyPress}
          disabled={disabled}
          inputProps={{ maxLength: 1000 }}
        />
        <IconButton
          color="primary"
          onClick={handleSubmit}
          disabled={!message.trim() || disabled}
          sx={{ mb: 0.5 }}
        >
          <SendIcon />
        </IconButton>
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block', textAlign: 'right' }}>
        {message.length}/1000
      </Typography>
    </Paper>
  );
}