import { fetchAPI } from "@/features/api/fetch-api";
import { useConfig } from "@/features/providers/config";
import { handle } from "@/features/utils/errors";
import { useEffect, useMemo, useRef, useState } from "react";

interface PartUpload {
  PartNumber: number;
  ETag: string;
}

interface MultipartInitResponse {
  status: number;
  data: {
    filename: string;
    file_key: string;
    upload_id: string;
  };
  headers: Headers;
}

interface MultipartPartResponse {
  status: number;
  data: {
    url: string;
    part_number: number;
  };
  headers: Headers;
}

interface UploadCompleteResponse {
  status: number;
  data: {
    filename: string;
    url: string;
  };
  headers: Headers;
}

interface DirectUploadResponse {
  status: number;
  data: {
    filename: string;
    file_key: string;
    url: string;
  };
  headers: Headers;
}

interface UploadResource {
  filename: string;
  url: string;
}

const isUserAbort = (error: unknown) =>
  error instanceof Error && error.message === "Aborted";

export enum BucketUploadState {
  IDLE = "idle",
  INITIATING = "initiating",
  IMPORTING = "importing",
  COMPLETING = "completing",
  COMPLETED = "completed",
  ERROR = "error",
}

export type BucketUploadManager = {
  file: File | null;
  /** Server-minted storage key, unique per upload — pass it to POST /imports/.
   *  The onSuccess callback receives a manager whose fileKey is already set (it
   *  can't rely on React state having flushed by the time the upload resolves). */
  fileKey: string | null;
  state: BucketUploadState;
  progress: number;
  upload: (file: File) => void;
  reset: () => void;
  abort: () => void;
}


/**
 * Upload a file part using XHR so we can report on progress through a handler.
 * @param url The presigned URL to PUT the part to.
 * @param chunk The file chunk to upload.
 * @param progressHandler A handler that receives progress updates as a single integer `0 <= x <= 100`.
 * @returns Promise that resolves with the ETag from the response.
 */
const uploadPart = (
  url: string,
  chunk: Blob,
  onInit: (xhr: XMLHttpRequest) => void,
  isAborted: () => boolean,
  progressHandler: (progress: number) => void
): Promise<string> =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);

    // See directUploadFile: distinguish a real user abort from a network
    // failure (status 0 without an abort event).
    xhr.addEventListener("error", () => reject(new Error('Upload failed')));
    xhr.addEventListener("abort", () => reject(new Error('Aborted')));
    onInit(xhr);

    xhr.addEventListener("readystatechange", () => {
      if (xhr.readyState === 4) {
        if (xhr.status === 200) {
          // Get ETag from response header (S3 returns it)
          const etag = xhr.getResponseHeader("ETag");
          if (!etag) {
            reject(new Error("No ETag in response"));
            return;
          }
          return resolve(etag);
        }
        if (xhr.status === 0) {
          // xhr.abort() lands here BEFORE the 'abort' event fires; the flag
          // set by the manager's abort() is the only reliable discriminator.
          if (isAborted()) {
            reject(new Error('Aborted'));
            return;
          }
          reject(new Error('Upload failed: could not reach the storage server.'));
          return;
        }
        reject(new Error(`Failed to upload part. Status: ${xhr.status}`));
      }
    });

    xhr.upload.addEventListener("progress", (progressEvent) => {
      if (progressEvent.lengthComputable) {
        progressHandler(
          Math.floor((progressEvent.loaded / progressEvent.total) * 100)
        );
      }
    });

    xhr.send(chunk);
  });

/**
 * Upload a file using simple PUT (for small files).
 */
const directUploadFile = (
  url: string,
  file: File,
  onInit: (xhr: XMLHttpRequest) => void,
  isAborted: () => boolean,
  progressHandler: (progress: number) => void
) =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");

    // Only a genuine user abort is surfaced as 'Aborted'. A network failure
    // (object storage down, CORS, DNS) fires "error" and/or lands here with
    // status 0 — that is NOT a user cancel, so report it as an upload
    // failure, not "you aborted".
    xhr.addEventListener("error", () => reject(new Error('Upload failed')));
    xhr.addEventListener("abort", () => reject(new Error('Aborted')));
    onInit(xhr);

    xhr.addEventListener("readystatechange", () => {
      if (xhr.readyState === 4) {
        if (xhr.status === 200) {
          return resolve(true);
        }
        if (xhr.status === 0) {
          // xhr.abort() lands here BEFORE the 'abort' event fires; the flag
          // set by the manager's abort() is the only reliable discriminator.
          if (isAborted()) {
            reject(new Error('Aborted'));
            return;
          }
          reject(new Error(`Upload failed: could not reach ${url}.`));
          return;
        }
        reject(new Error(`Failed to perform the upload on ${url}.`));
      }
    });

    xhr.upload.addEventListener("progress", (progressEvent) => {
      if (progressEvent.lengthComputable) {
        progressHandler(
          Math.floor((progressEvent.loaded / progressEvent.total) * 100)
        );
      }
    });

    xhr.send(file);
  });

