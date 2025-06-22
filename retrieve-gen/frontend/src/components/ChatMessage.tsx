'use client';

import React, { useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  Avatar,
  Chip,
  Collapse,
  IconButton,
  Divider,
} from '@mui/material';
import {
  Person as PersonIcon,
  SmartToy as BotIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Description as DescriptionIcon,
} from '@mui/icons-material';
import { ChatMessage as ChatMessageType } from '@/lib/types';
import { formatTime } from '@/lib/utils';

interface ChatMessageProps {
  message: ChatMessageType;
}

export function ChatMessage({ message }: ChatMessageProps) {
  const [showContext, setShowContext] = useState(false);
  const isUser = message.role === 'user';

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        gap: 2,
        mb: 2,
        alignItems: 'flex-start',
      }}
    >
      <Avatar
        sx={{
          bgcolor: isUser ? 'primary.main' : 'grey.300',
          width: 40,
          height: 40,
        }}
      >
        {isUser ? <PersonIcon /> : <BotIcon />}
      </Avatar>

      <Box sx={{ maxWidth: '80%', minWidth: '200px' }}>
        <Paper
          elevation={2}
          sx={{
            p: 2,
            bgcolor: isUser ? 'primary.main' : 'grey.100',
            color: isUser ? 'white' : 'text.primary',
            borderRadius: isUser ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
          }}
        >
          <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
            {message.content}
          </Typography>
        </Paper>

        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            mt: 1,
            justifyContent: isUser ? 'flex-end' : 'flex-start',
          }}
        >
          <Typography variant="caption" color="text.secondary">
            {formatTime(message.timestamp)}
          </Typography>

          {!isUser && message.context && message.context.length > 0 && (
            <Chip
              icon={<DescriptionIcon />}
              label={`${message.context.length} sources`}
              size="small"
              onClick={() => setShowContext(!showContext)}
              clickable
              variant="outlined"
            />
          )}
        </Box>

        {!isUser && message.context && message.context.length > 0 && (
          <Collapse in={showContext}>
            <Box sx={{ mt: 2 }}>
              <Divider sx={{ mb: 1 }} />
              <Typography variant="subtitle2" gutterBottom>
                Source Documents:
              </Typography>
              {message.context.map((doc, index) => (
                <Paper
                  key={index}
                  elevation={1}
                  sx={{
                    p: 2,
                    mb: 1,
                    bgcolor: 'grey.50',
                    borderLeft: '4px solid',
                    borderLeftColor: 'primary.main',
                  }}
                >
                  <Typography variant="caption" color="text.secondary">
                    Source {index + 1}
                  </Typography>
                  <Typography variant="body2" sx={{ mt: 0.5 }}>
                    {doc.document.substring(0, 200)}
                    {doc.document.length > 200 && '...'}
                  </Typography>
                </Paper>
              ))}
            </Box>
          </Collapse>
        )}
      </Box>
    </Box>
  );
}