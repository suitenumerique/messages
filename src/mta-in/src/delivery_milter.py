#!/usr/bin/env python3
"""
Postfix milter for synchronous email delivery during SMTP session.

This milter processes messages before they are queued and performs
delivery immediately. If delivery fails, the SMTP session is rejected.
"""

import json
import os
import sys
from io import BytesIO

import Milter

from api.mda import mda_api_call


class DeliveryMilter(Milter.Base):
    """
    Milter that performs synchronous delivery during SMTP session.

    This milter:
    1. Collects message data during SMTP session
    2. Performs delivery via MDA API when message is complete
    3. Accepts/rejects SMTP session based on delivery result
    """

    def __init__(self):
        self.reset_state()

    def reset_state(self):
        """Reset milter state for a new message"""
        self.mailfrom = None
        self.rcpttos = []
        self.message_data = BytesIO()

    def connect(self, IPname, family, hostaddr):
        """Called when SMTP client connects"""
        self.reset_state()  # Reset state for new connection

        # Extract client connection info
        self.client_addr = hostaddr[0] if hostaddr else None
        self.client_port = str(hostaddr[1]) if hostaddr and len(hostaddr) > 1 else None
        self.client_hostname = IPname or None

        return Milter.CONTINUE

    def envfrom(self, mailfrom, *str):
        """Called for MAIL FROM command"""
        # Reset state for new message (in case of multiple messages per connection)
        self.reset_state()

        # Strip angle brackets from sender address
        self.mailfrom = mailfrom.strip("<>")
        return Milter.CONTINUE

    def envrcpt(self, to, *str):
        """Called for each RCPT TO command - validate recipient and collect"""
        # Strip angle brackets from recipient address
        clean_to = to.strip("<>")

        try:
            # Check if recipient exists via MDA API
            status_code, response = mda_api_call(
                "check-recipients/",
                "application/json",
                json.dumps({"addresses": [clean_to]}).encode("utf-8"),
                {},
            )

            if status_code != 200:
                # API error - temporary failure
                return Milter.TEMPFAIL

            # Check if recipient exists
            recipient_exists = response.get(clean_to, False)

            if not recipient_exists:
                # Recipient doesn't exist - permanent failure
                return Milter.REJECT

            # Recipient exists - add to list and continue
            self.rcpttos.append(clean_to)
            return Milter.CONTINUE

        except Exception:
            # Exception during validation - temporary failure
            return Milter.TEMPFAIL

    def header(self, name, hval):
        """Called for each header"""
        header_line = f"{name}: {hval}\r\n"
        self.message_data.write(header_line.encode("utf-8"))
        return Milter.CONTINUE

    def eoh(self):
        """Called at end of headers"""
        self.message_data.write(b"\r\n")  # Empty line separating headers from body
        return Milter.CONTINUE

    def body(self, chunk):
        """Called for each body chunk"""
        self.message_data.write(chunk)
        return Milter.CONTINUE

    def eom(self):
        """
        Called at end of message - this is where we do synchronous delivery.

        Returns:
            Milter.DISCARD: Message accepted (delivery succeeded)
            Milter.REJECT: Message rejected (delivery failed permanently)
            Milter.TEMPFAIL: Temporary failure (delivery failed temporarily)
        """
        try:
            # Get complete message content
            message_content = self.message_data.getvalue()

            # Calculate message size
            message_size = str(len(message_content))

            # Perform synchronous delivery via MDA API
            status_code, response = mda_api_call(
                "inbound-email/",
                "message/rfc822",
                message_content,
                {
                    "sender": self.mailfrom,
                    "original_recipients": self.rcpttos,
                    "client_address": self.client_addr,
                    "client_port": self.client_port,
                    "client_hostname": self.client_hostname,
                    "client_helo": self.client_helo,
                    "size": message_size,
                },
            )

            if status_code == 200 and response.get("status") == "ok":
                # CRITICAL: Discard the message to prevent normal Postfix processing
                # This prevents duplication and ensures only milter delivery happens
                return Milter.DISCARD
            else:
                return Milter.TEMPFAIL

        except Exception:
            return Milter.TEMPFAIL

    def close(self):
        """Called when connection is closed"""
        return Milter.CONTINUE

    def hello(self, heloname):
        """Called for HELO/EHLO command"""
        self.client_helo = heloname
        return Milter.CONTINUE


def main():
    """Run the milter server"""
    print("Starting delivery milter...")

    # Set the socket for milter communication
    # Use Unix socket for better performance and security
    socket_path = "unix:/var/spool/postfix/milter/delivery.sock"

    # Create directory if it doesn't exist
    os.makedirs("/var/spool/postfix/milter", exist_ok=True)

    # Register our milter class
    Milter.factory = DeliveryMilter

    # Set milter flags - we want to see all message content
    flags = Milter.CHGBODY + Milter.CHGHDRS + Milter.ADDHDRS
    Milter.set_flags(flags)

    print(f"Milter listening on {socket_path}")

    try:
        # Start the milter
        Milter.runmilter("delivery_milter", socket_path, timeout=240)
    except KeyboardInterrupt:
        print("Milter shutting down...")
    except Exception as e:
        print(f"Milter error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
