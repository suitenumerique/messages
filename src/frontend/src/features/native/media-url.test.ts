import { Capacitor } from "@capacitor/core";

import { isNativePlatform } from "./platform";
import { getNativeServerUrl, toNativeMediaUrl } from "./media-url";

vi.mock("@capacitor/core", () => ({
  Capacitor: {},
}));
vi.mock("./platform", () => ({
  isNativePlatform: vi.fn(),
}));

const capacitor = Capacitor as { getServerUrl?: () => string };
const native = vi.mocked(isNativePlatform);

const MEDIA_URL = "https://api.test/api/v1.0/blob/42/preview/?x=1&y=a b";

beforeEach(() => {
  vi.clearAllMocks();
  delete capacitor.getServerUrl;
});

describe("getNativeServerUrl", () => {
  it("returns the bridge server url when the bridge exposes it", () => {
    capacitor.getServerUrl = () => "capacitor://localhost";
    expect(getNativeServerUrl()).toBe("capacitor://localhost");
  });

  it("returns an empty string on the web runtime", () => {
    expect(getNativeServerUrl()).toBe("");
  });
});

describe("toNativeMediaUrl", () => {
  it("leaves the url untouched on the web", () => {
    native.mockReturnValue(false);
    capacitor.getServerUrl = () => "capacitor://localhost";

    expect(toNativeMediaUrl(MEDIA_URL)).toBe(MEDIA_URL);
  });

  it("leaves the url untouched when the bridge exposes no server url", () => {
    native.mockReturnValue(true);

    expect(toNativeMediaUrl(MEDIA_URL)).toBe(MEDIA_URL);
  });

  it.each([
    ["capacitor://localhost", "capacitor://localhost/_capacitor_http_interceptor_?u="],
    ["https://localhost", "https://localhost/_capacitor_http_interceptor_?u="],
  ])(
    "routes the url through the bridge's HTTP interceptor on %s",
    (serverUrl, expectedPrefix) => {
      native.mockReturnValue(true);
      capacitor.getServerUrl = () => serverUrl;

      const proxied = toNativeMediaUrl(MEDIA_URL);

      expect(proxied.startsWith(expectedPrefix)).toBe(true);
      // The target url survives the round trip verbatim, query string included.
      expect(new URL(proxied).searchParams.get("u")).toBe(MEDIA_URL);
    },
  );
});
