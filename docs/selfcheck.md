# Selfcheck System

The selfcheck system provides end-to-end testing of the mail delivery pipeline to ensure that the entire system is working correctly.

## Overview

The selfcheck system performs the following operations:

1. **Creates test mailboxes** for the configured FROM and TO addresses if they don't exist
2. **Creates a test message** with a unique secret in the body
3. **Sends the message** via the outbound system using `prepare_outbound_message` and `send_message(force_mta_out=True)`
4. **Waits for message reception** by polling the target mailbox for a message containing the secret
5. **Verifies message integrity** by checking that the received message contains the secret and has proper structure
6. **Cleans up test data** by deleting the test message and thread (but keeping the mailboxes)
7. **Times all operations** and provides detailed metrics

## Configuration

The selfcheck system uses the following environment variables:

- `MESSAGES_SELFCHECK_FROM`: Email address to send from (for instance: `selfcheck@example.local`)
- `MESSAGES_SELFCHECK_TO`: Email address to send to (for instance: `selfcheck-receiver@example.local`)
- `MESSAGES_SELFCHECK_SECRET`: Secret string to include in the message body (for instance: `selfcheck-secret-xyz`)
- `MESSAGES_SELFCHECK_INTERVAL`: Interval in seconds between self-checks (for instance: `600` - 10 minutes)
- `MESSAGES_SELFCHECK_TIMEOUT`: Timeout in seconds for message reception (for instance: `60` - 60 seconds)

As well as these prometheus specific environment variables:

- `MESSAGES_SELFCHECK_PROMETHEUS_METRICS_ENABLED`: Enable or disable Prometheus metrics reporting (default: `False`)
- `MESSAGES_SELFCHECK_PROMETHEUS_METRICS_PUSHGATEWAY_URL`: URL of the Prometheus Pushgateway to which metrics are sent (default: `None`)
- `MESSAGES_SELFCHECK_PROMETHEUS_METRICS_PREFIX`: Prefix for all Prometheus metrics names (default: empty string)

## Usage

### Manual Execution

Run the selfcheck manually using the Django management command:

```bash
# Run with default settings
python manage.py selfcheck

# Run with verbose output
python manage.py selfcheck --verbose
```

### Scheduled Execution

The selfcheck runs automatically every 10 minutes via Celery Beat. The interval can be configured using the `MESSAGES_SELFCHECK_INTERVAL` setting.

## Response Format

The selfcheck returns simplified timing metrics:

```json
{
  "success": true,
  "error": null,
  "send_time": 0.15,
  "reception_time": 2.34
}
```

## Error Handling

If the selfcheck fails, it will return an error message and attempt to clean up any test data that was created. Common failure scenarios include:

- **Message preparation failure**: The outbound message preparation failed
- **Message sending failure**: The message could not be sent via the MTA
- **Reception timeout**: The message was not received within the timeout period (configurable via `MESSAGES_SELFCHECK_TIMEOUT`)
- **Integrity verification failure**: The received message does not contain the expected secret or has structural issues

## Logging

The selfcheck system logs all operations with appropriate log levels:

- `INFO`: Normal operation progress
- `WARNING`: Non-critical issues (e.g., parsing errors for individual messages)
- `ERROR`: Critical failures that cause the self-check to fail

The selfcheck results can be integrated with monitoring systems by:

1. **Checking the success status** of the selfcheck task
2. **Monitoring timing metrics** to detect performance degradation
3. **Alerting on failures** to quickly identify delivery pipeline issues
4. **Tracking trends** in reception times to identify system bottlenecks

## Monitoring

By setting `MESSAGES_SELFCHECK_PROMETHEUS_METRICS_ENABLED` to `True` as well as setting `MESSAGES_SELFCHECK_PROMETHEUS_METRICS_PUSHGATEWAY_URL` to your [prometheus pushgateway](https://github.com/prometheus/pushgateway)'s url, the job will push the following metrics:

- `selfcheck_start_time`: Start timestamp of the self check
- `selfcheck_end_time`: End timestamp of the self check
- `selfcheck_success`: 1 if the self check succeeded, 0 if it failed
- `selfcheck_send_duration_seconds`: Time taken to send the test message (seconds), only on successful send
- `selfcheck_reception_duration_seconds`: Time taken to receive the test message (seconds), only on successful reception

All metric names can be prefixed using the `MESSAGES_SELFCHECK_PROMETHEUS_METRICS_PREFIX` environment variable.

## Security Considerations

- The selfcheck uses dedicated test mailboxes that are separate from user data
- Test messages are automatically cleaned up after verification
- The secret string is configurable to prevent predictable patterns
- All test data is isolated from production user data
