/**
 * Adversarial coverage for the text/html body path.
 *
 * The body is attacker-controlled: anyone can email it. Defence is in two
 * layers — DOMPurify, then the sandboxed CSP iframe it is mounted in.
 * Both are asserted here, because each is load-bearing for a different
 * class: DOMPurify for script execution, the frame for layout, forms and
 * remote loads (which survive sanitisation by design).
 */
import { describe, it, expect } from "vitest";
import { renderTextHtml, ExternalImageOptions } from "./text_html";

const render = (html: string) => renderTextHtml(html, new Map());

const renderWithImages = (html: string, overrides: Partial<ExternalImageOptions> = {}) =>
  renderTextHtml(html, new Map(), {
    canDisplayExternalImages: true,
    displayExternalImages: true,
    onExternalImageDetected: () => {},
    getProxiedUrl: (url) => `/proxy?url=${encodeURIComponent(url)}`,
    ...overrides,
  });

describe("layer 1 — sanitiser blocks script execution", () => {
  it.each([
    ["script tag", `<script>alert(1)</script>`],
    ["img onerror", `<img src=x onerror=alert(1)>`],
    ["svg onload", `<svg onload=alert(1)>`],
    ["input autofocus onfocus", `<input autofocus onfocus=alert(1)>`],
    ["iframe srcdoc", `<iframe srcdoc="<script>alert(1)</script>"></iframe>`],
    ["object data", `<object data="//evil.example/x"></object>`],
    ["embed", `<embed src="//evil.example/x">`],
  ])("neutralises %s", (_label, payload) => {
    const out = render(payload);
    expect(out).not.toMatch(/<script/i);
    expect(out).not.toMatch(/\son(error|load|focus|click)\s*=/i);
    expect(out).not.toMatch(/<i?frame|<object|<embed/i);
  });

  it.each([
    ["javascript:", `<a href="javascript:alert(1)">x</a>`],
    ["data:text/html", `<a href="data:text/html,<script>alert(1)</script>">x</a>`],
    ["vbscript:", `<a href="vbscript:msgbox(1)">x</a>`],
  ])("strips dangerous URL scheme %s", (_label, payload) => {
    expect(render(payload)).not.toMatch(/javascript:|vbscript:|data:text\/html/i);
  });

  it("forces safe rel/target on links", () => {
    const out = render(`<a href="https://example.com">x</a>`);
    expect(out).toContain('rel="noopener noreferrer"');
    expect(out).toContain('target="_blank"');
  });

  it("drops tracking pixels", () => {
    expect(render(`<img src="https://e.example/p.gif" width="1" height="1">`))
      .not.toMatch(/<img/i);
    expect(render(`<img src="https://e.example/p.gif" style="display:none">`))
      .not.toMatch(/<img/i);
  });
});

describe("layer 1 — remote images only load through the proxy", () => {
  it("drops srcset so the browser cannot pick a non-proxied candidate", () => {
    const out = renderWithImages(
      `<img src="https://e.example/a.png" srcset="https://evil.example/t.png 2x" sizes="50vw" width="99" height="99">`
    );
    expect(out).not.toMatch(/srcset|sizes|evil\.example/i);
    expect(out).toContain(`src="/proxy?url=${encodeURIComponent("https://e.example/a.png")}"`);
  });

  it("falls back on the first srcset candidate when there is no src", () => {
    const out = renderWithImages(
      `<img srcset="https://e.example/a,b.png 1x, https://e.example/c.png 2x" width="99" height="99">`
    );
    expect(out).not.toMatch(/srcset/i);
    expect(out).toContain(`src="/proxy?url=${encodeURIComponent("https://e.example/a,b.png")}"`);
  });

  it("drops a srcset-only image when external images are not displayed", () => {
    expect(renderWithImages(
      `<img srcset="https://evil.example/t.png 1x" width="99" height="99">`,
      { displayExternalImages: false },
    )).not.toMatch(/<img|evil\.example/i);
  });

  it("renders the <img> fallback of a <picture> instead of its sources", () => {
    const out = renderWithImages(
      `<picture><source srcset="https://evil.example/t.webp" type="image/webp"><img src="https://e.example/a.png" width="99" height="99"></picture>`
    );
    expect(out).not.toMatch(/<source|evil\.example/i);
    expect(out).toContain(`src="/proxy?url=${encodeURIComponent("https://e.example/a.png")}"`);
  });

  it("drops the image when it cannot be proxied", () => {
    expect(renderWithImages(
      `<img src="https://e.example/a.png" width="99" height="99">`,
      { getProxiedUrl: () => null },
    )).not.toMatch(/<img/i);
  });
});

describe("layer 2 — the frame contains what the sanitiser lets through", () => {
  /**
   * These payloads survive DOMPurify on purpose: a message may legitimately
   * use inline styles, and stripping every layout property would mangle
   * ordinary mail. They are contained by the iframe instead — `position:
   * fixed` resolves against the iframe viewport, forms cannot submit
   * without `allow-forms`, and remote loads are refused by `img-src`.
   *
   * Asserted so that if anyone moves this body out of the frame, or
   * loosens the sandbox/CSP, these become live vulnerabilities and this
   * test says so.
   */
  it("documents that layout and form markup do survive sanitisation", () => {
    expect(render(`<div style="position:fixed;top:0">x</div>`)).toMatch(/position/i);
    expect(render(`<form action="//evil.example"><input name="pw"></form>`)).toMatch(/<input/i);
    expect(render(`<table background="//evil.example/t.png"><tr><td>x</td></tr></table>`))
      .toMatch(/background/i);
  });

  it("the mount is a sandboxed iframe that cannot run scripts or submit forms", async () => {
    const src = await import("fs").then((fs) =>
      fs.readFileSync(
        "src/features/layouts/components/thread-view/components/thread-message/thread-message-body.tsx",
        "utf8"
      )
    );
    const sandbox = src.match(/sandbox="([^"]+)"/)?.[1] ?? "";
    expect(sandbox).not.toContain("allow-scripts");
    expect(sandbox).not.toContain("allow-forms");
    expect(src).toMatch(/srcDoc=\{wrappedHtml\}/);
  });

  it("the frame CSP forbids scripts and non-proxied remote loads", async () => {
    const src = await import("fs").then((fs) =>
      fs.readFileSync(
        "src/features/layouts/components/thread-view/components/thread-message/thread-message-body.tsx",
        "utf8"
      )
    );
    expect(src).toMatch(/"script-src 'none'"/);
    expect(src).toMatch(/"default-src 'none'"/);
    expect(src).toMatch(/"connect-src 'none'"/);
    // Remote images only via our own origin/API — this is what stops a
    // background attribute or a CSS url() leaking a read receipt.
    expect(src).toMatch(/img-src 'self' data: \$\{getApiOrigin\(\)\}/);
  });
});
