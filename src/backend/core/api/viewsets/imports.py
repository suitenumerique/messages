"""The imports API: one mailbox-nested viewset for the import-run resource.

``POST /mailboxes/{id}/imports/`` starts an import (file or IMAP);
``GET .../imports/`` + ``GET .../imports/{id}/`` read run state
(status/progress/counts hydrated from Redis + the channel);
``PATCH .../imports/{id}/`` arms/pauses a continuous poller (``mode`` +
``is_active``); ``POST .../imports/{id}/cancel/`` stops a run and deletes its
messages; ``DELETE .../imports/{id}/`` forgets a finished run while *keeping*
its messages. An import run is a ``Channel`` with ``type=import`` grouping
every message the run created.
``MessagesArchiveUploadViewSet`` is the S3 upload helper a file import uses
before ``POST .../imports/``.
"""

from django.core.files.storage import storages
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property

from botocore.exceptions import ClientError
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core import enums, models
from core.api.utils import generate_file_key, generate_presigned_url, validate_file_key
from core.models import Mailbox
from core.services.importer.channel import (
    TERMINAL_STATUSES,
    clear_state,
    disable_continuous,
    enable_continuous,
    get_import_channel,
    mark_cancelled,
    merged_state,
    pause_import,
)
from core.services.importer.service import start_file_import, start_imap_import
from core.services.importer.tasks import cancel_import_task, run_import_task

from .. import permissions
from ..serializers import (
    ImportCreateSerializer,
    ImportFileUploadAbortSerializer,
    ImportFileUploadCompleteSerializer,
    ImportFileUploadPartSerializer,
    ImportFileUploadSerializer,
    ImportRunSerializer,
    ImportUpdateSerializer,
)


def _require_owned_file_key(user_id, file_key: str) -> str:
    """Return ``file_key`` iff it was minted for this user; else raise 400.

    The single ownership/format gate for every endpoint that takes a key from
    the client: refuses a foreign or hand-crafted key (traversal, arbitrary
    bucket path) before it can reach S3.
    """
    if not validate_file_key(user_id, file_key):
        raise ValidationError({"file_key": "Unknown upload."})
    return file_key


