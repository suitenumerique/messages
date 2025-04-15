#!/usr/bin/env python3
import sys
import os
import requests
import jwt
import datetime
import json
import hashlib
from api.mda import mda_api_call

def check_recipient(address):
    response = mda_api_call("check-recipients/", "application/json", json.dumps({
        "addresses": [address]
    }).encode('utf-8'), {})
    return bool(response.get(address))
    
def main():
    while True:
        attrs = {}
        # Read input until empty line
        while True:
            line = sys.stdin.readline()
            if not line or line == "\n":
                break
            key, value = line.strip().split("=", 1)
            attrs[key] = value

        recipient = attrs.get("recipient")
        if not recipient:
            print("action=REJECT No recipient\n", flush=True)
            continue

        try:
            result = check_recipient(recipient)
        except Exception as e:
            print(f"action=REJECT Error: {e}\n", flush=True)
            continue

        if result is False:
            print("action=REJECT User unknown\n", flush=True)
        else:
            print("action=DUNNO\n", flush=True)

if __name__ == "__main__":
    main()
