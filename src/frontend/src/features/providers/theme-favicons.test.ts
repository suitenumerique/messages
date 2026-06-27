import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const FAVICON_SVG = `<svg width="48" height="48" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M5 40L16 30Z" fill="#2845C1"/>
</svg>`;

const getFaviconLinks = () =>
    Array.from(document.head.querySelectorAll<HTMLLinkElement>('link[rel="icon"]'));

const decodeHref = (href: string) => decodeURIComponent(href.replace("data:image/svg+xml,", ""));

/** Let the fetch + parse chain behind `setFaviconBadge` settle. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("theme-favicons", () => {
    // The badged-href cache and the installed links live in module scope, so
    // every test re-imports the module to start from a clean slate.
    let favicons: typeof import("./theme-favicons");
    let cleanup: () => void;

    beforeEach(async () => {
        vi.resetModules();
        vi.stubGlobal(
            "fetch",
            vi.fn(() => Promise.resolve(new Response(FAVICON_SVG, { status: 200 }))),
        );
        favicons = await import("./theme-favicons");
        cleanup = favicons.installThemeFavicons("anct");
    });

    afterEach(() => {
        cleanup();
        vi.unstubAllGlobals();
    });

    it("installs one favicon per color scheme", () => {
        expect(getFaviconLinks().map((link) => [link.media, new URL(link.href).pathname])).toEqual([
            ["(prefers-color-scheme: light)", "/images/anct/favicon-light.svg"],
            ["(prefers-color-scheme: dark)", "/images/anct/favicon-dark.svg"],
        ]);
    });

    it("badges every favicon variant with a dot in the bottom-right corner", async () => {
        favicons.setFaviconBadge(true);
        await flush();

        for (const link of getFaviconLinks()) {
            const svg = decodeHref(link.href);
            expect(svg).toContain('fill="#D7010E"');
            // Fixed radius 9, centered 1.4 radii from the bottom-right corner.
            expect(svg).toContain('cx="35.4" cy="35.4" r="9"');
            // The glyph is punched out around the dot so it stays readable at 16px.
            expect(svg).toContain('mask="url(#favicon-unread-badge-cutout)"');
            expect(svg).toContain('fill="#2845C1"');
        }
    });

    it("fetches each variant once across badge toggles", async () => {
        favicons.setFaviconBadge(true);
        await flush();
        favicons.setFaviconBadge(false);
        favicons.setFaviconBadge(true);
        await flush();

        expect(fetch).toHaveBeenCalledTimes(2);
    });

    it("restores the plain favicon when the badge is cleared", async () => {
        favicons.setFaviconBadge(true);
        await flush();
        favicons.setFaviconBadge(false);

        expect(getFaviconLinks().map((link) => new URL(link.href).pathname)).toEqual([
            "/images/anct/favicon-light.svg",
            "/images/anct/favicon-dark.svg",
        ]);
    });

    it("keeps the plain favicon when the source SVG cannot be fetched", async () => {
        vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
        const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

        favicons.setFaviconBadge(true);
        await flush();

        expect(getFaviconLinks().every((link) => link.href.endsWith(".svg"))).toBe(true);
        consoleError.mockRestore();
    });
});
