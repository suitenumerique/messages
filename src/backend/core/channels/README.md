# Channels

The Channel feature allows the application to receive messages from various sources beyond traditional email, such as web widgets, forms, and APIs.

## Overview

Channels provide a flexible way to integrate external message sources into the existing email infrastructure. Each channel has a specific type (e.g., "widgets", "mta") and can be configured with custom settings.

The system includes a generic inbound API that replaces the previous MTA-specific endpoints, providing a unified interface for all channel types.

## Architecture

### Models

- **Channel**: Stores channel configuration and metadata
  - `name`: Human-readable name for the channel
  - `type`: Type of channel (e.g., "widgets", "webform", "api")
  - `settings`: JSON field for channel-specific configuration
  - `mailbox`: Optional foreign key to a specific mailbox
  - `maildomain`: Optional foreign key to a mail domain

### Channel Processors

Each channel type has a corresponding processor class that handles incoming messages:

- **WidgetsChannel**: Handles messages from web widgets
  - Validates API keys
  - Creates Contact objects for senders
  - Converts widget data to email format
  - Creates Message and Thread objects

- **MTAChannel**: Handles email messages from Mail Transfer Agents
  - Checks recipient deliverability
  - Parses and delivers email messages
  - Integrates with existing email infrastructure

### API Endpoints

- **POST `/api/v1.0/inbound/{channel_type}/check/`**: Check recipient addresses or other delivrability criteria
- **POST `/api/v1.0/inbound/{channel_type}/deliver/`**: Deliver incoming messages
- **GET/POST/PUT/DELETE `/api/v1.0/channels/`**: Admin interface for managing channels
