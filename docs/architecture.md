# Architecture

## System Architecture Overview

```mermaid
graph TB
    %% External actors
    ExternalSender[External Email Sender]
    User[User/Browser]

    %% Frontend
    Frontend[Next.js Frontend<br/>React + TypeScript]

    %% Backend services
    subgraph "Core Services"
        Backend[Django REST API<br/>Backend Service]
        Celery[Celery Worker<br/>Async Task Processing]

        %% Backend components
        subgraph "Backend Components"
            API[REST API Endpoints]
            MDA[Mail Delivery Agent]
            Search[Search Service]
            Tasks[Async Tasks]
        end
    end

    %% Mail Transfer Agents
    subgraph "Mail Processing Layer"
        MTAIn[MTA-In<br/>Postfix + Python]
        MTAOut[MTA-Out<br/>Postfix]
        MPA[Mail Processing Agent<br/>rspamd]
    end

    %% Data layer
    subgraph "Data Layer"
        PostgreSQL[(PostgreSQL<br/>Primary Database)]
        Redis[(Redis<br/>Cache & Message Broker)]
        Elasticsearch[(Elasticsearch<br/>Search Index)]
        S3[(S3 Storage<br/>File Attachments)]
    end

    %% Authentication
    subgraph "Authentication"
        Keycloak[Keycloak<br/>OIDC Provider]
    end

    %% Development tools
    subgraph "Development Support"
        MailCatcher[MailCatcher<br/>Email Testing]
        Flower[Flower<br/>Celery Monitoring]
        ElasticUI[ElasticVue<br/>Search Monitoring]
    end

    %% External flows
    ExternalSender -->|SMTP| MTAIn
    User -->|HTTPS| Frontend

    %% Frontend to backend
    Frontend -->|REST API| Backend
    Frontend -->|Authentication| Keycloak

    %% Backend internal flows
    Backend --> API
    Backend --> MDA
    Backend --> Search
    Backend --> Tasks

    %% Mail processing flows
    MTAIn -->|Recipient Validation| Backend
    MTAIn -->|Message Delivery| MDA
    Backend -->|Send Email| MTAOut
    MTAOut -->|External Relay| MailCatcher
    MPA -->|Filter/Process| MTAIn

    %% Data access
    Backend --> PostgreSQL
    Backend --> Redis
    Backend -->|Direct Queries| Elasticsearch
    Backend --> S3
    Celery --> Redis
    Celery --> PostgreSQL

    %% Async processing
    Backend -->|Queue Tasks| Celery
    Celery -->|Async Indexing| Elasticsearch

    %% Development monitoring
    Celery -.-> Flower
    Elasticsearch -.-> ElasticUI
    MTAOut -.-> MailCatcher

    %% Authentication flow
    Backend -->|Verify Tokens| Keycloak
```

## Core Components

### Frontend Layer

- **Next.js Application**: React-based SPA with TypeScript
- **Auto-generated API Client**: Generated from OpenAPI schema using Orval
- **Multi-panel Interface**: Mailbox panel, thread list, and message view
- **Real-time Updates**: Using TanStack Query for efficient state management

### Backend Services

- **Django REST Framework**: Main API service handling business logic
- **Celery Workers**: Asynchronous task processing for heavy operations
- **Mail Delivery Agent (MDA)**: Email processing and parsing
- **Search Service**: Elasticsearch integration for full-text search

### Mail Transfer Layer

- **MTA-In (Inbound)**: Postfix server with Python-based recipient validation
- **MTA-Out (Outbound)**: Postfix server for email delivery and relay
- **Mail Processing Agent**: rspamd for spam filtering and mail processing

### Data Storage

- **PostgreSQL**: Primary relational database for all structured data
- **Redis**: Caching layer and Celery message broker
- **Elasticsearch**: Full-text search index for messages and threads
- **S3-Compatible Storage**: File and attachment storage

### Authentication & Authorization

- **Keycloak**: OIDC provider for user authentication
- **Role-based Access**: Multi-tenant access control via mailbox and thread permissions

## Data Flow

### Inbound Email Processing

1. External email arrives at **MTA-In** via SMTP
2. **MTA-In** validates recipients against Django backend
3. Valid emails are processed by **rspamd** for filtering
4. **MDA** parses and stores messages in PostgreSQL
5. **Celery** tasks index content in Elasticsearch
6. Users see new messages in real-time via frontend

### Outbound Email Processing

1. User composes message in frontend
2. Frontend sends draft via REST API
3. Backend validates and queues message
4. **Celery** processes sending via **MTA-Out**
5. **MTA-Out** delivers email externally or to MailCatcher (dev)

### Search Operations

1. User submits search query via frontend
2. Backend directly queries Elasticsearch for real-time results
3. Results are ranked and filtered by permissions
4. Frontend displays paginated results

### Search Indexing

1. New messages/threads are saved to PostgreSQL
2. Backend queues indexing tasks to Celery
3. Celery workers asynchronously index content in Elasticsearch
4. Heavy operations (bulk imports, reindexing) are handled via Celery

## Key Features

### Multi-tenancy

- **Domain-based**: Mail domains with administrative roles
- **Mailbox-based**: Individual mailbox access permissions
- **Thread-based**: Granular access control for conversations

### Scalability

- **Microservices Architecture**: Independent scaling of components
- **Async Processing**: Non-blocking operations via Celery
- **Caching Strategy**: Redis for session and query caching
- **Search Optimization**: Elasticsearch for fast full-text search

### Development Experience

- **OpenAPI-First**: Auto-generated client from backend schema
- **Docker Compose**: Complete development environment
- **Hot Reloading**: Frontend and backend development servers
- **Testing Tools**: Comprehensive test suites and monitoring

## Security Considerations

### Authentication

- OIDC integration with Keycloak
- JWT token validation
- Session management via Redis

### Authorization

- Role-based access control (RBAC)
- Resource-level permissions
- Multi-tenant isolation

### Email Security

- DKIM signing for outbound messages
- SPF and DMARC policy enforcement
- Anti-spam filtering via rspamd

### Data Protection

- Encrypted storage for sensitive data
- Secure file upload handling
- CORS and CSRF protection

## Deployment Architecture

The system is designed for containerized deployment with:

- **Docker containers** for all services
- **Environment-specific configurations** (dev, staging, production)
- **Horizontal scaling** capability for backend and Celery workers
- **Load balancing** support via nginx reverse proxy
- **Health checks** and monitoring integration
