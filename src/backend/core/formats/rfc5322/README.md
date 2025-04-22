# RFC5322 Email Parser

This module provides a centralized approach to parsing email messages in the st-messages application.

## Overview

The parser uses the [Flanker](https://github.com/mailgun/flanker) library to handle robust email parsing according to RFC5322 standards. Flanker is a sophisticated parsing library developed by Mailgun, designed to handle the challenges of parsing email addresses and messages.

The parser follows the [JMAP specification](https://jmap.io/spec-mail.html#properties-of-the-email-object) for email object properties, particularly for body content representation.

## Features

- Parse email addresses with or without display names
- Parse lists of email addresses from comma-separated strings
- Decode email headers that might contain encoded text
- Parse RFC5322 date strings
- Extract body content in JMAP-compatible format (multiple text/HTML parts)
- Support for email attachments
- Fully parse raw email data into a structured dictionary
- Pure Flanker-based implementation with no fallbacks to maintain consistency

## JMAP Compatibility

The parser structures email body content to follow the JMAP specification:

- `textBody`: Array of text/plain body parts
- `htmlBody`: Array of text/html body parts
- `attachments`: Array of attachment information

Each body part contains:
- `partId`: A unique identifier for the part
- `type`: MIME type of the part
- `charset`: Character encoding
- `content`: The actual content text

## Implementation Philosophy

Rather than implementing fallbacks to different libraries or manual parsing when Flanker fails, we've adopted a more principled approach:

1. **Pure Flanker implementation**: Using a single parsing library ensures consistent behavior
2. **Clear error boundaries**: When parsing fails, it fails with a descriptive error rather than degrading silently
3. **JMAP compatibility**: Following standards for future-proof integration

This approach provides cleaner code, more consistent behavior, and better error reporting while leveraging the full capabilities of the Flanker library.

## Integration

The parser is integrated into the MTA viewset to handle incoming emails. Instead of directly using the Python standard library's email module, the viewset now uses our centralized parser, making email handling:

1. More robust with better error handling
2. Standardized across the application
3. Easier to maintain with all parsing logic in one place
4. Compatible with JMAP for future integration

## Testing

A comprehensive test suite is included to verify the correctness of the parser functions:

- Email address parsing tests
- Header decoding tests
- Date parsing tests
- Message content parsing tests (JMAP format)
- Attachment handling tests
- Full email parsing tests with edge cases

## Usage

```python
from core.formats.rfc5322 import parse_email_message, EmailParseError

try:
    # Parse raw email data
    parsed_email = parse_email_message(raw_email_data)
    
    # Access parsed data
    subject = parsed_email["subject"]
    sender_name = parsed_email["from"]["name"]
    sender_email = parsed_email["from"]["email"]
    
    # Access body content in JMAP format
    text_parts = parsed_email["textBody"]  # List of text parts
    html_parts = parsed_email["htmlBody"]  # List of HTML parts
    
    # For each text part
    for part in text_parts:
        part_id = part["partId"]
        content = part["content"]
        mime_type = part["type"]
        charset = part["charset"]
    
    # Access recipients
    to_recipients = parsed_email["to"]  # List of {"name": "...", "email": "..."} dicts
    cc_recipients = parsed_email["cc"]
    bcc_recipients = parsed_email["bcc"]
    
    # Access attachments
    attachments = parsed_email["attachments"]
    
except EmailParseError as e:
    # Handle parsing errors
    print(f"Failed to parse email: {e}")
```

## Dependencies

- Flanker >= 0.9.11
- Python >= 3.8 