import { describe, expect, it } from "vitest";
import { linkifyHtml } from "./linkify";

describe("linkifyHtml", () => {
    it("should return empty content unchanged", () => {
        expect(linkifyHtml("")).toBe("");
    });

    it("should leave text without URL untouched", () => {
        const html = "<p>Hello world</p>";
        expect(linkifyHtml(html)).toBe(html);
    });

    it("should wrap a bare http(s) URL in an anchor", () => {
        expect(linkifyHtml("<p>See https://example.com/page for details</p>")).toBe(
            '<p>See <a href="https://example.com/page" target="_blank" rel="noopener noreferrer">https://example.com/page</a> for details</p>'
        );
    });

    it("should prefix www URLs with https", () => {
        expect(linkifyHtml("<p>Visit www.example.com now</p>")).toBe(
            '<p>Visit <a href="https://www.example.com" target="_blank" rel="noopener noreferrer">www.example.com</a> now</p>'
        );
    });

    it("should linkify several URLs in the same text node", () => {
        const result = linkifyHtml("<p>https://a.example and https://b.example</p>");
        expect(result).toContain('href="https://a.example"');
        expect(result).toContain('href="https://b.example"');
        expect(result).toContain("</a> and <a");
    });

    it("should split comma-separated URLs pasted without a space", () => {
        const result = linkifyHtml("<p>https://a.example,https://b.example</p>");
        expect(result).toContain('href="https://a.example"');
        expect(result).toContain('href="https://b.example"');
        expect(result).toContain("</a>,<a");
    });

    it("should not touch URLs already wrapped in an anchor", () => {
        const html = '<p><a href="https://real.example">https://displayed.example</a></p>';
        expect(linkifyHtml(html)).toBe(html);
    });

    it("should not linkify inside style or script contents", () => {
        const html = "<style>body { background: url(https://example.com/bg.png); }</style><p>text</p>";
        expect(linkifyHtml(html)).toBe(html);
    });

    it("should exclude trailing sentence punctuation from the URL", () => {
        expect(linkifyHtml("<p>Go to https://example.com/page.</p>")).toBe(
            '<p>Go to <a href="https://example.com/page" target="_blank" rel="noopener noreferrer">https://example.com/page</a>.</p>'
        );
    });

    it("should exclude a closing parenthesis wrapping the URL", () => {
        expect(linkifyHtml("<p>(see https://example.com/page)</p>")).toBe(
            '<p>(see <a href="https://example.com/page" target="_blank" rel="noopener noreferrer">https://example.com/page</a>)</p>'
        );
    });

    it("should keep balanced parentheses inside the URL", () => {
        expect(linkifyHtml("<p>https://en.wikipedia.org/wiki/Test_(unit)</p>")).toBe(
            '<p><a href="https://en.wikipedia.org/wiki/Test_(unit)" target="_blank" rel="noopener noreferrer">https://en.wikipedia.org/wiki/Test_(unit)</a></p>'
        );
    });

    it("should linkify URLs in nested markup while preserving structure", () => {
        const result = linkifyHtml("<div><p>intro</p><blockquote>quoted https://example.com text</blockquote></div>");
        expect(result).toBe(
            '<div><p>intro</p><blockquote>quoted <a href="https://example.com" target="_blank" rel="noopener noreferrer">https://example.com</a> text</blockquote></div>'
        );
    });
});
