# Albert Chatbot

This module provides a chatbot implementation using the Albert API from Etalab for processing emails with the following capabilities:

## Features

1. **Mail Summarization**: Automatically summarize email content with key points and urgency assessment
2. **Answer Generation**: Generate professional responses to emails with configurable tone
3. **Mail Classification**: Classify emails into categories (urgent, normal, spam, etc.)

## Configuration

The chatbot uses the Albert API with the following configuration:

```python
from deep_search import AlbertChatbot, AlbertConfig

# Default configuration
config = AlbertConfig(
    name="albert-etalab",
    base_url="https://albert.api.etalab.gouv.fr/v1",
    api_key="sk-eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    model="albert-large"
)

chatbot = AlbertChatbot(config)
```

## Usage Examples

### 1. Summarize an Email

```python
from deep_search import get_chatbot

chatbot = get_chatbot()

result = chatbot.summarize_mail(
    mail_content="Bonjour, je souhaiterais avoir des informations...",
    sender="user@example.com",
    subject="Demande d'informations"
)

if result['success']:
    summary = result['summary']
    print(f"Résumé: {summary['summary']}")
    print(f"Points clés: {summary['key_points']}")
    print(f"Action requise: {summary['action_required']}")
    print(f"Urgence: {summary['urgency_level']}")
```

### 2. Generate an Email Response

```python
result = chatbot.generate_mail_answer(
    original_mail="Bonjour, pouvez-vous m'aider avec...",
    context="L'utilisateur a besoin d'aide technique",
    tone="professional"
)

if result['success']:
    response = result['response']
    print(f"Réponse: {response['response']}")
    print(f"Sujet: {response['subject']}")
```

### 3. Classify an Email

```python
result = chatbot.classify_mail(
    mail_content="URGENT: Problème avec le service...",
    sender="support@example.com",
    subject="Problème urgent"
)

if result['success']:
    classification = result['classification']
    print(f"Catégorie: {classification['primary_category']}")
    print(f"Confiance: {classification['confidence_score']}")
    print(f"Justification: {classification['reasoning']}")
```

### 4. Batch Processing

```python
mails = [
    {
        'content': 'Email 1 content...',
        'sender': 'user1@example.com',
        'subject': 'Subject 1'
    },
    {
        'content': 'Email 2 content...',
        'sender': 'user2@example.com', 
        'subject': 'Subject 2'
    }
]

results = chatbot.process_mail_batch(mails, operation="summarize")

for result in results:
    if result['success']:
        print(f"Mail {result['batch_index']}: {result['summary']['summary']}")
```

## Mail Classifications

The chatbot can classify emails into the following categories:

- `urgent`: Emails requiring immediate attention
- `normal`: Standard emails
- `low_priority`: Non-urgent emails
- `spam`: Unwanted emails
- `information`: Informational emails
- `request`: Service requests
- `complaint`: Complaints or issues
- `support`: Technical support requests

## Error Handling

All functions return a consistent response format:

```python
{
    'success': True,  # or False
    'result_data': {...},  # Specific to each function
    'error': 'Error message',  # Only present if success=False
    # Additional metadata...
}
```

## Logging

The chatbot uses Django's logging system with appropriate log levels:

- `INFO`: Normal operations and successful API calls
- `ERROR`: API failures and processing errors
- `DEBUG`: Detailed debugging information (development only)

## API Rate Limiting

The chatbot respects the Albert API rate limits. For high-volume processing, consider:

1. Using batch processing functionality
2. Implementing retry logic with exponential backoff
3. Monitoring API usage and quotas

## Security

- API keys are handled securely through configuration
- No sensitive information is logged
- All API communications use HTTPS
- Function calling ensures structured responses

## Testing

Test the real Albert API integration with:

```bash
# Test individual functions
python chatbot/test_focused_api.py --function connection
python chatbot/test_focused_api.py --function summarize
python chatbot/test_focused_api.py --function classify
python chatbot/test_focused_api.py --function answer

# Run comprehensive real API tests
python chatbot/test_real_api.py
```

The test suite includes:
- Real Albert API integration tests
- Individual function testing
- Error handling scenarios
- Batch processing with real API
- Complete workflow testing
