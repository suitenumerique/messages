import {
  MutationCache,
  Query,
  QueryCache,
  QueryClient,
} from "@tanstack/react-query";

import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { errorToString } from "@/features/api/api-error";

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
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});
