# File: frontend/Dockerfile
FROM node:18-alpine

WORKDIR /app

# 1. Copy package.json and lock files
COPY package*.json ./

# 2. Install dependencies (this layer is now cached)
RUN npm install

# 3. Now copy the rest of your application code
COPY . .

# Build the Next.js app
RUN npm run build

# Expose the port and set the start command
EXPOSE 3000
CMD ["npm", "start"]