import Keycloak from "keycloak-js";
import type { KcContext } from "./KcContext";
import { getBasename, routePaths } from "./router";

export type ActionStatus = "success" | "cancelled" | "error";
export type PendingAction = { action: string; status: ActionStatus };

const AUTH_RESPONSE_PARAMS = [
  "code",
  "state",
  "session_state",
  "iss",
  "error",
  "error_description",
  "error_uri",
];

function readPendingAction(): PendingAction | undefined {
  const url = new URL(location.href);
  const params = url.searchParams;
  const action = params.get("kc_action");
  const status = params.get("kc_action_status");
  const hasAction = params.has("kc_action") || params.has("kc_action_status");
  if (!hasAction) return undefined;

  params.delete("kc_action");
  params.delete("kc_action_status");
  // A response without `state` is not ours: keycloak-js ignores it and it would poison the next redirect_uri.
  if ((params.has("code") || params.has("error")) && !params.has("state")) {
    for (const name of AUTH_RESPONSE_PARAMS) params.delete(name);
  }
  history.replaceState(history.state, "", url.toString());

  if (status === "success" || status === "cancelled" || status === "error") {
    return { action: action ?? "", status };
  }
  return undefined;
}

export const pendingAction: PendingAction | undefined = readPendingAction();

export type Auth = {
  getToken: () => Promise<string>;
  runAction: (action: string) => Promise<void>;
  logout: () => Promise<void>;
};

export async function createAuth(kcContext: KcContext): Promise<Auth> {
  const keycloak = new Keycloak({
    url: kcContext.serverBaseUrl,
    realm: kcContext.realm.name,
    clientId: kcContext.clientId,
  });
  keycloak.onAuthLogout = () => {
    void keycloak.login();
  };
  await keycloak.init({
    onLoad: "login-required",
    pkceMethod: "S256",
    responseMode: "query",
    scope: kcContext.scope,
  });

  return {
    async getToken() {
      try {
        await keycloak.updateToken(5);
      } catch {
        await keycloak.login();
      }
      return keycloak.token ?? "";
    },
    // Every action returns to the account page, where the alert and the credential list live.
    runAction: (action) =>
      keycloak.login({
        action,
        redirectUri: new URL(
          getBasename() + routePaths.account.slice(1),
          location.href,
        ).href,
      }),
    // The account-console client accepts only its own URL after a logout; it then shows the sign-in page.
    logout: () =>
      keycloak.logout({
        redirectUri: new URL(getBasename(), location.href).href,
      }),
  };
}
