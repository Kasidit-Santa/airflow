'use client';

import React, { useEffect, useRef } from 'react';
import {
  Box,
  Paper,
  Typography,
  IconButton,
  AppBar,
  Toolbar,
  Container,
  Alert,
  Card,
  CardContent,
  List,
  ListItem,
  ListItemText,
} from '@mui/material';
import {
  Delete as DeleteIcon,
  SmartToy as BotIcon,
} from '@mui/icons-material';
import { useChat } from '@/hooks/useChat';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { TypingIndicator } from './TypingIndicator';

export function ChatInterface() {
  const { messages, isLoading, error, sendMessage, clearChat } = useChat();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  return (
    <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <AppBar position="static" elevation={2}>
        <Toolbar>
          <BotIcon sx={{ mr: 2 }} />
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6">
              Thai Health Data Assistant
            </Typography>
            <Typography variant="body2" sx={{ opacity: 0.8 }}>
              Ask questions about COVID-19 data and health information
            </Typography>
          </Box>
          {messages.length > 0 && (
            <IconButton
              color="inherit"
              onClick={clearChat}
              sx={{ ml: 2 }}
            >
              <DeleteIcon />
            </IconButton>
          )}
        </Toolbar>
      </AppBar>

      <Box sx={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <Box sx={{ flex: 1, overflow: 'auto', p: 2 }}>
          {messages.length === 0 ? (
            <Container maxWidth="md" sx={{ mt: 4 }}>
              <Box sx={{ textAlign: 'center', mb: 4 }}>
                <BotIcon sx={{ fontSize: 64, color: 'primary.main', mb: 2 }} />
                <Typography variant="h4" gutterBottom>
                  Welcome to Thai Health Data Assistant
                </Typography>
                <Typography variant="body1" color="text.secondary" paragraph>
                  Ask questions about COVID-19 data, provincial health statistics, or district-level information.
                </Typography>
              </Box>

              <Card>
                <CardContent>
                  <Typography variant="h6" gutterBottom>
                    Example Questions:
                  </Typography>
                  <List>
                    <ListItem>
                      <ListItemText primary="What's the COVID-19 situation in Bangkok?" />
                    </ListItem>
                    <ListItem>
                      <ListItemText primary="Show me health data for Chiang Mai province" />
                    </ListItem>
                    <ListItem>
                      <ListItemText primary="Compare infection rates between districts" />
                    </ListItem>
                  </List>
                </CardContent>
              </Card>
            </Container>
          ) : (
            <Container maxWidth="md">
              {messages.map((message) => (
                <ChatMessage key={message.id} message={message} />
              ))}
              
              {isLoading && <TypingIndicator />}
              
              {error && (
                <Alert severity="error" sx={{ mt: 2 }}>
                  <Typography variant="body2">
                    <strong>Error occurred:</strong> {error}
                  </Typography>
                </Alert>
              )}
              
              <div ref={messagesEndRef} />
            </Container>
          )}
        </Box>

        <ChatInput onSendMessage={sendMessage} disabled={isLoading} />
      </Box>
    </Box>
  );
}
