import { afterEach, describe, it, expect } from "vitest";
import {
  findAnchorTarget,
  findFollowedLink,
  getFirstSrcsetUrl,
  mayHaveViewportHeightLengths,
  removeViewportHeightLengths,
} from "./utils";

describe("getFirstSrcsetUrl", () => {
  it.each([
    ["https://e.example/a.png 1x, https://e.example/b.png 2x", "https://e.example/a.png"],
    ["https://e.example/a.png, https://e.example/b.png 2x", "https://e.example/a.png"],
    ["  ,https://e.example/a.png 480w", "https://e.example/a.png"],
    ["https://e.example/a,b.png 1x", "https://e.example/a,b.png"],
    ["https://e.example/a.png", "https://e.example/a.png"],
  ])("extracts the first candidate of %s", (srcset, expected) => {
    expect(getFirstSrcsetUrl(srcset)).toBe(expected);
  });

  it.each(["", "   ", " , , "])("returns null for an empty srcset %j", (srcset) => {
    expect(getFirstSrcsetUrl(srcset)).toBeNull();
  });
});

describe("removeViewportHeightLengths", () => {
  const ruleText = (selector: string) =>
    Array.from(document.styleSheets)
      .flatMap((sheet) => Array.from(sheet.cssRules))
      .flatMap((rule) => [rule, ...Array.from((rule as Partial<CSSStyleRule>).cssRules ?? [])])
      .find((rule) => (rule as Partial<CSSStyleRule>).selectorText === selector)?.cssText;

  afterEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
  });

  it("drops the declarations sized against the viewport height from style sheets", () => {
    document.head.innerHTML = `<style>
      .hero { height: 100vh; color: red; }
      .calc { min-height: calc(100dvh - 2px); margin: 0px; }
      @media (max-width: 600px) { .nested { max-height: 50VMIN; width: 10vw; } }
    </style>`;
    expect(ruleText(".hero")).toContain("100vh");

    removeViewportHeightLengths(document);

    expect(ruleText(".hero")).not.toContain("height");
    expect(ruleText(".hero")).toContain("color: red");
    expect(ruleText(".calc")).not.toContain("min-height");
    expect(ruleText(".calc")).toContain("margin");
    expect(ruleText(".nested")).not.toContain("max-height");
    expect(ruleText(".nested")).toContain("width: 10vw");
  });

  it("drops the declarations sized against the viewport height from style attributes", () => {
    document.body.innerHTML = '<div style="min-height: 100vh; padding: 24px 0px;"></div>';
    const element = document.querySelector("div")!;
    expect(element.style.getPropertyValue("min-height")).toBe("100vh");

    removeViewportHeightLengths(document);

    expect(element.style.getPropertyValue("min-height")).toBe("");
    expect(element.style.getPropertyValue("padding")).toBe("24px 0px");
  });

  it("skips elements with a style attribute but no inline style object", () => {
    // Older browsers, like jsdom, give MathML elements no `style` object.
    document.body.innerHTML = '<math style="display: block"><mi>x</mi></math><p style="height: 50vh"></p>';

    expect(() => removeViewportHeightLengths(document)).not.toThrow();
    expect(document.querySelector("p")!.style.getPropertyValue("height")).toBe("");
  });

  it("drops container query heights and lengths following a sign or an operator", () => {
    document.head.innerHTML = `<style>
      .container { min-height: 100cqh; width: 50cqw; }
      .negative { margin-top: -50vh; padding: 0px; }
      .operator { height: calc(100% - 10vh); color: blue; }
    </style>`;

    removeViewportHeightLengths(document);

    expect(ruleText(".container")).not.toContain("min-height");
    expect(ruleText(".container")).toContain("50cqw");
    expect(ruleText(".negative")).not.toContain("margin-top");
    expect(ruleText(".negative")).toContain("padding");
    expect(ruleText(".operator")).not.toContain("height");
    expect(ruleText(".operator")).toContain("color: blue");
  });

  it("keeps names only ending like a viewport length", () => {
    document.head.innerHTML = `<style>
      .animated { animation-name: slide-100vh; font-family: Brand2vb, sans-serif; }
    </style>`;

    removeViewportHeightLengths(document);

    expect(ruleText(".animated")).toContain("slide-100vh");
    expect(ruleText(".animated")).toContain("Brand2vb");
  });

  it("keeps lengths found in urls and strings", () => {
    document.head.innerHTML = `<style>
      .image { background-image: url("hero) 100vh.png"); }
      .label::before { content: "a \\" 100vh"; }
    </style>`;

    removeViewportHeightLengths(document);

    expect(ruleText(".image")).toContain("100vh.png");
    expect(ruleText(".label::before")).toContain("100vh");
  });
});

