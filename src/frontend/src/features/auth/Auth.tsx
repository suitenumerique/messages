import React, { PropsWithChildren, useEffect } from "react";

import { getRequestUrl } from "@/features/api/utils";
import { useUsersMeRetrieve } from "@/features/api/gen/users/users";
import { User } from "@/features/api/gen/models/user";
import { Spinner } from "@gouvfr-lasuite/ui-kit";

export const logout = () => {
  window.location.replace(getRequestUrl("/api/v1.0/logout/"));
};

export const login = () => {
  window.location.replace(getRequestUrl("/api/v1.0/authenticate/"));
};

interface AuthContextInterface {
  user?: User | null;
  init?: () => Promise<User | null>;
}

export const AuthContext = React.createContext<AuthContextInterface>({});

export const useAuth = () => React.useContext(AuthContext);

export const Auth = ({
  children,
  redirect,
}: PropsWithChildren & { redirect?: boolean }) => {
  const query = useUsersMeRetrieve({
    request: { logoutOn401: false },
  });

  useEffect(() => {
    if (query.isError && redirect) {
      login();
    }
  }, [query.isError, redirect]);

  if (query.isFetched === false) {
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
        user: query?.data?.data || null,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
