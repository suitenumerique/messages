import { computeCodeChallenge, generateCodeVerifier } from "./pkce";

describe("pkce", () => {
  describe("generateCodeVerifier", () => {
    it("produces a 64-character base64url string", () => {
      const verifier = generateCodeVerifier();
      expect(verifier).toHaveLength(64);
      expect(verifier).toMatch(/^[A-Za-z0-9_-]+$/);
    });

    it("produces a different verifier on each call", () => {
      const verifiers = new Set(
        Array.from({ length: 10 }, () => generateCodeVerifier()),
      );
      expect(verifiers.size).toBe(10);
    });
  });

  describe("computeCodeChallenge", () => {
    it("matches the RFC 7636 appendix B test vector", async () => {
      // The backend recomputes this transform (_s256 in mobile_auth.py) and
      // compares: any drift here breaks the whole mobile login.
      await expect(
        computeCodeChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
      ).resolves.toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
    });

    it("returns an unpadded base64url digest", async () => {
      const challenge = await computeCodeChallenge(generateCodeVerifier());
      // SHA-256 → 32 bytes → 43 base64url chars once padding is stripped.
      expect(challenge).toHaveLength(43);
      expect(challenge).toMatch(/^[A-Za-z0-9_-]+$/);
    });
  });
});
