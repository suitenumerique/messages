# ST Messages MPA (Mail Processing Agent)

The MPA container provides spam filtering capabilities using rspamd.

It exposes rspamd's HTTP API for spam checking via the `/checkv2` endpoint, which is used by the backend to analyze incoming messages before delivery.

The service is entirely stateless and configurable via environment variables.

## Architecture

The MPA container runs:
- **rspamd**: The spam filtering engine
- **nginx**: Reverse proxy for the rspamd HTTP API

After receiving an email, the backend sends it to rspamd's `/checkv2` API endpoint for spam analysis. The response includes:
- `action`: The recommended action (`reject`, `add header`, `greylist`, or `no action`)
- `score`: The spam score
- `required_score`: The threshold for rejection
- `is_skipped`: Whether the check was skipped

Messages with `action: "reject"` are marked as spam and stored with the `is_spam` flag set.

## Configuration

The service is configured via environment variables:
- `RSPAMD_password`: Password for rspamd API authentication
- `PORT`: Port on which nginx listens (default: 8010)

## API Usage

The rspamd API is accessible at `http://mpa:8010/_api/checkv2` (internal) or `http://localhost:8918/_api/checkv2` (external).

Requests should include:
- `Content-Type: message/rfc822` header
- `Authorization: {RSPAMD_AUTH}` header (where `RSPAMD_AUTH` is the configured auth value, e.g., `Bearer password` or just `password`)
- Raw email message bytes in the request body

Example response:
```json
{
  "is_skipped": false,
  "score": 15.0,
  "required_score": 15.0,
  "action": "reject",
  "thresholds": {
    "reject": 15.0,
    "add header": 6.0,
    "greylist": 4.0
  },
  "symbols": {...},
  "messages": {},
  "time_real": 0.184484
}
```

## Testing

To run the tests, go to the repository root and do:

```
make mpa-test
```

The test suite includes:
- Health check verification
- Empty message spam detection
- Simple valid message processing

