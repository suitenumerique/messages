import React, { PropsWithChildren, useEffect, useMemo } from "react";

import { getRequestUrl } from "@/features/api/utils";
import { setWebCsrfToken } from "@/features/api/csrf";
import { useUsersMeRetrieve } from "@/features/api/gen/users/users";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { UserWithAbilities } from "../api/gen/models/user_with_abilities";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { useTranslation } from "react-i18next";
import { nativeLogin, nativeLogout } from "../native/auth";
import { isNativePlatform } from "../native/platform";
import {
  clearDeliveredNativeNotifications,
  refreshNativePushRegistration,
} from "../native/push";
import {
  clearLoginFailed,
  consumeLoginAttempt,
  consumeSessionExpired,
  hasLoginFailed,
  hasPendingLoginAttempt,
  isSessionExpired,
  markLoginAttempt,
  markLoginFailed,
} from "./login-state";
import { useConfig } from "../providers/config";
import {
  listenForPushSubscriptionChange,
  refreshWebPushSubscription,
} from "../layouts/components/mailbox-settings/devices-view/web-push";
import { attemptSilentLogin, canAttemptSilentLogin } from "./silent-login";

/**
 * Log the user out.
 *
 * Web push is deliberately NOT torn down here. A voluntary logout is handled
 * *server-side*: the ``user_logged_out`` receiver deletes the push channel
 * stamped with this session, so the device stops receiving the moment the
 * logout view runs — browser subscription and per-user opt-in marker survive,
 * which is what lets the same user's notifications resume transparently on
 * their next login (`refreshWebPushSubscription`). A session that merely
 * expires (401 funnel) reaches the logout view anonymous, so nothing is
 * unregistered and notifications keep flowing — by design.
 */
export const logout = () => {
  if (isNativePlatform()) {
    void nativeLogout();
    return;
  }
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
  if (isNativePlatform()) {
    void nativeLogin();
    return;
  }
  const safeNext = sanitizeNextUrl(nextUrl);
  const params: Record<string, string> = {};
  if (safeNext) params.next = safeNext;
  // Marker read back after the OIDC callback to detect a failed sign-in
  // (e.g. no Messages user exists for the authenticated identity).
  markLoginAttempt();
  window.location.replace(getRequestUrl("/api/v1.0/authenticate/", params));
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

  const shouldAttemptSilentLogin = useMemo(() => {
    if (isNativePlatform()) return false;
    if (!config.FRONTEND_SILENT_LOGIN_ENABLED) return false;
    if (user !== null) return false;
    if (!canAttemptSilentLogin()) return false;
    // Skip silent login while a one-shot toast still needs to be shown,
    // otherwise the redirect unmounts the page before the Toaster renders
    // (e.g. failed explicit sign-in, or session expired notification).
    if (hasPendingLoginAttempt()) return false;
    if (isSessionExpired()) return false;
    if (hasLoginFailed()) return false;
    return true;
  }, [config.FRONTEND_SILENT_LOGIN_ENABLED, user]);
  const isAuthenticated = !!user;
  const userId = user?.id;

  // Cache the session-bound CSRF token delivered with /users/me/ so mutations
  // can echo it in the X-CSRFToken header (no `csrftoken` cookie any more under
  // CSRF_USE_SESSIONS). The native shell uses its own token from the session
  // exchange, so it is skipped here.
  useEffect(() => {
    if (isNativePlatform()) return;
    if (user) setWebCsrfToken(user.csrf_token);
  }, [user]);

  // Self-heal push once authenticated: if the user previously enabled it on
  // this device, re-register the current subscription/token so a rotated one
  // doesn't silently stop delivering. Passive — no-ops unless the user opted
  // in here. On the web the listener additionally catches a rotation that
  // happens while the app stays open: the worker can't sign the registration
  // POST, so it hands the new subscription to us. The native shells have no
  // equivalent — the on-launch refresh is their rotation catch-up.
  //
  // This runs here, gated on an authenticated `user`, rather than in
  // ConfigProvider: the registration POST needs the in-memory CSRF token, which
  // is only set once `/users/me/` resolves (same `user` that drives
  // `setWebCsrfToken` above). Firing it from the config layer raced ahead of the
  // token and the POST 403'd (no more `csrftoken` cookie under CSRF_USE_SESSIONS).
  useEffect(() => {
    if (!isAuthenticated || !config.PUSH_ENABLED) return;
    if (isNativePlatform()) {
      refreshNativePushRegistration(userId);
      return;
    }
    if (!config.PUSH_VAPID_PUBLIC_KEY) return;
    refreshWebPushSubscription(config.PUSH_VAPID_PUBLIC_KEY, userId);
    return listenForPushSubscriptionChange();
  }, [isAuthenticated, userId, config.PUSH_ENABLED, config.PUSH_VAPID_PUBLIC_KEY]);

  // Clear the installed-PWA badge whenever the app is in the foreground. The
  // service worker's push handler is the only thing that raises it (and clears
  // it on notification tap); this covers the icon-launch / tab-refocus paths
  // where the user reaches the app without going through a notification, so a
  // badge never lingers while they are actually looking at their mail. Runs
  // once on mount (visible load) and on every hidden→visible transition.
  // Best-effort no-op where the Badging API is unavailable (Firefox/Safari).
  // The native shells dismiss their delivered OS notifications on the same
  // signal (the iOS badge itself is reset in the AppDelegate).
  useEffect(() => {
    if (!isAuthenticated) return;
    const clearBadge = () => {
      if (document.visibilityState !== "visible") return;
      clearDeliveredNativeNotifications();
      if ("clearAppBadge" in navigator) {
        navigator.clearAppBadge().catch(() => {});
      }
    };
    clearBadge();
    document.addEventListener("visibilitychange", clearBadge);
    return () => document.removeEventListener("visibilitychange", clearBadge);
  }, [isAuthenticated]);

  // Cache the session-bound CSRF token delivered with /users/me/ so mutations
  // can echo it in the X-CSRFToken header (no `csrftoken` cookie any more under
  // CSRF_USE_SESSIONS). The native shell uses its own token from the session
  // exchange, so it is skipped here.
  useEffect(() => {
    if (isNativePlatform()) return;
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

  // When the session is expired, display a toast to inform the user that
  // they have been disconnected for that reason. Deferred until `user` is
  // resolved so the Toaster has been mounted by the rendered children.
  useEffect(() => {
    if (user === undefined) return;
    if (!consumeSessionExpired()) return;
    addToast(
      <ToasterItem type="info">
        {t('Your session has expired. Please log in again.')}
      </ToasterItem>
    );
  }, [user, t]);

  // After an explicit OIDC sign-in attempt, warn the user when no Messages
  // account is associated with the authenticated identity (the backend
  // redirects to the homepage unauthenticated in that case).
  useEffect(() => {
    if (user === undefined) return;
    if (user) {
      clearLoginFailed();
    }
    if (!consumeLoginAttempt()) return;
    if (user === null) {
      markLoginFailed();
      // Signing out terminates the IdP session (ProConnect ignores
      // prompt=login, so this is the only way to let the user restart an
      // authentication cycle with another identity from the homepage).
      // The toast must stay until the user acts on it: it is the only
      // affordance to break out of the SSO loop.
      addToast(
        <ToasterItem type="warning" actions={[
          {
            label: t('Sign out'),
            showLabel: true,
            onClick: () => logout(),
          },
        ]}>
          {t('No Messages account is associated with this identity. Please contact your administrator.')}
        </ToasterItem>,
        { autoClose: false },
      );
    }
  }, [user, t]);

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
