import { getWebCsrfToken, setWebCsrfToken } from "./csrf";

describe("web csrf store", () => {
  afterEach(() => {
    setWebCsrfToken(undefined);
  });

  it("returns undefined before /users/me/ delivered a token", () => {
    expect(getWebCsrfToken()).toBeUndefined();
  });

  it("returns the cached token", () => {
    setWebCsrfToken("token-from-users-me");
    expect(getWebCsrfToken()).toBe("token-from-users-me");
  });

  it("stays out of storage — in-memory only by design", () => {
    setWebCsrfToken("token-from-users-me");
    expect(localStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });
});
