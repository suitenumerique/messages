"""Storage-usage computation shared by the metrics API and the
mailbox entitlements/quota backends.

Storage for a mailbox is the sum of:

- ``messages_count * OVERHEAD`` — a flat per-message overhead
  (``METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE``) standing in for the
  Postgres row/index cost that is not captured by blob sizes;
- the compressed size of every blob reachable from the mailbox through
  its threads (raw MIME bodies and draft bodies) and its own attachments
  and message templates.

All blob sizes are counted through the message/attachment relationships
(via ``ThreadAccess``), never through ``blob.mailbox``. This is the exact
computation the global metrics endpoint exposes; keeping it in one place
means the in-app quota gauge and the external metrics scrape can never
drift apart.

SHARED-BLOB BASIS (settled — do not "fix" this to dedupe).
Blobs are sha-deduplicated, so one Blob row can back several messages (most
plausibly two drafts with identical bodies, or a re-imported message). This
per-mailbox figure counts such a blob ONCE PER REFERENCING MESSAGE — the
"storage felt" by the mailbox, not the physical bytes on disk. That is the
intended basis:

- It is pinned by ``test_blobs_with_identical_sizes_counted_separately`` and is
  the same number reported to the entitlements API (``deploycenter`` POSTs it in
  ``_build_usage_metrics``), so it is the quota basis on both sides.
- The plain ``Sum`` below is therefore correct on purpose. The *per-domain*
  metrics endpoint answers a different question (physical bytes across a whole
  domain) and deliberately deduplicates with ``SELECT DISTINCT b.id`` — that
  asymmetry is intentional, not a bug. Measured impact of the difference on real
  data is nil (normal received mail never shares a blob within one mailbox).
"""

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from core.models import Attachment, Blob, Mailbox, Message, MessageTemplate


def mailbox_storage_used_expr(overhead=None):
    """Return a Django expression computing ``storage_used`` for a mailbox.

    Meant to annotate a ``Mailbox`` queryset (``OuterRef("pk")`` resolves to
    the mailbox row). Subqueries are used to avoid the cross-product a naive
    multi-join aggregate would produce.

    Args:
        overhead: Per-message overhead in bytes. Defaults to
            ``settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE``.
    """
    if overhead is None:
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

    messages_count_subquery = Subquery(
        Message.objects.filter(thread__accesses__mailbox=OuterRef("pk"))
        .order_by()
        .values("thread__accesses__mailbox")
        .annotate(cnt=Count("id", distinct=True))
        .values("cnt")[:1]
    )

    # Raw MIME blobs linked via Message.blob
    mime_blobs_subquery = Subquery(
        Blob.objects.filter(messages__thread__accesses__mailbox=OuterRef("pk"))
        .order_by()
        .values("messages__thread__accesses__mailbox")
        .annotate(total=Sum("size_compressed"))
        .values("total")[:1]
    )

    # Draft body blobs linked via Message.draft_blob
    draft_blobs_subquery = Subquery(
        Blob.objects.filter(drafts__thread__accesses__mailbox=OuterRef("pk"))
        .order_by()
        .values("drafts__thread__accesses__mailbox")
        .annotate(total=Sum("size_compressed"))
        .values("total")[:1]
    )

    # Attachment blobs linked via Attachment.mailbox
    attachment_blobs_subquery = Subquery(
        Attachment.objects.filter(mailbox=OuterRef("pk"))
        .order_by()
        .values("mailbox")
        .annotate(total=Sum("blob__size_compressed"))
        .values("total")[:1]
    )

    # Template/signature blobs linked via MessageTemplate.mailbox
    template_blobs_subquery = Subquery(
        MessageTemplate.objects.filter(mailbox=OuterRef("pk"), blob__isnull=False)
        .order_by()
        .values("mailbox")
        .annotate(total=Sum("blob__size_compressed"))
        .values("total")[:1]
    )

    return (
        Coalesce(messages_count_subquery, Value(0)) * overhead
        + Coalesce(mime_blobs_subquery, Value(0))
        + Coalesce(draft_blobs_subquery, Value(0))
        + Coalesce(attachment_blobs_subquery, Value(0))
        + Coalesce(template_blobs_subquery, Value(0))
    )


