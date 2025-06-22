# File: backend/Dockerfile
FROM python:3.10-slim

WORKDIR /app

# 1. Copy only the requirements file first
COPY requirements.txt .

# 2. Install dependencies (this layer is now cached)
RUN pip install --no-cache-dir -r requirements.txt

# 3. Now copy the rest of your application code
COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]