/**
 * Upload a file using multipart upload (for large files).
 */
const multiPartUploadFile = async (
  mailboxId: string,
  file: File,
  chunkSize: number,
  onUploadCreated: (uploadId: string, fileKey: string) => void,
  onUploadInit: (xhr: XMLHttpRequest) => void,
  onUploadCompleting: () => void,
  isAborted: () => boolean,
  progressHandler: (progress: number) => void
): Promise<UploadResource> => {
  let uploadId: string | null = null;
  let fileKey: string | null = null;
  const filename = file.name;

  try {
    // Step 1: Initiate multipart upload
    const initResponse = await fetchAPI<MultipartInitResponse>(
      `/api/v1.0/mailboxes/${mailboxId}/imports/upload/?multipart`,
      {
        method: "POST",
        body: JSON.stringify({
          filename,
          content_type: file.type || "application/octet-stream",
        }),
      }
    );

    uploadId = initResponse.data.upload_id;
    fileKey = initResponse.data.file_key;

    // Validate before handing the values to React state (same pattern as the
    // direct-upload path).
    if (!uploadId || !fileKey) {
      throw new Error("Failed to initiate multipart upload");
    }
    onUploadCreated(uploadId, fileKey);

    // Step 2: Split file into chunks and upload each part
    const totalChunks = Math.ceil(file.size / chunkSize);

    let uploadedBytes = 0;
    const parts: PartUpload[] = [];
    for (let index = 0; index < totalChunks; index++) {
        // An abort can land BETWEEN chunks (during the presign fetch, or just
        // after a part resolved): without this gate the loop would keep
        // uploading against the aborted server-side upload and surface the
        // resulting NoSuchUpload as a generic error instead of a user cancel.
        if (isAborted()) {
          throw new Error("Aborted");
        }
        try {
          const partNumber = index + 1;
          const start = index * chunkSize;
          const end = Math.min(start + chunkSize, file.size);
          const chunk = file.slice(start, end);

          const partResponse = await fetchAPI<MultipartPartResponse>(
            `/api/v1.0/mailboxes/${mailboxId}/imports/upload/${uploadId}/part/`,
            {
              method: "POST",
              body: JSON.stringify({ file_key: fileKey, part_number: partNumber }),
            }
          );

          const presignedUrl = partResponse?.data.url;

          if (!presignedUrl) {
            throw new Error("Failed to get presigned url.");
          }

          // Upload the part
          const etag = await uploadPart(presignedUrl, chunk, onUploadInit, isAborted, (partProgress) => {
            const partBytes = Math.floor((chunk.size * partProgress) / 100);
            const totalProgress = Math.floor(((uploadedBytes + partBytes) / file.size) * 100);
            progressHandler(totalProgress);
          });

          uploadedBytes += chunk.size;

          parts.push({
            PartNumber: partNumber,
            ETag: etag,
          });
        } catch (error) {
          throw error;
        }
    };

    // Step 3: Complete multipart upload — unless the user aborted while the
    // last part was in flight (completing would resurrect a cancelled upload).
    if (isAborted()) {
      throw new Error("Aborted");
    }
    onUploadCompleting();
    const completeResponse = await fetchAPI<UploadCompleteResponse>(
      `/api/v1.0/mailboxes/${mailboxId}/imports/upload/${uploadId}/`,
      {
        method: "PUT",
        body: JSON.stringify({ file_key: fileKey, parts }),
      }
    );

    return completeResponse.data;
  } catch (error) {
    // A deliberate user abort is neither a Sentry report nor ours to clean
    // up: the manager's abort() already sent the server-side abort, and
    // sending a second one would 404.
    if (!isUserAbort(error)) {
      handle(new Error("Failed to upload file."), { extra: { error } });
      // Something went wrong server-side or on the wire: try to abort the
      // multipart upload so its parts don't linger until the lifecycle rule.
      if (uploadId && fileKey) {
        await abortUpload(mailboxId, uploadId, fileKey);
      }
    }
    throw error;
  }
};

const abortUpload = async (mailboxId: string, uploadId: string, fileKey: string) => {
  try {
    await fetchAPI(`/api/v1.0/mailboxes/${mailboxId}/imports/upload/${uploadId}/`, {
      method: "DELETE",
      body: JSON.stringify({ file_key: fileKey }),
    });
  } catch (error) {
    handle(new Error("Failed to abort multipart upload."), { extra: { error } });
  }
};

