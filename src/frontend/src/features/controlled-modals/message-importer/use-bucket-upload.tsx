import { fetchAPI } from "@/features/api/fetch-api";
import { useEffect, useMemo, useRef, useState } from "react";


// Threshold to use multipart upload (object storage allows chunks of 10MB at least)
const CHUNK_SIZE_MB = process.env.NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE ? parseInt(process.env.NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE) : 100;
const CHUNK_SIZE = CHUNK_SIZE_MB * 1024 * 1024;
const MULTIPART_THRESHOLD = CHUNK_SIZE;

interface PartUpload {
  PartNumber: number;
  ETag: string;
}

interface MultipartInitResponse {
  status: number;
  data: {
    filename: string;
    upload_id: string;
    key: string;
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
    url: string;
  };
  headers: Headers;
}

interface UploadResource {
  filename: string;
  url: string;
}

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
  progressHandler: (progress: number) => void
): Promise<string> =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);

    xhr.addEventListener("error", reject);
    xhr.addEventListener("abort", reject);
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
          reject(new Error('Aborted'));
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
  progressHandler: (progress: number) => void
) =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");

    xhr.addEventListener("error", reject);
    xhr.addEventListener("abort", () => reject('Aborted'));
    onInit(xhr);

    xhr.addEventListener("readystatechange", () => {
      if (xhr.readyState === 4) {
        if (xhr.status === 200) {
          return resolve(true);
        }
        if (xhr.status === 0) {
          reject(new Error('Aborted'));
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
  file: File,
  onUploadCreated: (args: string) => void,
  onUploadInit: (xhr: XMLHttpRequest) => void,
  onUploadCompleting: () => void,
  progressHandler: (progress: number) => void
): Promise<UploadResource> => {
  let uploadId: string | null = null;
  const filename = file.name;

  try {
    // Step 1: Initiate multipart upload
    const initResponse = await fetchAPI<MultipartInitResponse>(
      "/api/v1.0/import/file/upload/?multipart",
      {
        method: "POST",
        body: JSON.stringify({
          filename,
          content_type: file.type || "application/octet-stream",
        }),
      }
    );

    uploadId = initResponse.data.upload_id;
    onUploadCreated(uploadId);

    if (!uploadId) {
      throw new Error("Failed to initiate multipart upload");
    }

    // Step 2: Split file into chunks and upload each part
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

    let uploadedBytes = 0;
    // const parts: PartUpload[] = await Promise.all<PartUpload>(Array.from({ length: totalChunks }, async (_, index) => {
    const parts: PartUpload[] = [];
    // Array.from({ length: totalChunks }, async (_, index) => {
    for (let index = 0; index < totalChunks; index++) {
        try {
          const partNumber = index + 1;
          const start = index * CHUNK_SIZE;
          const end = Math.min(start + CHUNK_SIZE, file.size);
          const chunk = file.slice(start, end);

          const partResponse = await fetchAPI<MultipartPartResponse>(
            `/api/v1.0/import/file/upload/${uploadId}/part/`,
            {
              method: "POST",
              body: JSON.stringify({ filename, part_number: partNumber }),
            }
          );

          const presignedUrl = partResponse?.data.url;

          if (!presignedUrl) {
            throw new Error("Failed to get presigned url.");
          }

          // Upload the part
          const etag = await uploadPart(presignedUrl, chunk, onUploadInit, (partProgress) => {
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

    // Step 3: Complete multipart upload
    onUploadCompleting();
    const completeResponse = await fetchAPI<UploadCompleteResponse>(
      `/api/v1.0/import/file/upload/${uploadId}/`,
      {
        method: "PUT",
        body: JSON.stringify({ filename, parts }),
      }
    );

    return completeResponse.data;
  } catch (error) {
    console.error("Failed to upload file:", error);
    // If something went wrong, try to abort the multipart upload
    if (uploadId) {
      await abortUpload(uploadId, filename);
    }
    throw error;
  }
};

const abortUpload = async (uploadId: string, filename: string) => {
  try {
    await fetchAPI(`/api/v1.0/import/file/upload/${uploadId}/`, {
      method: "DELETE",
      body: JSON.stringify({ filename }),
    });
  } catch (abortError) {
    console.error("Failed to abort multipart upload:", abortError);
  }
};

export const useBucketUpload = (
  { onSuccess, onError }: { onSuccess?: (manager: BucketUploadManager) => void, onError?: (error: string) => void }
): BucketUploadManager => {
  const [file, setFile] = useState<File | null>(null);
  const uploadIdRef = useRef<string | null>(null);
  const [state, setState] = useState<BucketUploadState>(BucketUploadState.IDLE);
  const [progress, setProgress] = useState<number>(0);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [xhr, setXhr] = useState<XMLHttpRequest | null>(null);

  const reset = () => {
    setFile(null);
    setState(BucketUploadState.IDLE);
    setProgress(0);
    setUploadId(null);
    setXhr(null);
  }

  const abort = async () => {
    if (!uploadId || !file) return;
    if (xhr) xhr.abort();
    await abortUpload(uploadId, file.name);
    reset();
  }

  const manager = useMemo(() => ({ file, state, progress, upload: setFile, reset, abort }), [file, state, progress, uploadId, xhr]);

  const upload = async (file: File) => {
    setState(BucketUploadState.IDLE);
    setProgress(0);
    setUploadId(null);

    try {
      // Use multipart upload for large files
      if (file.size > MULTIPART_THRESHOLD) {
        const handleUploadCreated = (uploadId: string) => {
          setUploadId(uploadId);
          setState(BucketUploadState.IMPORTING);
        };
        const handleUploadCompleting = () => setState(BucketUploadState.COMPLETING);
        setState(BucketUploadState.INITIATING);
        await multiPartUploadFile(
          file,
          handleUploadCreated,
          setXhr,
          handleUploadCompleting,
          (progress) => setProgress(progress)
      );
      } else {
        // Use simple upload for small files
        setState(BucketUploadState.INITIATING);
        const response = await fetchAPI<DirectUploadResponse>(
          "/api/v1.0/import/file/upload/",
          {
            method: "POST",
            body: JSON.stringify({ filename: file.name, content_type: file.type || "application/octet-stream" }),
          }
        );
        const { url } = response.data;
        if (!url) {
          throw new Error("Failed to generate upload url.");
        }
        setState(BucketUploadState.IMPORTING);
        await directUploadFile(url, file, setXhr, setProgress);
      }
    } catch(error) {
      if (error instanceof Error && error.message === 'Aborted') {
        onError?.('Aborted');
        return;
      };
      console.error("Failed to upload file:", error);
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
    onSuccess?.(manager);
  };

  useEffect(() => {
    uploadIdRef.current = uploadId;
  }, [uploadId]);

  useEffect(() => {
    if (file) {
      upload(file);

      return () => {
        if (uploadIdRef.current) {
          abortUpload(uploadIdRef.current, file.name);
        }
      }
    }
  }, [file])

  return manager;
}
