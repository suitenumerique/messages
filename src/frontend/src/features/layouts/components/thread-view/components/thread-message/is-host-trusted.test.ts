import { describe, expect, it } from "vitest";
import { isHostTrusted } from "./is-host-trusted";

describe("isHostTrusted", () => {
    it("should not trust anything when the allowlist is empty", () => {
        expect(isHostTrusted("example.com", [])).toBe(false);
    });

    it("should trust every host when the allowlist is a lone '*'", () => {
        expect(isHostTrusted("anything.example", ["*"])).toBe(true);
        expect(isHostTrusted("evil.test", ["*"])).toBe(true);
    });

    it("should match an exact host", () => {
        expect(isHostTrusted("gouv.fr", ["gouv.fr"])).toBe(true);
    });

    it("should not match a subdomain for a non-wildcard pattern", () => {
        expect(isHostTrusted("impots.gouv.fr", ["gouv.fr"])).toBe(false);
    });

    it("should match subdomains for a wildcard pattern", () => {
        expect(isHostTrusted("impots.gouv.fr", ["*.gouv.fr"])).toBe(true);
    });

    it("should match the bare domain for a wildcard pattern", () => {
        expect(isHostTrusted("gouv.fr", ["*.gouv.fr"])).toBe(true);
    });

    it("should not treat a wildcard as a plain suffix", () => {
        // "*.gouv.fr" must not match a host that merely ends with "gouv.fr"
        // without a dot boundary.
        expect(isHostTrusted("evilgouv.fr", ["*.gouv.fr"])).toBe(false);
    });

    it("should match case-insensitively", () => {
        expect(isHostTrusted("Example.COM", ["example.com"])).toBe(true);
        expect(isHostTrusted("FOO.Example.com", ["*.EXAMPLE.com"])).toBe(true);
    });

    it("should match when any pattern in the list matches", () => {
        expect(isHostTrusted("example.com", ["gouv.fr", "example.com"])).toBe(true);
    });

    it("should ignore blank patterns", () => {
        expect(isHostTrusted("example.com", ["", "   "])).toBe(false);
    });
});
