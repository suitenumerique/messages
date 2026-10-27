import {
  MutationCache,
  Query,
  QueryCache,
  QueryClient,
} from "@tanstack/react-query";

import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { errorToString, NetworkError } from "@/features/api/api-error";

const onError = (error: Error, query: unknown) => {
  if ((query as Query).meta?.noGlobalError) {
    return;
  }
  addToast(
    <ToasterItem type="error">
      <span>{errorToString(error)}</span>
    </ToasterItem>,
    {
      toastId: "APPLICATION_ERROR_TOAST",
    },
  );
};

export const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => onError(error, mutation),
  }),
  queryCache: new QueryCache({
    onError: (error, query) => onError(error, query),
  }),
  defaultOptions: {
    queries: {
      // A network failure is mostly transient: the device just woke up and the
      // network is not back yet. React Query holds each retry until the tab is
      // visible again, so the toast only shows once the network stays down.
      retry: (failureCount, error) =>
        error instanceof NetworkError && failureCount < 3,
      refetchOnWindowFocus: false,
    },
  },
});
