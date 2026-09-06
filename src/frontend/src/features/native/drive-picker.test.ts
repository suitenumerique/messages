import { Browser } from "@capacitor/browser";

import { openNativeDrivePicker } from "./drive-picker";

vi.mock("@capacitor/browser", () => ({
  Browser: {
    open: vi.fn(),
    close: vi.fn(),
    addListener: vi.fn(),
  },
}));

const browser = vi.mocked(Browser);
const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const CONFIG = { url: "https://drive.test/sdk", apiUrl: "https://drive.test/api" };

const item = {
  id: "1",
  size: 10,
  title: "report.pdf",
  type: "file" as const,
  url_permalink: "https://drive.test/f/1",
  url_preview: "https://drive.test/p/1",
  url: "https://drive.test/f/1",
};

const relayResponse = (event: unknown) =>
  ({ json: () => Promise.resolve(event) }) as Response;

let browserFinished: (() => void) | undefined;

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  browserFinished = undefined;
  browser.open.mockResolvedValue(undefined);
  browser.close.mockResolvedValue(undefined);
  // vi.mocked only keeps the first addListener overload — widen the event name.
  browser.addListener.mockImplementation(((
    eventName: string,
    callback: () => void,
  ) => {
    if (eventName === "browserFinished") browserFinished = callback;
    return Promise.resolve({ remove: vi.fn() });
  }) as typeof Browser.addListener);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("openNativeDrivePicker", () => {
  it("opens the picker in the in-app browser with a relay token", async () => {
    fetchMock.mockResolvedValue(relayResponse(null));

    void openNativeDrivePicker(CONFIG);
    await vi.advanceTimersByTimeAsync(0);

    const openedUrl = browser.open.mock.calls[0][0].url;
    const token = new URL(openedUrl).searchParams.get("token");
    expect(openedUrl).toMatch(/^https:\/\/drive\.test\/sdk\?token=/);
    expect(token).toHaveLength(32);
    expect(fetchMock).toHaveBeenCalledWith(
      `https://drive.test/api/sdk-relay/events/${token}/`,
    );
  });

  it("polls the relay until a selection arrives, then closes the browser", async () => {
    fetchMock
      .mockResolvedValueOnce(relayResponse(null))
      .mockResolvedValueOnce(
        relayResponse({ type: "ITEMS_SELECTED", data: { items: [item] } }),
      );

    const resultPromise = openNativeDrivePicker(CONFIG);
    await vi.advanceTimersByTimeAsync(500);

    await expect(resultPromise).resolves.toEqual({
      type: "picked",
      items: [item],
    });
    expect(browser.close).toHaveBeenCalled();
  });

  it("resolves cancelled on a CANCEL relay event", async () => {
    fetchMock.mockResolvedValue(relayResponse({ type: "CANCEL", data: {} }));

    const resultPromise = openNativeDrivePicker(CONFIG);
    await vi.advanceTimersByTimeAsync(0);

    await expect(resultPromise).resolves.toEqual({ type: "cancelled" });
  });

  it("keeps polling through transient relay errors", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(
        relayResponse({ type: "ITEMS_SELECTED", data: { items: [item] } }),
      );

    const resultPromise = openNativeDrivePicker(CONFIG);
    await vi.advanceTimersByTimeAsync(500);

    await expect(resultPromise).resolves.toEqual({
      type: "picked",
      items: [item],
    });
  });

  it("resolves cancelled when the user dismisses the browser with no selection", async () => {
    fetchMock.mockResolvedValue(relayResponse(null));

    const resultPromise = openNativeDrivePicker(CONFIG);
    await vi.advanceTimersByTimeAsync(0);

    browserFinished?.();
    await expect(resultPromise).resolves.toEqual({ type: "cancelled" });
    // The tab is already gone: closing it again would be a no-op at best.
    expect(browser.close).not.toHaveBeenCalled();
  });

  it("catches a selection posted right before the user closed the browser", async () => {
    fetchMock
      .mockResolvedValueOnce(relayResponse(null))
      .mockResolvedValueOnce(
        relayResponse({ type: "ITEMS_SELECTED", data: { items: [item] } }),
      );

    const resultPromise = openNativeDrivePicker(CONFIG);
    await vi.advanceTimersByTimeAsync(0);

    browserFinished?.();
    await expect(resultPromise).resolves.toEqual({
      type: "picked",
      items: [item],
    });
  });
});
