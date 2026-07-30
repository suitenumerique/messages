// Inspired by https://github.com/orval-labs/orval/blob/master/samples/next-app-with-fetch/custom-fetch.ts

import { logout } from "../auth";
import { markSessionExpired } from "../auth/login-state";
import { nativeFetch } from "../native/fetch";
import { isNativePlatform } from "../native/platform";
import { APIError } from "./api-error";
import { getHeaders, getRequestUrl, isJson } from "./utils";

// https://github.com/orval-labs/orval/issues/258
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export type ErrorType<_E> = APIError;

export interface fetchAPIOptions {
  logoutOn401?: boolean;
}

export const fetchAPI= async <T>(
  pathname: string,
  { params, logoutOn401 = true, ...requestInit }: RequestInit & fetchAPIOptions & { params?: Record<string, string> } = {},
): Promise<T> => {
  const requestUrl = getRequestUrl(pathname, params);
  const isMultipartFormData = requestInit.body instanceof FormData;
  const options: RequestInit = {
    ...requestInit,
    credentials: "include",
    headers: getHeaders(requestInit.headers, isMultipartFormData),
  };

  // In the Capacitor shell, mutations go through the CapacitorHttp plugin
  // directly: the bridge's patched fetch strips the Origin header that
  // Django's CSRF check requires over HTTPS (see nativeFetch). Reads stay on
  // the patched fetch — no CSRF there, and they keep React Query cancellation.
  const method = (requestInit.method ?? "GET").toUpperCase();
  const useNativeFetch = isNativePlatform() && !["GET", "HEAD"].includes(method);
  const response = useNativeFetch
    ? await nativeFetch(requestUrl, options)
    : await fetch(requestUrl, options);

  if (response.status === 401 && logoutOn401) {
    markSessionExpired();
    logout();
  }

  if (response.ok) {
    const data = response.status === 204 ? null : await response.json();
    return { status: response.status, data, headers: response.headers } as T;
  }

  const data = await response.text();
  if (isJson(data)) {
    throw new APIError(response.status, JSON.parse(data));
  }
  throw new APIError(response.status);
};
