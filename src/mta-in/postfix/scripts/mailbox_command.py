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
from api.mda import mda_api_call

sys.stdin.reconfigure(encoding='ascii')

def log(message):
    print(message)

def main():
    try:
        client_address, client_helo, client_hostname, client_port, client_protocol, queue_id, sender, size = sys.argv[1:9]
        original_recipients = sys.argv[9:]
        
        # Read raw email from stdin. After this, it seems stdout won't be read by postfix.
        raw_email = sys.stdin.read().encode('ascii')
        log(f"Received raw email data: {len(raw_email)} bytes.")

        response = mda_api_call("inbound-email/", 'message/rfc822', raw_email, {
            "client_address": client_address,
            "client_helo": client_helo,
            "client_hostname": client_hostname,
            "client_port": client_port,
            "client_protocol": client_protocol,
            "original_recipients": original_recipients,
            "queue_id": queue_id,
            "sender": sender,
            "size": size,
        })
        log(f"Response: {response}")    
        return 0
    except Exception as e:
        log(f"Error in forwarding: {str(e)}")
        log(traceback.format_exc())
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code) 