export const useBucketUpload = (
  { mailboxId, onSuccess, onError }: { mailboxId: string, onSuccess?: (manager: BucketUploadManager) => void, onError?: (error: string) => void }
): BucketUploadManager => {
  const { MULTIPART_UPLOAD_CHUNK_SIZE_MB } = useConfig();
  // Threshold to use multipart upload (object storage allows chunks of 10MB at least)
  const chunkSize = MULTIPART_UPLOAD_CHUNK_SIZE_MB * 1024 * 1024;
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState<string | null>(null);
  const uploadIdRef = useRef<string | null>(null);
  const fileKeyRef = useRef<string | null>(null);
  // Set by abort() BEFORE xhr.abort(): the status-0 readystatechange fires
  // before the 'abort' event, so this is how the xhr helpers tell a deliberate
  // user cancel from a network failure.
  const userAbortedRef = useRef(false);
  const [state, setState] = useState<BucketUploadState>(BucketUploadState.IDLE);
  const [progress, setProgress] = useState<number>(0);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [xhr, setXhr] = useState<XMLHttpRequest | null>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);

  const reset = () => {
    setFile(null);
    setFileKey(null);
    setState(BucketUploadState.IDLE);
    setProgress(0);
    setUploadId(null);
    setXhr(null);
  }

  const abort = async () => {
    userAbortedRef.current = true;
    if (xhr) xhr.abort();
    // Only a multipart upload has server-side state to abort; a direct PUT
    // (uploadId is never set on that path) just stops with its XHR — the
    // orphaned object is reclaimed by the bucket lifecycle.
    if (uploadId && fileKey) await abortUpload(mailboxId, uploadId, fileKey);
    reset();
  }

  const manager = useMemo(() => ({ file, fileKey, state, progress, upload: setFile, reset, abort }), [file, fileKey, state, progress, uploadId, xhr]);

  const upload = async (file: File) => {
    setState(BucketUploadState.IDLE);
    setProgress(0);
    setUploadId(null);
    setFileKey(null);
    userAbortedRef.current = false;
    const isAborted = () => userAbortedRef.current;

    // Track the resolved key locally: onSuccess fires synchronously at the end
    // of this function, before React has necessarily flushed setFileKey, so we
    // must not read it back off component state.
    let resolvedFileKey: string | null = null;

    try {
      // Use multipart upload for large files
      if (file.size > chunkSize) {
        const handleUploadCreated = (uploadId: string, key: string) => {
          setUploadId(uploadId);
          setFileKey(key);
          resolvedFileKey = key;
          setState(BucketUploadState.IMPORTING);
        };
        const handleUploadCompleting = () => setState(BucketUploadState.COMPLETING);
        setState(BucketUploadState.INITIATING);
        await multiPartUploadFile(
          mailboxId,
          file,
          chunkSize,
          handleUploadCreated,
          setXhr,
          handleUploadCompleting,
          isAborted,
          (progress) => setProgress(progress)
      );
      } else {
        // Use simple upload for small files
        setState(BucketUploadState.INITIATING);
        const response = await fetchAPI<DirectUploadResponse>(
          `/api/v1.0/mailboxes/${mailboxId}/imports/upload/`,
          {
            method: "POST",
            body: JSON.stringify({ filename: file.name, content_type: file.type || "application/octet-stream" }),
          }
        );
        const { url, file_key } = response.data;
        if (!url || !file_key) {
          throw new Error("Failed to generate upload url.");
        }
        setFileKey(file_key);
        resolvedFileKey = file_key;
        setState(BucketUploadState.IMPORTING);
        await directUploadFile(url, file, setXhr, isAborted, setProgress);
      }
    } catch(error) {
      if (isUserAbort(error)) {
        onError?.('Aborted');
        return;
      };
      handle(new Error("Failed to upload file."), { extra: { error } });
      setState(BucketUploadState.ERROR);
      onError?.("An error occurred while uploading the file.");
      setUploadId(null);
      setFile(null);
      setXhr(null);
      return;
    }

    setState(BucketUploadState.COMPLETED);
    setUploadId(null);
    setXhr(null);
    // Hand onSuccess a manager carrying the just-resolved key (the memoized
    // ``manager`` closes over a possibly-stale fileKey from this render).
    onSuccess?.({ ...manager, fileKey: resolvedFileKey });
  };

  useEffect(() => {
    uploadIdRef.current = uploadId;
  }, [uploadId]);

  useEffect(() => {
    fileKeyRef.current = fileKey;
  }, [fileKey]);

  useEffect(() => {
    xhrRef.current = xhr;
  }, [xhr]);

  useEffect(() => {
    if (file) {
      upload(file);

      return () => {
        // Stop the in-flight transfer too (a direct upload has no multipart
        // state to abort server-side, but its XHR would otherwise keep
        // uploading after the modal is gone).
        userAbortedRef.current = true;
        xhrRef.current?.abort();
        if (uploadIdRef.current && fileKeyRef.current) {
          abortUpload(mailboxId, uploadIdRef.current, fileKeyRef.current);
        }
      }
    }
  }, [file])

  return manager;
}
