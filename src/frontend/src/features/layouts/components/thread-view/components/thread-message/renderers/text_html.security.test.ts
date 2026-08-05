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
import { renderTextHtml } from "./text_html";

const render = (html: string) => renderTextHtml(html, new Map());

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
    expect(render(`<img srcset="//evil.example/t.png 1x" width="99" height="99">`))
      .toMatch(/srcset/i);
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
    // srcset or background attribute leaking a read receipt.
    expect(src).toMatch(/img-src 'self' data: \$\{getApiOrigin\(\)\}/);
  });
});
