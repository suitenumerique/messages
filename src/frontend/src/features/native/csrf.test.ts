import { getNativeCsrfToken, setNativeCsrfToken } from "./csrf";

describe("native csrf store", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns undefined before any exchange", () => {
    expect(getNativeCsrfToken()).toBeUndefined();
  });

  it("persists the token across reads", () => {
    setNativeCsrfToken("token-from-exchange");
    expect(getNativeCsrfToken()).toBe("token-from-exchange");
  });

  it("survives a WebView reload (localStorage backed)", () => {
    setNativeCsrfToken("token-from-exchange");
    // The store must not rely on module state: read what a fresh page would.
    expect(localStorage.getItem("messages_native-csrf-token")).toBe(
      "token-from-exchange",
    );
  });

  it("clears the token on logout", () => {
    setNativeCsrfToken("token-from-exchange");
    setNativeCsrfToken(null);
    expect(getNativeCsrfToken()).toBeUndefined();
  });
});
