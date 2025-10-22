/**
 * UnquoteMessage
 * This util is mainly a port of the Python unquotemail package to TypeScript
 * https://github.com/getfernand/unquotemail
 *
 * Why ?
 * We did not identity library able to wrap quoted content in a collapsible element instead
 * all removing the quotes (https://github.com/mailgun/talon) or only works with plain text email
 * (https://github.com/crisp-oss/email-reply-parser)
 *
 * This utility parses HTML and plain text emails to extract only the new message content
 * by removing quoted sections from previous conversations.
 *
 * Contributions are welcome! Here are the main things you can do:
 * - Add/improve existing handlers to the HANDLERS array (see handlers.ts)
 * - Add new reply patterns to the REPLY_PATTERNS array (see constants.ts)
 * - Add new forward patterns to the FORWARD_PATTERNS array (see constants.ts)
 *
 * Features:
 * - Removes known email client quote markers (.gmail_quote, .protonmail_quote, etc.)
 * - Detects standard reply patterns using regex (e.g., "On YYYY/MM/dd HH:mm:ss, [sender] wrote:")
 * - Handles both HTML and plain text email content
 * - Progressive approach: tries known markers first, then falls back to pattern matching
 */

import i18n from "@/features/i18n/initI18n";
import { FORWARD_PATTERNS, REPLY_PATTERNS } from "./constants";
import { HANDLERS } from "./handlers";
import { UnquoteOptions, UnquoteResult } from "./types";

/**
 * UnquoteMessage class - Main utility for removing quoted content from emails
 */
export class UnquoteMessage {
  #htmlContent: string;
  #textContent: string;
  #options: Required<UnquoteOptions>;

