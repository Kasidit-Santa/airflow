import { format } from 'date-fns';

export function formatTime(date: Date): string {
  return format(date, 'HH:mm');
}

export function generateId(): string {
  return Math.random().toString(36).substr(2, 9);
}