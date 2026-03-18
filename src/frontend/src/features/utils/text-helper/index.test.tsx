import React from "react";
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TextHelper } from "./index";

const render = (nodes: React.ReactNode[]) =>
    renderToStaticMarkup(<>{nodes}</>);

describe("TextHelper", () => {
    describe("renderMentions", () => {
        it("should return plain text when there are no mentions", () => {
            const result = TextHelper.renderMentions("Hello world");
            expect(result).toEqual(["Hello world"]);
        });

        it("should render a single mention as a span", () => {
            const html = render(TextHelper.renderMentions("Hello @[John Doe]"));
            expect(html).toBe(
                'Hello <span class="mention">@John Doe</span>',
            );
        });

        it("should render a single mention with custom class name", () => {
            const html = render(TextHelper.renderMentions("Hello @[John Doe]", undefined, { baseClassName: "thread-event" }));
            expect(html).toBe(
                'Hello <span class="thread-event__mention">@John Doe</span>',
            );
        });

        it("should render multiple mentions", () => {
            const html = render(
                TextHelper.renderMentions("@[Alice] and @[Bob] are here"),
            );
            expect(html).toContain(
                '<span class="mention">@Alice</span>',
            );
            expect(html).toContain(
                '<span class="mention">@Bob</span>',
            );
            expect(html).toContain(" and ");
            expect(html).toContain(" are here");
        });

        it("should highlight the current user mention", () => {
            const html = render(
                TextHelper.renderMentions("Hey @[Alice]", "Alice"),
            );
            expect(html).toContain("mention--highlight");
        });

        it("should not highlight other users as self", () => {
            const html = render(
                TextHelper.renderMentions("Hey @[Bob]", "Alice"),
            );
            expect(html).not.toContain("mention--highlight");
        });

        it("should handle mentions with spaces in names", () => {
            const html = render(
                TextHelper.renderMentions("cc @[Jean-Pierre Dupont]"),
            );
            expect(html).toContain("@Jean-Pierre Dupont");
        });

        it("should not treat @name without brackets as a mention", () => {
            const result = TextHelper.renderMentions("Hello @John");
            expect(result).toEqual(["Hello @John"]);
        });
    });

    describe("buildMentionPattern", () => {
        it("should match a mention at the start of the string", () => {
            const pattern = TextHelper.buildMentionPattern("John");
            expect(pattern.test("@John is here")).toBe(true);
        });

        it("should match a mention preceded by a space", () => {
            const pattern = TextHelper.buildMentionPattern("John");
            expect(pattern.test("Hello @John")).toBe(true);
        });

        it("should not match a mention inside an email address", () => {
            const pattern = TextHelper.buildMentionPattern("bar");
            expect(pattern.test("foo@bar.com")).toBe(false);
        });

        it("should not match a partial name", () => {
            const pattern = TextHelper.buildMentionPattern("John");
            expect(pattern.test("@Johnny")).toBe(false);
        });

        it("should match when followed by punctuation", () => {
            const pattern = TextHelper.buildMentionPattern("John");
            expect(pattern.test("@John, are you there?")).toBe(true);
        });

        it("should handle names with special regex characters", () => {
            const pattern = TextHelper.buildMentionPattern("John (Doe)");
            expect(pattern.test("Hello @John (Doe)!")).toBe(true);
        });

        it("should handle accented characters in names", () => {
            const pattern = TextHelper.buildMentionPattern("René");
            expect(pattern.test("cc @René")).toBe(true);
        });

        it("should not match when preceded by a letter", () => {
            const pattern = TextHelper.buildMentionPattern("René");
            expect(pattern.test("bonjour@René")).toBe(false);
        });

        it("should match with global flag", () => {
            const pattern = TextHelper.buildMentionPattern("John", "gu");
            const matches = "Hello @John and @John".match(pattern);
            expect(matches).toHaveLength(2);
        });
    });

    describe("renderLinks", () => {
        it("should return plain text when there are no URLs", () => {
            const result = TextHelper.renderLinks(["Hello world"]);
            expect(result).toEqual(["Hello world"]);
        });

        it("should render an http URL as a link", () => {
            const html = render(
                TextHelper.renderLinks(["Visit http://example.com"]),
            );
            expect(html).toContain(
                '<a href="http://example.com" target="_blank" rel="noopener noreferrer">http://example.com</a>',
            );
        });

        it("should render an https URL as a link", () => {
            const html = render(
                TextHelper.renderLinks(["Visit https://example.com"]),
            );
            expect(html).toContain(
                '<a href="https://example.com" target="_blank" rel="noopener noreferrer">https://example.com</a>',
            );
        });

        it("should strip trailing punctuation from URLs", () => {
            const html = render(
                TextHelper.renderLinks(["See https://example.com."]),
            );
            expect(html).toContain('href="https://example.com"');
            expect(html).toContain("https://example.com</a>.");
        });

        it("should strip trailing comma from URLs", () => {
            const html = render(
                TextHelper.renderLinks(["Check https://a.com, please"]),
            );
            expect(html).toContain('href="https://a.com"');
            expect(html).toContain("https://a.com</a>, please");
        });

        it("should strip multiple trailing punctuation characters", () => {
            const html = render(
                TextHelper.renderLinks(["Wow https://example.com!)."]),
            );
            expect(html).toContain('href="https://example.com"');
            expect(html).toContain("https://example.com</a>!).");
        });

        it("should preserve balanced parentheses in URLs", () => {
            const html = render(
                TextHelper.renderLinks(["See https://en.wikipedia.org/wiki/Function_(mathematics)"]),
            );
            expect(html).toContain('href="https://en.wikipedia.org/wiki/Function_(mathematics)"');
        });

        it("should strip unmatched trailing parenthesis from URL", () => {
            const html = render(
                TextHelper.renderLinks(["(see https://example.com)"]),
            );
            expect(html).toContain('href="https://example.com"');
            expect(html).toContain("https://example.com</a>)");
        });

        it("should preserve balanced parens and strip trailing punctuation after", () => {
            const html = render(
                TextHelper.renderLinks(["Check https://en.wikipedia.org/wiki/Foo_(bar)."]),
            );
            expect(html).toContain('href="https://en.wikipedia.org/wiki/Foo_(bar)"');
            expect(html).toContain("Foo_(bar)</a>.");
        });

        it("should render multiple URLs in the same text", () => {
            const html = render(
                TextHelper.renderLinks([
                    "Go to https://a.com and https://b.com",
                ]),
            );
            expect(html).toContain('href="https://a.com"');
            expect(html).toContain('href="https://b.com"');
        });

        it("should preserve URL paths and query strings", () => {
            const html = render(
                TextHelper.renderLinks([
                    "See https://example.com/path?q=1&b=2#frag",
                ]),
            );
            expect(html).toContain(
                'href="https://example.com/path?q=1&amp;b=2#frag"',
            );
        });

        it("should pass through non-string nodes untouched", () => {
            const element = <strong>bold</strong>;
            const result = TextHelper.renderLinks([element, " text"]);
            expect(result[0]).toBe(element);
        });

        it("should handle an empty array", () => {
            expect(TextHelper.renderLinks([])).toEqual([]);
        });

        it("should pass optional props to link", () => {
            const html = render(
                TextHelper.renderLinks([
                    "See https://example.com",
                ], { props: { title:  "A link", className: "linkified" } }),
            );
            expect(html).toContain('href="https://example.com" target="_blank"');
            expect(html).toContain('title="A link"');
            expect(html).toContain('class="linkified"');
        });
    });

    describe("chaining renderMentions then renderLinks", () => {
        it("should render both mentions and links", () => {
            const html = render(
                TextHelper.renderLinks(
                    TextHelper.renderMentions(
                        "Hey @[Alice], see https://example.com",
                    ),
                ),
            );
            expect(html).toContain("mention");
            expect(html).toContain("@Alice");
            expect(html).toContain('href="https://example.com"');
        });

        it("should not linkify inside mention nodes", () => {
            const html = render(
                TextHelper.renderLinks(
                    TextHelper.renderMentions("@[https://not-a-link.com]"),
                ),
            );
            // The mention should be rendered as a mention, not a link
            expect(html).toContain("mention");
            expect(html).not.toContain("<a ");
        });
    });
});