describe("mayHaveViewportHeightLengths", () => {
  it.each([
    '<div style="min-height: 100vh">',
    "<style>.a { max-height: 50VMIN; }</style>",
    "<style>.a { height: calc(100% - 1.5dvh); }</style>",
    "<style>.a { min-height: 100cqh; }</style>",
  ])("spots a viewport height length in %s", (html) => {
    expect(mayHaveViewportHeightLengths(html)).toBe(true);
  });

  it.each([
    '<p style="height: 100px; width: 50vw">Hello</p>',
    "<style>.a { width: 10cqw; }</style>",
    "",
  ])("finds no viewport height length in %j", (html) => {
    expect(mayHaveViewportHeightLengths(html)).toBe(false);
  });
});

describe("findAnchorTarget", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("finds the element with the fragment as id before a named anchor", () => {
    document.body.innerHTML = '<a name="part">named</a><h2 id="part">by id</h2>';
    expect(findAnchorTarget(document, "part")?.textContent).toBe("by id");
  });

  it("finds a named anchor, but no other named element", () => {
    document.body.innerHTML = '<input name="part"><a name="part">named</a>';
    expect(findAnchorTarget(document, "part")?.localName).toBe("a");
  });

  it("decodes the fragment", () => {
    document.body.innerHTML = '<h2 id="été 2026">x</h2>';
    expect(findAnchorTarget(document, "%C3%A9t%C3%A9%202026")?.id).toBe("été 2026");
  });

  it("finds nothing for a missing or malformed fragment", () => {
    document.body.innerHTML = '<h2 id="part">x</h2>';
    expect(findAnchorTarget(document, "other")).toBeNull();
    expect(findAnchorTarget(document, "%E0%A4%A")).toBeNull();
  });
});

describe("findFollowedLink", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it.each([
    ["a table cell", '<a href="https://evil.example"><table><tr><td><a id="inner">Verify</a></td></tr></table></a>'],
    ["an SVG image", '<a href="https://evil.example"><svg><a><text id="inner">Verify</text></a></svg></a>'],
  ])("skips an anchor without target URL nested in %s", (_label, html) => {
    document.body.innerHTML = html;
    const followedLink = findFollowedLink(document.getElementById("inner"));
    expect(followedLink?.link.localName).toBe("a");
    expect(followedLink?.href).toBe("https://evil.example");
  });

  it("finds an SVG anchor with a legacy xlink:href", () => {
    document.body.innerHTML = '<svg><a xlink:href="https://evil.example"><text id="inner">x</text></a></svg>';
    expect(findFollowedLink(document.getElementById("inner"))?.href).toBe("https://evil.example");
  });

  it("finds an image map area", () => {
    document.body.innerHTML = '<map name="m"><area id="area" shape="default" href="https://evil.example"></map>';
    expect(findFollowedLink(document.getElementById("area"))?.href).toBe("https://evil.example");
  });

  it("keeps an empty target URL, which still leads somewhere", () => {
    document.body.innerHTML = '<a id="empty" href="">x</a>';
    expect(findFollowedLink(document.getElementById("empty"))?.href).toBe("");
  });

  it("finds nothing outside of a link", () => {
    document.body.innerHTML = '<p><a name="part"><span id="inner">x</span></a></p>';
    expect(findFollowedLink(document.getElementById("inner"))).toBeNull();
    expect(findFollowedLink(null)).toBeNull();
  });
});
