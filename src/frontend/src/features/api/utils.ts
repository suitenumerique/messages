export const errorCauses = async (response: Response, data?: unknown) => {
  const errorsBody = (await response.json()) as Record<
    string,
    string | string[]
  > | null;

  const causes = errorsBody
    ? Object.entries(errorsBody)
        .map(([, value]) => value)
        .flat()
    : undefined;

  return {
    status: response.status,
    cause: causes,
    data,
  };
};

export const isJson = (str: string) => {
  try {
    JSON.parse(str);
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
  } catch (e) {
    return false;
  }
  return true;
};

/**
 * Build the request url from the context url and the base url
 *
 */
export function getRequestUrl(pathname: string, params?: Record<string, string>): string {
  const origin =
    process.env.NEXT_PUBLIC_API_ORIGIN ||
    (typeof window !== "undefined" ? window.location.origin : "");

  const requestUrl = new URL(`${origin}${pathname}`);

  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      requestUrl.searchParams.set(key, value);
    });
  }

  return requestUrl.toString();
};

export const getHeaders = (headers?: HeadersInit): HeadersInit => {
  const csrfToken = getCSRFToken();
  return {
    ...headers,
    "Content-Type": "application/json",
    ...(csrfToken && { "X-CSRFToken": csrfToken }),
  };
};

/**
* Retrieves the CSRF token from the document's cookies.
*
* @returns {string|null} The CSRF token if found in the cookies, or null if not present.
*/
export function getCSRFToken() {
  return document.cookie
    .split(";")
    .filter((cookie) => cookie.trim().startsWith("csrftoken="))
    .map((cookie) => cookie.split("=")[1])
    .pop();
}