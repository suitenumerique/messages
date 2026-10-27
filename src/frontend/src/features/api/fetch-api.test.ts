import { APIError, NetworkError } from "./api-error";
import { fetchAPI } from "./fetch-api";

vi.mock("../auth", () => ({ logout: vi.fn() }));

describe("fetchAPI", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("throws a NetworkError when the request gets no response", async () => {
    const cause = new TypeError("Failed to fetch");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(cause));

    const promise = fetchAPI("/api/v1.0/mailboxes/");

    await expect(promise).rejects.toBeInstanceOf(NetworkError);
    await expect(promise).rejects.toHaveProperty("cause", cause);
  });

  it("lets an aborted request reject with its AbortError", async () => {
    const abortError = new DOMException("The operation was aborted.", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));

    await expect(fetchAPI("/api/v1.0/mailboxes/")).rejects.toBe(abortError);
  });

  it("throws an APIError on an HTTP error status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>Bad gateway</html>", { status: 502 })),
    );

    const promise = fetchAPI("/api/v1.0/mailboxes/");

    await expect(promise).rejects.toBeInstanceOf(APIError);
    await expect(promise).rejects.toHaveProperty("code", 502);
  });
});