  /**
   * Create a new UnquoteMessage instance
   * @param html - HTML email content
   * @param text - Plain text email content
   * @param options - Optional configuration
   */
  constructor(
    html: string = "",
    text: string = "",
    options: UnquoteOptions = {}
  ) {
    this.#htmlContent = html;
    this.#textContent = text;
    this.#options = {
      mode: "remove",
      ignoreFirstForward: false,
      depth: Infinity,
      ...options,
    };
  }

  /**
   * Get the unquoted HTML content
   * @returns UnquoteResult with unquoted HTML and metadata
   */
  public getHtml(): UnquoteResult {
    if (this.#htmlContent.trim() === "") {
      return {
        content: "",
        hadQuotes: false,
        detectionMethod: null,
      };
    }

    const handlersResult = this.#removeHtmlQuotesByHandlers();
    return handlersResult;
  }

  /**
   * Get the unquoted plain text content
   * @returns UnquoteResult with unquoted text and metadata
   */
  public getText(): UnquoteResult {
    if (this.#textContent.trim() === "") {
      return {
        content: "",
        hadQuotes: false,
        detectionMethod: null,
      };
    }

    return this.#removeTextQuotesByPattern();
  }

  /**
   * Remove quotes from HTML using unified handlers
   * Handles both selector-based and detect-based handlers
   */
  #removeHtmlQuotesByHandlers(): UnquoteResult {
    // Create a temporary DOM element to parse the HTML
    const rootDocument = this.#createDocumentElement(this.#htmlContent);
    if (!rootDocument) {
      return {
        content: this.#htmlContent,
        hadQuotes: false,
        detectionMethod: null,
      };
    }

    const quotedElements = new Set<HTMLElement>();
    const elementsToHandle = new Set<HTMLElement>();

    // Process built-in handlers
    for (const handler of HANDLERS) {
      try {
        let matchedElements = Array.from(
          rootDocument.querySelectorAll<HTMLElement>(handler.selector)
        );

        if (matchedElements.length > 0) {
          // If handler has a detect method, pass the matched elements for validation/processing
          // The detect method can use matchedElements for optimization or do its own querying
          if (handler.detect) {
            matchedElements = matchedElements.flatMap(handler.detect);
          }
          matchedElements.forEach((element) => {
            if (
              !this.#options.ignoreFirstForward ||
              !this.#isForwardContent(element)
            ) {
              elementsToHandle.add(element);
            }

          });
        }
        // If no elements matched the selector, skip this handler entirely (optimization)
      } catch (e) {
        console.warn(
          `Invalid quote selector for ${handler.name}: ${handler.selector}`,
          e
        );
      }
    }

    // If we found elements, process them
    // Filter out elements that exceed max depth
    elementsToHandle.forEach((element) => {
      const currentDepth = this.#getElementDepth(element, elementsToHandle);

      if (currentDepth <= this.#options.depth) {
        quotedElements.add(element);
      }
    });

    const hadQuotes = quotedElements.size > 0;

    // Handle quoted elements based on mode
    if (hadQuotes) {
      if (this.#options.mode === "wrap") {
        this.#wrapQuotedElements(rootDocument, Array.from(quotedElements));
      } else {
        // Remove mode (default)
        quotedElements.forEach((element) => element.remove());
        this.#cleanupEmptyElements(rootDocument);
      }
    }

    return {
      content: rootDocument.body.innerHTML.trim(),
      hadQuotes,
      detectionMethod: "handlers",
    };
  }

  /**
   * Remove quotes from plain text using pattern matching
   */
  #removeTextQuotesByPattern(): UnquoteResult {
    let workingText = this.#textContent;
    let hadQuotes = false;
    let quotedIndex = -1;

    // Find the earliest quote marker
    REPLY_PATTERNS.forEach((pattern) => {
      const match = pattern.exec(workingText);
      if (match && match.index !== undefined) {
        if (quotedIndex === -1 || match.index < quotedIndex) {
          quotedIndex = match.index;
          hadQuotes = true;
        }
      }
      // Reset regex state
      pattern.lastIndex = 0;
    });

    // If quotes detected, extract only the content before them
    if (hadQuotes && quotedIndex > -1) {
      workingText = workingText.substring(0, quotedIndex);
    }

    // Additionally, check for lines starting with ">" (common in plain text quotes)
    const lines = workingText.split("\n");
    let lastNonQuotedLine = lines.length - 1;

    // Scan backwards to find where quotes start
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i].trim();
      if (line.startsWith(">")) {
        hadQuotes = true;
        lastNonQuotedLine = i - 1;
      } else if (line !== "") {
        // Found a non-empty, non-quoted line - this is where new content ends
        break;
      }
    }

    if (hadQuotes && lastNonQuotedLine >= 0) {
      workingText = lines.slice(0, lastNonQuotedLine + 1).join("\n");
    }

    return {
      content: workingText.trim(),
      hadQuotes,
      detectionMethod: "pattern",
    };
  }

  /**
   * Check if an element contains forward patterns
   */
  #isForwardContent(element: HTMLElement): boolean {
    // Get element as text and break it down into lines to keep only the first line
    const text = element.textContent?.trim() || '';
    return FORWARD_PATTERNS.some((pattern) => {
      // Improve pattern matching by checking if the text starts with the pattern
      return text.match(pattern);
    });
  }

  /**
   * Create a DOM element from HTML string
   */
  #createDocumentElement(html: string): Document | null {
    // Use DOMParser to preserve html and body nodes
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    return doc;
  }

  /**
   * Clean up empty elements after quote removal
   */
  #cleanupEmptyElements(document: Document): void {
    const emptyElements: Element[] = [];

    // Find all empty elements
    document.querySelectorAll("*").forEach((el) => {
      const text = el.textContent?.trim() || "";
      const hasChildren = el.children.length > 0;

      // Don't remove elements that might be intentionally empty (like <br>, <img>, etc.)
      const isSelfClosing = ["br", "hr", "img", "input"].includes(
        el.tagName.toLowerCase()
      );

      if (text === "" && !hasChildren && !isSelfClosing) {
        emptyElements.push(el);
      }
    });

    // Remove empty elements
    emptyElements.forEach((el) => el.remove());
  }

  /**
   * Create a details element for wrapping quoted content
   */
  #createDetailsElement(root: Document): HTMLElement {
    const details = root.createElement("details");
    details.className = "email-quoted-content";

    const summary = root.createElement("summary");
    summary.innerHTML = "<span>&hellip;</span>";
    summary.className = "email-quoted-summary";
    summary.dataset.content = i18n.t("Show embedded message");

    details.appendChild(summary);

    return details;
  }

  /**
   * Get the depth of nesting for an element within processed quote elements
   * Returns the number of processed quote ancestors this element has
   */
  #getElementDepth(
    element: HTMLElement,
    processedElements: Set<HTMLElement>
  ): number {
    let depth = 0;
    let parent = element.parentElement;

    while (parent && depth <= this.#options.depth) {
      if (processedElements.has(parent as HTMLElement)) {
        depth++;
      }
      parent = parent.parentElement;
    }

    return depth;
  }

  /**
   * Wrap quoted elements in details tags
   */
  #wrapQuotedElements(document: Document, elements: HTMLElement[]): void {
    if (elements.length === 0) return;

    // Group consecutive elements together
    const groups: HTMLElement[][] = [];
    let currentGroup: HTMLElement[] = [];

    elements.forEach((element, index) => {
      if (currentGroup.length === 0) {
        currentGroup.push(element);
      } else {
        // Check if this element is adjacent to the previous one
        const prevElement = currentGroup[currentGroup.length - 1];
        const areAdjacent =
          prevElement.nextElementSibling === element ||
          prevElement.parentElement === element.parentElement;

        if (areAdjacent) {
          currentGroup.push(element);
        } else {
          // Start a new group
          groups.push([...currentGroup]);
          currentGroup = [element];
        }
      }

      // Push the last group
      if (index === elements.length - 1) {
        groups.push([...currentGroup]);
      }
    });

    // Wrap each group in a details element
    groups.forEach((group) => {
      if (group.length === 0) return;

      const details = this.#createDetailsElement(document);
      const firstElement = group[0];

      // Insert details before the first element
      firstElement.parentElement?.insertBefore(details, firstElement);

      // Move all elements into the details
      group.forEach((element) => {
        details.appendChild(element);
      });
    });
  }

  /**
   * Static helper method to quickly unquote HTML content
   * @param html - HTML content to unquote
   * @param options - Optional configuration
   * @returns Unquoted HTML content string
   */
  static unquoteHtml(html: string, options?: UnquoteOptions): string {
    const unquote = new UnquoteMessage(html, "", options);
    return unquote.getHtml().content;
  }

  /**
   * Static helper method to quickly unquote plain text content
   * @param text - Plain text content to unquote
   * @param options - Optional configuration
   * @returns Unquoted text content string
   */
  static unquoteText(text: string, options?: UnquoteOptions): string {
    const unquote = new UnquoteMessage("", text, options);
    return unquote.getText().content;
  }
}

// Default export for convenience
export default UnquoteMessage;
