'use client';

import React from 'react';
import { Box, Typography } from '@mui/material';

export function TypingIndicator() {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, p: 2 }}>
      <Box sx={{ display: 'flex', gap: 0.5 }}>
        {[0, 1, 2].map((i) => (
          <Box
            key={i}
            sx={{
              width: 8,
              height: 8,
              bgcolor: 'grey.400',
              borderRadius: '50%',
              animation: 'bounce 1.4s ease-in-out infinite both',
              animationDelay: `${i * 0.16}s`,
              '@keyframes bounce': {
                '0%, 80%, 100%': {
                  transform: 'scale(0)',
                },
                '40%': {
                  transform: 'scale(1)',
                },
              },
            }}
          />
        ))}
      </Box>
      <Typography variant="body2" color="text.secondary">
        AI is typing...
      </Typography>
    </Box>
  );
}