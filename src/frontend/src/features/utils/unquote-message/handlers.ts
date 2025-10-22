import { FORWARD_PATTERNS, REPLY_PATTERNS } from "./constants";
import { CustomHandler } from "./types";

/**
 * Unified email client handlers
 * Each handler can use:
 * - A CSS selector (for simple cases)
 * - A detect function (for complex cases requiring custom logic)
 * - Both (selector to find elements, detect to validate/process)
 *
 * Order matters: check more specific patterns before generic ones
 */
export const HANDLERS: CustomHandler[] = [
  // ==================== Simple Selector Handlers ====================
  // These use CSS selectors without custom logic
  {
    name: "Quote Separator",
    selector: 'blockquote[data-type="quote-separator"]',
  },
  {
    name: "Freshdesk",
    selector: "div.freshdesk_quote",
  },
  {
    name: "Front",
    selector: ".front-blockquote",
  },
  {
    name: "Spark",
    selector: '[name="messageReplySection"]',
  },
  {
    name: "Notion",
    selector: "blockquote.notion-mail-quote",
  },
  {
    name: "QT",
    selector: 'blockquote[type="cite"][id="qt"]',
  },
  {
    name: "Yahoo",
    selector: "div.yahoo_quoted",
  },
  {
    name: "Yahoo ydp",
    selector: 'div[class$="yahoo_quoted"][id$="yahoo_quoted"]',
  },
  {
    name: "GetFernand",
    selector: "div.fernand_quote",
  },
  {
    name: "Intercom",
    selector: "div.history",
  },
  {
    name: "Microsoft Office",
    selector: "div#mail-editor-reference-message-container",
  },
  {
    name: "ProtonMail",
    selector: ".protonmail_quote",
  },
  {
    name: "Trix",
    selector: "div.trix-content>blockquote",
  },
  // ==================== Complex Handlers ====================
  // ==================== Mozilla Thunderbird ====================
  // must be before Apple due to blockquote overlap)
  {
    name: "Mozilla Thunderbird",
    selector: "div.moz-cite-prefix",
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      let nextSibling = element.nextElementSibling;
      while (nextSibling && nextSibling.nodeType !== Node.ELEMENT_NODE) {
        nextSibling = nextSibling.nextSibling as Element | null;
      }

      if (nextSibling && nextSibling.matches('blockquote[type="cite"]')) {
        detectedElements.push(nextSibling as HTMLElement, element);
      }
      return detectedElements;
    },
  },

  // ==================== Gmail ====================
  {
    name: "Gmail attr",
    selector: ".gmail_quote_container.gmail_quote > .gmail_attr",
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      if (element.parentElement) {
        detectedElements.push(element.parentElement);
      }
      return detectedElements;
    },
  },
  {
    name: "Gmail blockquote",
    selector: "div.gmail_quote > blockquote.gmail_quote",
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      if (element.parentElement) {
          detectedElements.push(element.parentElement);
      }
      return detectedElements;
    },
  },

  // ==================== Ymail ====================
  // remove everything after signature
  {
    name: "Ymail",
    selector: "div.ymail_android_signature",
    detect: (element) => {
      const detectedElements: HTMLElement[] = [element];
      let sibling = element.nextElementSibling;
      while (sibling) {
        detectedElements.push(sibling as HTMLElement);
        sibling = sibling.nextElementSibling;
      }
      return detectedElements;
    },
  },

  // ==================== Microsoft Outlook ====================
  // Web/Rich HTML with sophisticated style check
  {
    name: "Microsoft Outlook Web",
    selector: 'div[style^="border:none;border-top:solid"]>p.MsoNormal>b',
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      if (!element.parentElement?.parentElement) {
        return detectedElements;
      }

      let msoRoot = element.parentElement.parentElement;
      const style = msoRoot.getAttribute("style") || "";

      // Normalize units to 'in' and check for specific padding pattern
      const normalizedStyle = style.replaceAll(/(cm|pt|mm)/g, "in");

      if (normalizedStyle.endsWith(" 1.0in;padding:3.0in 0in 0in 0in")) {
        // Check if parent has only one element child
        if (msoRoot.parentElement) {
          const elementChildren = Array.from(
            msoRoot.parentElement.childNodes
          ).filter((node) => node.nodeType === Node.ELEMENT_NODE);

          if (elementChildren.length === 1) {
            msoRoot = msoRoot.parentElement as HTMLElement;
          }
        }

        // Collect all next siblings
        detectedElements.push(msoRoot);
        let sibling = msoRoot.nextElementSibling;
        while (sibling) {
          detectedElements.push(sibling as HTMLElement);
          sibling = sibling.nextElementSibling;
        }
      }

      return detectedElements;
    },
  },

  // ==================== Microsoft Outlook Desktop ====================
  {
    name: "Microsoft Outlook Desktop",
    selector: "div#divRplyFwdMsg",
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      let prev = element.previousElementSibling;
      while (prev) {
        if (prev.tagName.toLowerCase() === "hr") {
          // It's a reply from Outlook!
          detectedElements.push(element, prev as HTMLElement);
          let sibling = element.nextElementSibling;
          while (sibling) {
            detectedElements.push(sibling as HTMLElement);
            sibling = sibling.nextElementSibling;
          }
          return detectedElements;
        }
        prev = prev.previousElementSibling;
      }

      return detectedElements;
    },
  },

  // ==================== ZMail ====================
  {
    name: "ZMail",
    selector: ".zmail_extra_hr + div.zmail_extra",
    detect: (element) => {
      const previousSibling = element.previousElementSibling as HTMLElement;
      return [previousSibling, element];
    },
  },

  // ==================== Zendesk ====================
  {
    name: "Zendesk",
    selector: "div.quotedReply > blockquote",
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      if (element.parentElement) {
        detectedElements.push(element.parentElement);
      }
      return detectedElements;
    },
  },

  // ==================== Zoho ====================
  // remove everything after + previous separator
  {
    name: "Zoho",
    selector: 'div[title="beforequote:::"]',
    detect: (element) => {
      const detectedElements: HTMLElement[] = [element];
      let sibling = element.nextElementSibling;
      while (sibling) {
        detectedElements.push(sibling as HTMLElement);
        sibling = sibling.nextElementSibling;
      }
      // Check previous sibling for separator
      const previous = element.previousElementSibling as HTMLElement;
      if (previous && previous.textContent?.trim().startsWith("---")) {
        detectedElements.push(previous);
      }
      return detectedElements;
    },
  },

  // ==================== Alimail ====================
  {
    name: "Alimail",
    selector: "blockquote > div.alimail-quote",
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      detectedElements.push(element.parentElement as HTMLElement);
      return detectedElements;
    },
  },

  // ==================== Apple Mail ====================
  // complex blockquote handling
  {
    name: "Apple Mail",
    selector: 'html[class*="apple-mail"] blockquote[type="cite"] > div[dir]',
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      if (!element.parentElement) return detectedElements;

      const blockquote = element.parentElement;
      detectedElements.push(blockquote);

      let previous = blockquote.previousElementSibling as HTMLElement;
      while (previous && previous.nodeType !== Node.ELEMENT_NODE) {
        previous = previous.previousSibling as HTMLElement;
      }

      if (previous && previous.getAttribute("dir")) {
        const children = Array.from(previous.children);
        for (const child of children) {
          if (
            child.tagName.toLowerCase() === "blockquote" &&
            child.parentElement
          ) {
            detectedElements.push(child.parentElement as HTMLElement);
            break;
          }
        }
      }

      return detectedElements;
    },
  },
  // ==================== Custom generic handlers in last resort ====================
  // These require custom detection logic
  {
    name: "Generic Blockquote Cite",
    selector: 'div:has(> blockquote[type="cite"]) + blockquote[type="cite"]',
    detect: (element) => {
      const previousSibling = element.previousElementSibling as HTMLElement;
      return [previousSibling, element];
    }
  },
  {
    name: "Reply intro",
    selector: '[id$="reply-intro"] + blockquote[type="cite"]',
    detect: (element) => {
      const previousSibling = element.previousElementSibling as HTMLElement;
      return [previousSibling, element];
    }
  },
  {
    name: "Blockquote Cite starting with reply or forward pattern",
    selector: 'blockquote[type="cite"]',
    detect: (element) => {
      const detectedElements: HTMLElement[] = [];
      const textContent = element.textContent?.trim().substring(0, 255) || '';
      if (textContent) {
        const isForwardOrReply = [...REPLY_PATTERNS, ...FORWARD_PATTERNS].some((pattern) => {
          return textContent?.match(pattern);
        });
        if (isForwardOrReply) {
          detectedElements.push(element);
        }
      }
      return detectedElements;
    }
  },
];
