// Inspired by https://github.com/orval-labs/orval/blob/master/samples/next-app-with-fetch/custom-fetch.ts

import { logout } from "../auth";
import { markSessionExpired } from "../auth/login-state";
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

  const response = await fetch(requestUrl, {
    ...requestInit,
    credentials: "include",
    headers: getHeaders(requestInit.headers, isMultipartFormData),
  });

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
