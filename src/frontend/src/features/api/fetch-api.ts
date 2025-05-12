// Inspired by https://github.com/orval-labs/orval/blob/master/samples/next-app-with-fetch/custom-fetch.ts

import { logout } from "../auth";
import { APIError } from "./api-error";
import { getHeaders, getRequestUrl, isJson } from "./utils";

export interface fetchAPIOptions {
  logoutOn401?: boolean;
}

export const fetchAPI= async <T>(
  pathname: string,
  { params, logoutOn401, ...requestInit }: RequestInit & fetchAPIOptions & { params?: Record<string, string> } = {},
): Promise<T> => {
  const requesUrl = getRequestUrl(pathname, params);

  const response = await fetch(requesUrl, {
    ...requestInit,
    credentials: "include",
    headers: getHeaders(requestInit.headers),
  });

  if ((logoutOn401 ?? true) && response.status === 401) {
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