@extend_schema(tags=["import"])
class ImportViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Start, list, retrieve and manage import runs for one mailbox.

    Mailbox-nested and gated by ``IsMailboxAdmin`` on the URL mailbox — imports
    bulk-load mail into a mailbox, so they are an admin operation (matching the
    ``import_messages`` ability that gates the UI).
    """

    permission_classes = [permissions.IsMailboxAdmin]
    serializer_class = ImportRunSerializer
    pagination_class = None

    @cached_property
    def mailbox(self):
        """The Mailbox from the URL (authorization target)."""
        return get_object_or_404(Mailbox, id=self.kwargs["mailbox_id"])

    def get_serializer_class(self):
        if self.action == "create":
            return ImportCreateSerializer
        if self.action == "partial_update":
            return ImportUpdateSerializer
        return ImportRunSerializer

    def get_queryset(self):
        """Import runs for this mailbox, most recently active first.

        ``last_used_at`` is the run's heartbeat (advanced while it works and on
        every continuous poll); a never-dispatched run falls back to its
        creation time, so a just-created import still sorts on top.
        """
        return (
            models.Channel.objects.filter(
                type=enums.ChannelTypes.IMPORT.value,
                mailbox=self.mailbox,
            )
            .select_related("mailbox")
            .annotate(last_activity=Coalesce("last_used_at", "created_at"))
            .order_by("-last_activity")
        )

    @extend_schema(
        request=ImportCreateSerializer,
        responses={
            202: OpenApiResponse(
                response=ImportRunSerializer,
                description="Import started; returns the import run to poll.",
            ),
            400: OpenApiResponse(description="Invalid input data or file format"),
            403: OpenApiResponse(description="No access to the mailbox"),
            404: OpenApiResponse(description="Mailbox not found"),
        },
    )
    def create(self, request, *args, **kwargs):
        """Start an import into the URL mailbox (``source=file`` from an
        uploaded archive, or ``source=imap`` from a live server)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data["source"] == "file":
            _require_owned_file_key(request.user.id, data["file_key"])
            success, resp = start_file_import(
                file_key=data["file_key"],
                recipient=self.mailbox,
                user=request.user,
                filename=data["filename"],
            )
        else:
            success, resp = start_imap_import(
                imap_server=data["imap_server"],
                imap_port=data["imap_port"],
                username=data["username"],
                password=data["password"],
                recipient=self.mailbox,
                user=request.user,
                use_ssl=data.get("use_ssl", True),
                mode=data.get("mode", enums.ImportMode.ONESHOT.value),
            )

        if not success:
            # The service tags each failure with the right HTTP status (bad
            # file → 400, missing → 404, server error → 500) so the client can
            # tell "your upload is wrong" from "something else broke".
            return Response(
                {"detail": resp.get("detail")},
                status=resp.get("status", status.HTTP_400_BAD_REQUEST),
            )

        channel = get_import_channel(resp["import_id"])
        return Response(
            ImportRunSerializer(channel).data, status=status.HTTP_202_ACCEPTED
        )

    @extend_schema(
        request=None,
        responses={
            202: OpenApiResponse(
                response=ImportRunSerializer,
                description=(
                    "Import cancelled. The run is marked cancelled immediately; "
                    "its imported messages are deleted (and empty threads "
                    "cleaned) in the background. Imported messages in threads "
                    "with non-import activity (e.g. a reply) are kept."
                ),
            ),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Import not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, *args, **kwargs):
        """Cancel an import.

        Flips it to ``cancelled`` synchronously (so the run stops and the
        scheduler won't resume it) and offloads the potentially-large message
        deletion + orphan-thread cleanup to an idempotent background task —
        which also removes the run from ``/imports/`` once it has settled.
        """
        channel = self.get_object()
        mark_cancelled(channel)
        cancel_import_task.delay(str(channel.id))
        return Response(
            ImportRunSerializer(channel).data, status=status.HTTP_202_ACCEPTED
        )

    @extend_schema(
        request=ImportUpdateSerializer,
        responses={200: ImportRunSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        """Arm or pause a continuous import via ``mode`` / ``is_active``.

        * ``mode=continuous`` (IMAP only) (re-)arms the import as a poller —
          flips ``is_active=True`` and dispatches a run now. Also the "re-enable
          a finished oneshot as continuous" path: stored credentials are reused,
          no re-auth.
        * ``mode=oneshot`` demotes a continuous poller back to a one-shot —
          polling stops (``is_active=False``); credentials and watermark are
          kept so it can be re-armed later.
        * ``is_active=false`` pauses a continuous poller (credentials are kept).

        The poll cadence is the global ``MESSAGES_IMPORT_IMAP_POLL_INTERVAL``
        setting, not settable here.
        """
        channel = self.get_object()
        serializer = self.get_serializer(channel, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        current_mode = (channel.settings or {}).get("import", {}).get("mode")
        # Pausing is mode-agnostic and takes precedence over any re-arm. The
        # serializer has already rejected is_active=true on a one-shot and the
        # contradictory mode=oneshot + is_active=true combination, so each
        # branch below is unambiguous.
        if data.get("is_active") is False:
            if data.get("mode") == enums.ImportMode.CONTINUOUS.value:
                # "Arm as a poller but start it paused": persist the mode
                # (enable_continuous also clears a stale cancelled snapshot)
                # before pausing, without dispatching a run. Silently dropping
                # the mode here would strand the import as a oneshot that a
                # later is_active=true PATCH refuses to re-activate.
                enable_continuous(channel)
            pause_import(channel)
            if data.get("mode") == enums.ImportMode.ONESHOT.value:
                disable_continuous(channel)
        elif data.get("mode") == enums.ImportMode.ONESHOT.value:
            # Demote a continuous poller; on an already-oneshot run this is an
            # idempotent no-op.
            if current_mode == enums.ImportMode.CONTINUOUS.value:
                disable_continuous(channel)
        elif data.get("mode") == enums.ImportMode.CONTINUOUS.value or (
            data.get("is_active") is True
            and current_mode == enums.ImportMode.CONTINUOUS.value
        ):
            enable_continuous(channel)
            run_import_task.delay(str(channel.id))

        channel.refresh_from_db()
        return Response(ImportRunSerializer(channel).data, status=status.HTTP_200_OK)

    @extend_schema(
        responses={
            204: OpenApiResponse(
                description="Import forgotten. Its imported messages are kept."
            ),
            400: OpenApiResponse(
                description="The import is still running or still polling."
            ),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Import not found"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Forget an import run, keeping the mail it imported.

        Deletes the Channel row (``Message.channel`` is SET_NULL, so the
        messages survive) — the opposite of ``cancel``, which deletes the
        messages (and then the row too). Only a settled run can be forgotten:
        cancel a running import first, and pause (or demote) a continuous
        poller so a live worker never loses its channel row mid-run.
        """
        channel = self.get_object()
        run = merged_state(channel)
        if run.get("status") not in TERMINAL_STATUSES:
            return Response(
                {"detail": "The import is still running; cancel it first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if channel.is_active:
            return Response(
                {"detail": "The import is still polling; pause it first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        clear_state(channel.id)
        channel.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessagesArchiveUploadViewSet(viewsets.ViewSet):
    """Upload a message archive into the imports bucket (direct or multipart),
    used before ``POST .../imports/`` with ``source=file``.

    Nested under the same mailbox as the imports it feeds and gated by the same
    ``IsMailboxAdmin`` — so only someone who could start the import can mint
    presigned writes into the bucket. The minted keys stay *user*-scoped (an
    upload can feed an import into any mailbox the user administers); the URL
    mailbox is the authorization target. Abandoned uploads (completed or
    dangling multipart) are reclaimed by the bucket lifecycle rule set by
    ``create_bucket`` (see docs/imports.md).
    """

    permission_classes = [permissions.IsMailboxAdmin]
    storage = storages["message-imports"]
    lookup_url_kwarg = "upload_id"
    lookup_field = "upload_id"

    def create(self, request, **kwargs):
        """Create a multipart upload (returns ``upload_id``) or a direct
        presigned PUT url for a file in the imports bucket."""
        serializer = ImportFileUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        filename = serializer.validated_data["filename"]
        # Minted fresh for every upload: keys are never reused, so no upload
        # can overwrite another (a resumable import must never see its
        # underlying archive replaced mid-run). The client passes the returned
        # ``file_key`` to the follow-up calls and to ``POST /imports/``.
        file_key = generate_file_key(request.user.id)
        is_multipart = "multipart" in request.query_params
        content_type = serializer.validated_data["content_type"]

        if is_multipart:
            s3_client = self.storage.connection.meta.client
            metadata = s3_client.create_multipart_upload(
                Bucket=self.storage.bucket_name, Key=file_key, ContentType=content_type
            )
            return Response(
                {
                    "filename": filename,
                    "file_key": file_key,
                    "upload_id": metadata["UploadId"],
                },
                status=status.HTTP_201_CREATED,
            )

        url = generate_presigned_url(
            self.storage,
            ClientMethod="put_object",
            Params={
                "Bucket": self.storage.bucket_name,
                "Key": file_key,
                "ContentType": content_type,
            },
        )
        return Response(
            {"filename": filename, "file_key": file_key, "url": url},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="part")
    def create_part_upload(self, request, upload_id=None, **kwargs):
        """Create a presigned url to upload one part of a multipart upload."""
        data = request.data.copy()
        data.update({"upload_id": upload_id})
        serializer = ImportFileUploadPartSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        file_key = _require_owned_file_key(
            request.user.id, serializer.validated_data["file_key"]
        )
        upload_id = serializer.validated_data["upload_id"]
        part_number = serializer.validated_data["part_number"]

        url = generate_presigned_url(
            self.storage,
            ClientMethod="upload_part",
            Params={
                "Bucket": self.storage.bucket_name,
                "Key": file_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
        )
        return Response(
            {
                "file_key": file_key,
                "part_number": part_number,
                "upload_id": upload_id,
                "url": url,
            },
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, upload_id=None, **kwargs):
        """Complete a multipart upload by providing all part ETags."""
        data = request.data.copy()
        data.update({"upload_id": upload_id})
        serializer = ImportFileUploadCompleteSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        file_key = _require_owned_file_key(
            request.user.id, serializer.validated_data["file_key"]
        )
        upload_id = serializer.validated_data["upload_id"]
        parts = serializer.validated_data["parts"]

        ordered_parts = sorted(parts, key=lambda x: x["PartNumber"])
        s3_client = self.storage.connection.meta.client
        s3_client.complete_multipart_upload(
            Bucket=self.storage.bucket_name,
            Key=file_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": ordered_parts},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, upload_id=None, **kwargs):
        """Abort a multipart upload."""
        data = request.data.copy()
        data.update({"upload_id": upload_id})
        serializer = ImportFileUploadAbortSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        file_key = _require_owned_file_key(
            request.user.id, serializer.validated_data["file_key"]
        )
        upload_id = serializer.validated_data["upload_id"]

        s3_client = self.storage.connection.meta.client
        try:
            s3_client.abort_multipart_upload(
                Bucket=self.storage.bucket_name, Key=file_key, UploadId=upload_id
            )
        except ClientError as exc:
            # Idempotent: the upload may already be gone (aborted by the
            # client's unmount cleanup racing its explicit abort, or already
            # completed). A duplicate abort is a no-op, not a 500.
            code = (exc.response or {}).get("Error", {}).get("Code")
            if code not in ("NoSuchUpload", "NotFound", "404"):
                raise
        return Response(status=status.HTTP_204_NO_CONTENT)
