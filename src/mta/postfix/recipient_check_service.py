#!/usr/bin/env python3
import sys
import os
import requests

with open("/etc/st-messages/env/REGIE_API_URL", "r") as f:
    REGIE_API_URL = f.read().strip()

def check_recipient(address):

    try:
        response = requests.post(REGIE_API_URL, json={"address": address}, timeout=30)
        return response.json().get("exists", False)          
    except Exception as e:
        # In case of error, default to allowing the message
        return False

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
        if recipient and check_recipient(recipient):
            print("action=DUNNO\n")
        else:
            print("action=REJECT User unknown\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
