#!/usr/bin/env python3
"""
See available env vars here. Must be passed in master.cf first.
https://www.postfix.org/postconf.5.html#mailbox_command

This script is used to pass emails to the MDA API.

"""

import sys
import os
import requests
import datetime
import traceback
import time
import socket
import jwt
import hashlib

with open("/etc/st-messages/env/MDA_API_URL", "r") as f:
    MDA_API_URL = f.read().strip()

with open("/etc/st-messages/env/MDA_API_SECRET", "r") as f:
    MDA_API_SECRET = f.read().strip()

def log(message):
    print(message)

def main():
    try:
        client_address, client_helo, client_hostname, client_port, client_protocol, queue_id, sender, size = sys.argv[1:9]
        original_recipients = sys.argv[9:]
        
        # Read raw email from stdin. After this, it seems stdout won't be read by postfix.
        raw_email = sys.stdin.read()
        log(f"Received raw email data: {len(raw_email)} bytes.")

        raw_email_hash = hashlib.sha256(raw_email.encode('utf-8')).hexdigest()

        jwt_token = jwt.encode(
            {
                "exp": datetime.datetime.now() + datetime.timedelta(seconds=60),
                "client_address": client_address,
                "client_helo": client_helo,
                "client_hostname": client_hostname,
                "client_port": client_port,
                "client_protocol": client_protocol,
                "original_recipients": original_recipients,
                "queue_id": queue_id,
                "sender": sender,
                "size": size,
                "email_hash": raw_email_hash
            },
            MDA_API_SECRET,
            algorithm="HS256"
        )

        log(f"Sending raw email to {MDA_API_URL}")
        headers = {
            'Content-Type': 'message/rfc822',
            'Authorization': f'Bearer {jwt_token}'
        }
        response = requests.post(MDA_API_URL, data=raw_email, headers=headers)
        log(f"Response: {response.status_code}")
        log(response.text)
        return 0
    except Exception as e:
        log(f"Error in forwarding: {str(e)}")
        log(traceback.format_exc())
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code) 