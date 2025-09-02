# Exported Prometheus Metrics

This service exposes the following custom Prometheus metrics via the `api/v1.0/prometheus/metrics` endpoint, in addition to all metrics exported via [django-prometheus](https://github.com/django-commons/django-prometheus).

Custom metrics are collected from the database using Django ORM and are available when the application is running.

## Metrics

### Message Status Counts

This metric is exported with labels corresponding to each possible message delivery status.

**Metric:**
```
message_status_count{status="<status>"}
```

**Example:**
- `message_status_count{status="retry"}`
- `message_status_count{status="sent"}`

**Description:**
Number of messages with the given delivery status. If no messages exist for a status, the value is `0`.

---

### Attachment Count

**Metric:**
```
attachment_count
```

**Description:**
Total number of attachments in the database.

---

### Attachments Total Size

**Metric:**
```
attachments_total_size_bytes
```

**Description:**
Total size (in bytes) of all attachments, summed over the `blob.size` field.


