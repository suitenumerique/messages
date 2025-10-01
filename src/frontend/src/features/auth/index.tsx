import React, { PropsWithChildren, useEffect } from "react";

import { getRequestUrl } from "@/features/api/utils";
import { useUsersMeRetrieve } from "@/features/api/gen/users/users";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { UserWithAbilities } from "../api/gen/models/user_with_abilities";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { useTranslation } from "react-i18next";
import { SESSION_EXPIRED_KEY } from "../config/constants";

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
  const query = useUsersMeRetrieve({
    query: {
      meta: {
        noGlobalError: true,
      },
    },
    request: { logoutOn401: false },
  });

  useEffect(() => {
    if (query.isError && redirect) {
      login();
    }
  }, [query.isError, redirect]);

  // When the session is expired, display a toast to
  // inform the user that they have been disconnected for that reason
  useEffect(() => {
    if (sessionStorage.getItem(SESSION_EXPIRED_KEY)) {
      sessionStorage.removeItem(SESSION_EXPIRED_KEY);
      addToast(
        <ToasterItem type="info">
          {t('session_expired')}
        </ToasterItem>
      )
    }
  }, []);

  if (!query.isFetched) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
        }}
      >
        <Spinner />
      </div>
    );
  }

  return (
    <AuthContext.Provider
      value={{
        user: query.data?.data ?? null,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
