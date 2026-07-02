import { CapacitorHttp } from "@capacitor/core";
import { Directory, Filesystem } from "@capacitor/filesystem";
import { Share } from "@capacitor/share";

/**
 * Reduce an attachment name to a safe flat filename.
 *
 * Attachment names come from MIME headers of received emails, so they are
 * sender-controlled: `Filesystem.writeFile` treats `path` as relative to the
 * target directory without any documented traversal protection, meaning a name
 * containing `../` or separators could escape the cache root.
 */
const sanitizeFilename = (filename: string): string => {
  const basename = filename.split(/[/\\]/).pop() ?? "";
  // eslint-disable-next-line no-control-regex
  const safe = basename.replace(/[\x00-\x1f]/g, "").replace(/^\.+/, "");
  return safe.trim() || "download";
};

/**
 * Download a file inside the Capacitor shell and hand it to the OS share sheet.
 *
 * On the web an `<a download>` reaches the API with the browser session. In the
 * native shell that anchor escapes the WebView and opens the system browser,
 * which has no access to the session living in the native HTTP jar — the
 * backend then answers 401. Here the bytes are fetched through `CapacitorHttp`
 * (carrying the native session), written to the cache directory and shared, so
 * the user can save or open them with any installed app.
 *
 * The body crosses the bridge as base64, which is fine because attachments are
 * capped server-side by `MAX_OUTGOING_ATTACHMENT_SIZE` (20 MiB); a streaming
 * native download would not carry the session cookie.
 *
 * @param url Absolute URL of the file to download (built via `getRequestUrl`).
 * @param filename Name used for the cached file and the share sheet title;
 *   reduced to a safe basename before touching the filesystem.
 */
export const nativeDownloadFile = async (
  url: string,
  filename: string,
): Promise<void> => {
  // `responseType: "blob"` makes the native layer return the body as a base64
  // string in `data`, which `Filesystem.writeFile` accepts as-is.
  const response = await CapacitorHttp.get({ url, responseType: "blob" });
  // CapacitorHttp resolves on HTTP error statuses instead of rejecting: fail
  // here rather than write (and share) the error body as if it were the file.
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`Download of ${filename} failed (${response.status}).`);
  }

  const safeFilename = sanitizeFilename(filename);
  const { uri } = await Filesystem.writeFile({
    path: safeFilename,
    data: response.data as string,
    directory: Directory.Cache,
  });

  try {
    await Share.share({ url: uri, title: safeFilename });
  } catch (error) {
    // Dismissing the share sheet rejects too: the file was downloaded, the
    // user just changed their mind — not a failure to report.
    if (error instanceof Error && /cancel/i.test(error.message)) {
      return;
    }
    throw error;
  }
};
