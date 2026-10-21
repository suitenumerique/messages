import type { KcContext } from "./KcContext";
import type { Auth } from "./auth";

export type UserProfileAttribute = {
  name: string;
  displayName?: string;
  required?: boolean;
  readOnly?: boolean;
};

export type UserRepresentation = {
  id?: string;
  username?: string;
  email: string;
  firstName: string;
  lastName: string;
  attributes?: Record<string, string[]>;
  userProfileMetadata?: { attributes?: UserProfileAttribute[] };
};

export type CredentialMetadata = {
  credential: {
    id: string;
    type: string;
    userLabel?: string;
    createdDate?: number;
  };
};

export type CredentialContainer = {
  type: string;
  category: string;
  displayName: string;
  helptext: string;
  createAction?: string;
  updateAction?: string;
  removeable: boolean;
  userCredentialMetadatas: CredentialMetadata[];
};

export type FieldError = {
  field: string;
  errorMessage: string;
  params?: string[];
};

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super(`Request failed with status ${status}`);
    this.status = status;
    this.body = body;
  }

  get fieldErrors(): FieldError[] {
    const body = this.body;
    if (typeof body === "object" && body !== null && "errors" in body) {
      const errors = (body as { errors: unknown }).errors;
      if (Array.isArray(errors)) return errors as FieldError[];
    }
    return [];
  }
}

export type Api = ReturnType<typeof createApi>;

export function createApi(kcContext: KcContext, auth: Auth) {
  const base = `${kcContext.serverBaseUrl}/realms/${kcContext.realm.name}/account`;

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = await auth.getToken();
    const response = await fetch(base + path, {
      ...init,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...init.headers,
        Authorization: `Bearer ${token}`,
      },
    });
    const text = await response.text();
    let body: unknown = undefined;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    if (!response.ok) throw new ApiError(response.status, body);
    return body as T;
  }

  return {
    getProfile: () => request<UserRepresentation>("/?userProfileMetadata=true"),
    saveProfile: (user: UserRepresentation) =>
      request<void>("/", { method: "POST", body: JSON.stringify(user) }),
    getCredentials: () => request<CredentialContainer[]>("/credentials"),
    getSupportedLocales: () => request<string[]>("/supportedLocales"),
  };
}
