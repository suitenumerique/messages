import React, { PropsWithChildren, useEffect, useMemo } from "react";

import { getRequestUrl } from "@/features/api/utils";
import { useUsersMeRetrieve } from "@/features/api/gen/users/users";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { UserWithAbilities } from "../api/gen/models/user_with_abilities";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { useTranslation } from "react-i18next";
import { SESSION_EXPIRED_KEY } from "../config/constants";
import { useConfig } from "../providers/config";
import { attemptSilentLogin, canAttemptSilentLogin } from "./silent-login";

export const logout = () => {
  window.location.replace(getRequestUrl("/api/v1.0/logout/"));
};

/**
 * Restricts the post-login redirect to the current site origin to prevent
 * open redirects. Accepts a relative path or absolute URL; returns an
 * absolute URL on the current origin, or undefined if the input is malformed
 * or off-origin.
 */
const sanitizeNextUrl = (raw?: string): string | undefined => {
  if (!raw) return undefined;
  try {
    const absolute = new URL(raw, window.location.origin);
    if (absolute.origin !== window.location.origin) return undefined;
    return absolute.href;
  } catch {
    return undefined;
  }
};

export const login = (nextUrl?: string) => {
    const safeNext = sanitizeNextUrl(nextUrl);
    const FORWARDABLE_OIDC_PARAMS = ['idp_hint', 'login_hint'];
    const searchParams = new URLSearchParams(window.location.search);
    const params = safeNext ? { next: safeNext } : undefined;
    window.location.replace(getRequestUrl("/api/v1.0/authenticate/", {
        ...params,
        ...FORWARDABLE_OIDC_PARAMS.reduce((params, key) => {
            if (searchParams.get(key)) {
                return { ...params, [key]: searchParams.get(key)}
            }
          return params;
        }, {})
  }));
};

interface AuthContextInterface {
  user?: UserWithAbilities | null;
}

export const AuthContext = React.createContext<AuthContextInterface>({});

export const useAuth = () => React.useContext(AuthContext);

export const Auth = ({
  children,
  redirect,
}: PropsWithChildren & { redirect?: boolean }) => {
  const { t } = useTranslation();
  const config = useConfig();
  const query = useUsersMeRetrieve({
    query: {
      meta: {
        noGlobalError: true,
      },
    },
    request: { logoutOn401: false },
  });

  /* User is null if the query is 401 error
   * User is the user object if the query is successful
   * Otherwise, user is undefined
   */
  const user = useMemo(() => {
    if (query.data?.data) return query.data.data;
    if (query.isError && query.error?.code === 401) return null;
    return undefined;
  }, [query.isError, query.error?.code, query.data]);
  const shouldAttemptSilentLogin = useMemo(
    () => config.FRONTEND_SILENT_LOGIN_ENABLED && user === null && canAttemptSilentLogin(),
    [config.FRONTEND_SILENT_LOGIN_ENABLED, user]
 );

  useEffect(() => {
    if (user !== null) return;

    if (shouldAttemptSilentLogin) {
      attemptSilentLogin();
      return;
    }

    if (redirect) {
      login();
    }
  }, [user]);

  // When the session is expired, display a toast to
  // inform the user that they have been disconnected for that reason
  useEffect(() => {
    if (sessionStorage.getItem(SESSION_EXPIRED_KEY)) {
      sessionStorage.removeItem(SESSION_EXPIRED_KEY);
      addToast(
        <ToasterItem type="info">
          {t('Your session has expired. Please log in again.')}
        </ToasterItem>
      )
    }
  }, []);

  if (query.isLoading || shouldAttemptSilentLogin) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh"
        }}
      >
        <Spinner size="xl" />
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ user }}>
      {children}
    </AuthContext.Provider>
  );
};
