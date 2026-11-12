import { useSyncExternalStore } from "react";
import type { KcContext } from "./KcContext";

export type Route = "account" | "personal-info" | "choice";

export const routePaths: Record<Route, string> = {
  "personal-info": "/",
  account: "/connection-and-account",
  choice: "/double-authentication-choice",
};

let basename = "/";

export function initRouter(kcContext: KcContext) {
  const { baseUrl } = kcContext;
  const path =
    typeof baseUrl === "string"
      ? new URL(baseUrl, location.href).pathname
      : baseUrl.path;
  basename = path.endsWith("/") ? path : path + "/";
}

export function getBasename() {
  return basename;
}

function subscribe(listener: () => void) {
  window.addEventListener("popstate", listener);
  return () => window.removeEventListener("popstate", listener);
}

function toRoute(pathname: string): Route {
  const rest = pathname.startsWith(basename)
    ? pathname.slice(basename.length)
    : "";
  switch (rest.replace(/\/+$/, "")) {
    case "connection-and-account":
      return "account";
    case "double-authentication-choice":
      return "choice";
    default:
      return "personal-info";
  }
}

export function useRoute(): Route {
  const pathname = useSyncExternalStore(subscribe, () => location.pathname);
  return toRoute(pathname);
}

export function navigate(path: string) {
  history.pushState(null, "", basename + path.replace(/^\/+/, ""));
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}
