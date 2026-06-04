import { useLocation, useNavigate } from "@tanstack/react-router";

/**
 * Returns a function that navigates to the current pathname with a new set of
 * search params. On TanStack Router the pathname already carries the active
 * path segments (mailboxId, threadId, …), so only the caller's params are
 * handed to the structured `search` option, which encodes them safely and
 * prevents XSS / open-redirect issues from user-controlled values.
 */
export const useSafeRouterPush = () => {
  const navigate = useNavigate();
  const location = useLocation();

  return (params: URLSearchParams) => {
    navigate({ to: location.pathname, search: Object.fromEntries(params) });
  };
};
