import React, { PropsWithChildren, useEffect, useMemo } from "react";

import { getRequestUrl } from "@/features/api/utils";
import { setWebCsrfToken } from "@/features/api/csrf";
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

export const login = () => {
  window.location.replace(getRequestUrl("/api/v1.0/authenticate/"));
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

  // Cache the session-bound CSRF token delivered with /users/me/ so mutations
  // can echo it in the X-CSRFToken header (no `csrftoken` cookie any more under
  // CSRF_USE_SESSIONS).
  useEffect(() => {
    if (user) setWebCsrfToken(user.csrf_token);
  }, [user]);

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