def compute_mailbox_storage_used(mailbox):
    """Return the storage used, in bytes, by a single mailbox."""
    return (
        Mailbox.objects.filter(pk=mailbox.pk)
        .annotate(storage_used=mailbox_storage_used_expr())
        .values_list("storage_used", flat=True)
        .first()
    ) or 0


def compute_organization_storage_used(account_id_key, account_id_value):
    """Return the storage used, in bytes, by an organization.

    The organization is the set of mailboxes whose domain carries
    ``custom_attributes[account_id_key] == account_id_value`` (e.g. all
    mailboxes of every maildomain sharing the same SIRET).
    """
    return (
        Mailbox.objects.filter(
            **{f"domain__custom_attributes__{account_id_key}": account_id_value}
        )
        .annotate(storage_used=mailbox_storage_used_expr())
        .aggregate(total=Coalesce(Sum("storage_used"), Value(0)))["total"]
    )


# ---------------------------------------------------------------------------
# Cached accessors
#
# The ``compute_*`` functions above run five correlated subqueries and take on
# the order of 100ms for a large mailbox, so the quota gauge/entitlements read
# through a short-TTL cache instead of recomputing on every sidebar load. The
# *metrics* endpoints deliberately keep calling the ``compute_*`` (live), since
# a scrape wants a current number and runs rarely.
#
# The TTL is the workhorse: it bounds staleness for the up-direction (new mail,
# sends) without touching those hot paths. ``invalidate_mailbox_storage`` is the
# nicety for the one down-event a user actively watches — emptying the trashbin
# — so the freed space shows immediately rather than after the TTL. The
# organization total is left to the TTL (it would need the backend-specific
# claim to key precisely, and a ~minute lag on the coarser org gauge is fine).
# ---------------------------------------------------------------------------


def _mailbox_storage_cache_key(mailbox_id):
    return f"storage_used:mailbox:{mailbox_id}"


def _organization_storage_cache_key(account_id_key, account_id_value):
    return f"storage_used:org:{account_id_key}:{account_id_value}"


def get_mailbox_storage_used(mailbox):
    """Cached ``compute_mailbox_storage_used``; see module note above."""
    ttl = settings.STORAGE_USAGE_CACHE_TTL
    if not ttl:
        return compute_mailbox_storage_used(mailbox)
    return cache.get_or_set(
        _mailbox_storage_cache_key(mailbox.pk),
        lambda: compute_mailbox_storage_used(mailbox),
        ttl,
    )


def get_organization_storage_used(account_id_key, account_id_value):
    """Cached ``compute_organization_storage_used``; see module note above."""
    ttl = settings.STORAGE_USAGE_CACHE_TTL
    if not ttl:
        return compute_organization_storage_used(account_id_key, account_id_value)
    return cache.get_or_set(
        _organization_storage_cache_key(account_id_key, account_id_value),
        lambda: compute_organization_storage_used(account_id_key, account_id_value),
        ttl,
    )


def invalidate_mailbox_storage(mailbox):
    """Drop a mailbox's cached storage usage (e.g. after emptying its trashbin).

    Only the mailbox entry is dropped; the organization total falls back to its
    TTL (keying it precisely would need the backend's org claim).
    """
    cache.delete(_mailbox_storage_cache_key(mailbox.pk))


def invalidate_mailbox_storage_ids(mailbox_ids):
    """``invalidate_mailbox_storage`` for many mailboxes, keyed by id.

    Used by the nightly cutoff sweep, which frees space across many mailboxes
    at once and never loads the Mailbox rows themselves. One ``delete_many``
    rather than a delete per mailbox.
    """
    keys = [_mailbox_storage_cache_key(mailbox_id) for mailbox_id in mailbox_ids]
    if keys:
        cache.delete_many(keys)
