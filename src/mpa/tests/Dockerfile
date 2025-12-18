FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure nobody user can write pytest cache files
RUN chown -R nobody:nogroup /app

USER nobody
