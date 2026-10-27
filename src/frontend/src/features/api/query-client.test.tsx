import { focusManager } from "@tanstack/react-query";
import { addToast } from "@/features/ui/components/toaster";
import { APIError, NetworkError } from "./api-error";
import { queryClient } from "./query-client";

vi.mock("@/features/ui/components/toaster", () => ({
  addToast: vi.fn(),
  ToasterItem: ({ children }: { children: React.ReactNode }) => children,
}));

const QUERY_KEY = ["query-client-test"];

// Goes through the query cache, unlike `fetchQuery` which disables retries.
const fetchWith = (queryFn: () => Promise<unknown>) =>
  queryClient
    .getQueryCache()
    .build(queryClient, { queryKey: QUERY_KEY, queryFn })
    .fetch()
    .catch(() => undefined);

describe("queryClient", () => {
  beforeAll(() => {
    // Subscribes the cache to the focus manager, as QueryClientProvider does.
    queryClient.mount();
  });

  afterAll(() => {
    queryClient.unmount();
  });

  beforeEach(() => {
    vi.useFakeTimers();
    focusManager.setFocused(true);
  });

  afterEach(() => {
    queryClient.clear();
    focusManager.setFocused(undefined);
    vi.mocked(addToast).mockClear();
    vi.useRealTimers();
  });

  it("retries a network failure and stays silent once the network is back", async () => {
    const queryFn = vi
      .fn()
      .mockRejectedValueOnce(new NetworkError())
      .mockResolvedValue({ data: [] });

    const promise = fetchWith(queryFn);
    await vi.runAllTimersAsync();

    await expect(promise).resolves.toEqual({ data: [] });
    expect(queryFn).toHaveBeenCalledTimes(2);
    expect(addToast).not.toHaveBeenCalled();
  });

  it("holds the retry while the tab is hidden", async () => {
    focusManager.setFocused(false);
    const queryFn = vi
      .fn()
      .mockRejectedValueOnce(new NetworkError())
      .mockResolvedValue({ data: [] });

    const promise = fetchWith(queryFn);
    await vi.runAllTimersAsync();
    expect(queryFn).toHaveBeenCalledOnce();

    focusManager.setFocused(true);
    await vi.runAllTimersAsync();

    await expect(promise).resolves.toEqual({ data: [] });
    expect(queryFn).toHaveBeenCalledTimes(2);
    expect(addToast).not.toHaveBeenCalled();
  });

  it("toasts once the network stays unreachable", async () => {
    const queryFn = vi.fn().mockRejectedValue(new NetworkError());

    const promise = fetchWith(queryFn);
    await vi.runAllTimersAsync();
    await promise;

    expect(queryFn).toHaveBeenCalledTimes(4);
    expect(addToast).toHaveBeenCalledOnce();
  });

  it("toasts any other error without retrying", async () => {
    const queryFn = vi.fn().mockRejectedValue(new APIError(500));

    const promise = fetchWith(queryFn);
    await vi.runAllTimersAsync();
    await promise;

    expect(queryFn).toHaveBeenCalledOnce();
    expect(addToast).toHaveBeenCalledOnce();
  });

  it("toasts a network failure raised by a mutation without retrying", async () => {
    const mutationFn = vi.fn().mockRejectedValue(new NetworkError());

    await queryClient
      .getMutationCache()
      .build(queryClient, { mutationFn })
      .execute(undefined)
      .catch(() => undefined);

    expect(mutationFn).toHaveBeenCalledOnce();
    expect(addToast).toHaveBeenCalledOnce();
  });
});
