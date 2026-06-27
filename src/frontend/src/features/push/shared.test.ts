import { hashEndpoint, listenForPushReceived } from "./shared";

describe("push/shared", () => {
  describe("listenForPushReceived", () => {
    /** Stand-in for `navigator.serviceWorker`, which jsdom does not implement.
     * An EventTarget is enough: the helper only add/removeEventListener's on it
     * and reads `event.data`. */
    const withServiceWorker = () => {
      const target = new EventTarget();
      vi.stubGlobal("navigator", { serviceWorker: target });
      return (data: unknown) =>
        target.dispatchEvent(Object.assign(new Event("message"), { data }));
    };

    afterEach(() => vi.unstubAllGlobals());

    it("fires on the worker's push notice", () => {
      const post = withServiceWorker();
      const onPush = vi.fn();

      listenForPushReceived(onPush);
      post({ type: "push-received" });

      expect(onPush).toHaveBeenCalledOnce();
    });

    it("ignores other worker messages", () => {
      const post = withServiceWorker();
      const onPush = vi.fn();

      listenForPushReceived(onPush);
      // The worker posts `push-subscription-changed` on the same channel.
      post({ type: "push-subscription-changed" });
      post("unstructured");
      post(null);

      expect(onPush).not.toHaveBeenCalled();
    });

    it("stops firing once cleaned up", () => {
      const post = withServiceWorker();
      const onPush = vi.fn();

      listenForPushReceived(onPush)();
      post({ type: "push-received" });

      expect(onPush).not.toHaveBeenCalled();
    });

    // Native shells and older browsers have no service worker; callers rely on
    // this being a silent no-op rather than a throw at bootstrap.
    it("no-ops without a service worker", () => {
      vi.stubGlobal("navigator", {});
      expect(() => listenForPushReceived(vi.fn())()).not.toThrow();
    });
  });

  describe("hashEndpoint", () => {
    // Shared contract vector: the backend computes the SAME value for a push
    // channel's `lookup_hash`/`token_hash` (`_token_hash` in
    // core/services/push/common.py, asserted against this exact literal in
    // test_push.py). The two implementations must stay byte-identical — the app
    // matches THIS device's registration to a server-listed device row purely
    // by comparing these hashes, so any drift silently breaks device sign-out
    // and shared-computer takeover detection.
    it("matches the backend token-hash contract vector", async () => {
      await expect(hashEndpoint("https://push.example/ep-123")).resolves.toBe(
        "aa90f805f294edd82e4284a23521c8b0067582a63c70fb030ddc77214bf8cf7b",
      );
    });

    it("returns lowercase sha256 hex (64 chars)", async () => {
      const hash = await hashEndpoint("https://push.example/other");
      expect(hash).toMatch(/^[0-9a-f]{64}$/);
    });
  });
});
