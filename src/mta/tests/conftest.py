import pytest
import smtplib
from fastapi import FastAPI, Request
import uvicorn
import threading
import time
import requests
from typing import Dict, List
import logging
import socket
import json
import subprocess
import sys
import jwt
import os
import hashlib
from email.parser import BytesParser

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MDA_API_SECRET = os.getenv("MDA_API_SECRET")
MTA_HOST = os.getenv("MTA_HOST")

class MockAPIServer:
    def __init__(self):
        self.app = FastAPI()
        self.received_emails = []
        self.mailboxes = {}
        self.should_exit = False
        self.server = None  # Add this to store server instance
        
        @self.app.post("/api/mail")
        async def receive_mail(request: Request):
            logger.info("Email received by API!")
            
            content_type = request.headers.get("content-type", "")
            if content_type != "message/rfc822":
                raise HTTPException(status_code=400, detail="Content-Type must be message/rfc822")

            # Handle raw email data
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                raise HTTPException(status_code=401, detail="Authorization header missing")
            jwt_token = auth_header.split(" ")[1]
            try:
                payload = jwt.decode(jwt_token, MDA_API_SECRET, algorithms=["HS256"])
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=401, detail="Token expired")
            except jwt.InvalidTokenError:
                raise HTTPException(status_code=401, detail="Invalid token")

            raw_data = await request.body()

            h = hashlib.sha256(raw_data).hexdigest()
            if h != payload["email_hash"]:
                raise HTTPException(status_code=401, detail="Invalid email hash")

            email_data = {
                "metadata": payload,
                "raw_email": raw_data,
                "email": BytesParser().parsebytes(raw_data, headersonly=False)
            }
            logger.info(f"Raw email received: {len(raw_data)} bytes for {payload["original_recipients"][0:4]}")
        
            self.received_emails.append(email_data)
            return {"status": "ok"}
            
        @self.app.post("/api/regie/mailbox")
        async def check_mailbox(request: Request):
            data = await request.json()
            address = data.get("address")
            exists = address in self.mailboxes
            logger.info(f"Mailbox check for {address}: {exists}")
            return {"exists": exists}
        
        @self.app.get("/health")
        async def health_check():
            logger.info("Health check received")
            return {"status": "healthy"}

    def add_mailbox(self, address: str):
        self.mailboxes[address] = True

    def wait_for_email(self, timeout: int = 10, n: int = 1):
        start_time = time.time()
        while len(self.received_emails) < n:
            time.sleep(0.1)
            if time.time() - start_time > timeout:
                raise TimeoutError(f"No email received after {timeout} seconds")

    def start(self):
        self.server = uvicorn.Server(uvicorn.Config(
            self.app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
            loop="asyncio"
        ))
        # Configure the server to listen on all interfaces
        self.thread = threading.Thread(
            target=self.server.run,
            daemon=True
        )
        self.thread.start()
        time.sleep(0.05)
        
    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=1)

@pytest.fixture(scope="function")
def mock_api_server():
    server = MockAPIServer()
    server.start()
    yield server
    server.stop()

@pytest.fixture
def smtp_client():
    # Wait for Postfix to be ready
    max_retries = 100
    for attempt in range(max_retries):
        try:
            # First check if port is open
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((MTA_HOST, 25))
                
            # Then try SMTP connection
            client = smtplib.SMTP(MTA_HOST, 25)
            logger.info("SMTP connection established")
            break
        except (ConnectionRefusedError, smtplib.SMTPConnectError, socket.error) as e:
            if attempt == max_retries - 1:
                raise
            if attempt % 20 == 0:
                logger.warning(f"SMTP connection attempt {attempt+1} failed ({str(e)}), retrying in 1s...")
            time.sleep(0.1)
    
    yield client
    try:
        client.quit() 
    except smtplib.SMTPServerDisconnected:
        pass
