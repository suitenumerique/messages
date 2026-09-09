import { CapacitorHttp } from "@capacitor/core";

/**
 * Drop-in `fetch` replacement for mutating API calls inside the Capacitor
 * shell, calling the `CapacitorHttp` plugin directly instead of the patched
 * `window.fetch`.
 *
 * Why bypass the patch: it normalizes options through `new Request()`, whose
 * `Headers` carry the browser "request" guard — forbidden names, `Origin`
 * included, are silently dropped before they reach the native layer. Django's
 * CSRF protection rejects HTTPS mutations carrying neither `Origin` nor
 * `Referer` ("Referer checking failed"), and the native HTTP client sends
 * none by itself, so the `Origin` injected by `getHeaders` must survive the
 * bridge. Calling the plugin directly passes headers verbatim and ends in the
 * same native code path as the patch: cookies (session jar) and TLS behave
 * identically.
 *
 * Body conversion (string kept as-is, `FormData` flattened to the plugin's
 * entry list) and `Response` reconstruction mirror the bridge's own fetch
 * patch, so responses are indistinguishable for callers.
 */

type NativeFormDataEntry =
  | { key: string; value: string; type: "string" }
  | {
      key: string;
      value: string;
      type: "base64File";
      contentType: string;
      fileName: string;
    };

const readFileAsBase64 = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result as string;
      // readAsDataURL yields `data:<mime>;base64,<data>` — the native layer
      // wants the bare base64 payload.
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });

const convertFormData = async (
  formData: FormData,
): Promise<NativeFormDataEntry[]> => {
  const entries: NativeFormDataEntry[] = [];
  for (const [key, value] of formData.entries()) {
    if (value instanceof File) {
      entries.push({
        key,
        value: await readFileAsBase64(value),
        type: "base64File",
        contentType: value.type,
        fileName: value.name,
      });
    } else {
      entries.push({ key, value, type: "string" });
    }
  }
  return entries;
};

/**
 * Perform an HTTP request through the CapacitorHttp plugin with `fetch`
 * semantics: same signature, resolves to a standard `Response` (the plugin
 * resolves on HTTP error statuses, so like `fetch` this only rejects on
 * transport failures).
 *
 * Supports the two body shapes the API client produces — JSON strings and
 * `FormData` — and rejects anything else loudly rather than corrupting the
 * request. `signal` is ignored: the plugin has no abort support, which is why
 * only mutations (never React Query reads) are routed here.
 */
export const nativeFetch = async (
  url: string,
  init: RequestInit = {},
): Promise<Response> => {
  // A standalone `Headers` has no guard: unlike the bridge's `new Request()`,
  // it keeps forbidden names such as `Origin` (iteration lowercases names,
  // which the native layer and Django both accept).
  const headers = Object.fromEntries(new Headers(init.headers).entries());

  const { body } = init;
  let data: string | NativeFormDataEntry[] | undefined;
  let dataType: "formData" | undefined;
  if (body instanceof FormData) {
    data = await convertFormData(body);
    dataType = "formData";
    // The native layer builds the multipart body and its boundary itself; a
    // leftover Content-Type would desynchronize the two.
    delete headers["content-type"];
  } else if (typeof body === "string") {
    data = body;
  } else if (body !== undefined && body !== null) {
    throw new Error("nativeFetch only supports string and FormData bodies.");
  }

  const response = await CapacitorHttp.request({
    url,
    method: init.method ?? "GET",
    headers,
    ...(data !== undefined && { data }),
    ...(dataType && { dataType }),
  });

  // The plugin parses JSON bodies into objects; `Response` wants the raw text
  // back. 204 must map to a null body — `Response` throws on any body there.
  let responseBody: string | null =
    typeof response.data === "string"
      ? response.data
      : JSON.stringify(response.data);
  if (response.status === 204 || response.data === undefined) {
    responseBody = null;
  }
  return new Response(responseBody, {
    status: response.status,
    headers: response.headers,
  });
};
