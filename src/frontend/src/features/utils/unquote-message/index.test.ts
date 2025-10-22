import { UnquoteMessage } from "./index";

describe("UnquoteMessage", () => {
  describe("HTML - Handler-based quote removal", () => {
    it("should handle multiple quote markers", () => {
      const html = `
        <div>New message</div>
        <div class="gmail_quote">
            <blockquote class="gmail_quote">
                First quote
            </blockquote>
        </div>
        <div class="protonmail_quote">Second quote</div>
      `;

      const unquote = new UnquoteMessage(html);
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"<div>New message</div>"`);
    });

    describe("Handlers - basics", () => {
      it("should remove Messages quotes", () => {
        const html = `
                <div>New message content</div>
                <blockquote data-type="quote-separator">
                This is a quoted message
                </blockquote>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Freshdesk quotes", () => {
        const html = `
                <div>New message content</div>
                <div class="freshdesk_quote">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Front quotes", () => {
        const html = `
                <div>New message content</div>
                <div class="front-blockquote">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Spark quotes", () => {
        const html = `
                <div>New message content</div>
                <div name="messageReplySection">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Notion quotes", () => {
        const html = `
                <div>New message content</div>
                <blockquote class="notion-mail-quote">This is a quoted message</blockquote>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove QT quotes", () => {
        const html = `
                <div>New message content</div>
                <blockquote type="cite" id="qt">This is a quoted message</blockquote>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Yahoo quotes", () => {
        const html = `
                <div>New message content</div>
                <div class="yahoo_quoted">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Yahoo ydp quotes", () => {
        const html = `
                <div>New message content</div>
                <div class="ydp-yahoo_quoted" id="yahoo_quoted">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove GetFernand quotes", () => {
        const html = `
                <div>New message content</div>
                <div class="fernand_quote">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Intercom quotes", () => {
        const html = `
                <div>New message content</div>
                <div class="history">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Microsoft Office quotes", () => {
        const html = `
                <div>New message content</div>
                <div id="mail-editor-reference-message-container">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove ProtonMail quotes", () => {
        const html = `
                <div>New message content</div>
                <div class="protonmail_quote">This is a quoted message</div>
              `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });

      it("should remove Trix quotes", () => {
        const html = `
            <div>New message content</div>
            <div class="trix-content">
              <blockquote>
                This is a quoted message
              </blockquote>
            </div>
          `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message content</div>"`
        );
      });
    });

    describe("Handlers - custom detect", () => {
      it("should remove Generic Blockquote Cite (div:has(> blockquote[type=cite]) + blockquote[type=cite])", () => {
        const html = `
                <div>My reply here</div>
                <div>
                    <blockquote type="cite">On 01/15/2024 10:00 AM, sender@example.com wrote:</blockquote>
                </div>
                <blockquote type="cite">
                    <div>Original message from Thunderbird</div>
                </blockquote>
            `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>My reply here</div>"`
        );
      });

      it("should remove reply-intro quotation", () => {
        const html = `
            <div>My reply here</div>
            <p id="some-id-reply-intro">On 01/15/2024 10:00 AM, sender@example.com wrote:</p>
            <blockquote type="cite">
                <div>Original message from Thunderbird</div>
            </blockquote>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>My reply here</div>"`
        );
      });

      it("should remove Blockquote Cite starting with reply or forward pattern", () => {
        const html = `
          <div>My reply here</div>
          <blockquote type="cite">
            <div>On 01/15/2024 10:00 AM, sender@example.com wrote:</div>
            <div>Original message from unknown mail client</div>
          </blockquote>
        `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>My reply here</div>"`
        );
      });

      it("should remove Mozilla Thunderbird quotes (moz-cite-prefix + blockquote)", () => {
        const html = `
          <div>My reply here</div>
          <div class="moz-cite-prefix">On 01/15/2024 10:00 AM, sender@example.com wrote:</div>
          <blockquote type="cite">
            <div>Original message from Thunderbird</div>
          </blockquote>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>My reply here</div>"`
        );
      });

      it("should remove Gmail quotes with gmail_attr", () => {
        const html = `
          <div>My response</div>
          <div class="gmail_quote_container gmail_quote">
            <div class="gmail_attr">On Mon, Jan 15, 2024, John Doe wrote:</div>
            <div>Quoted content</div>
          </div>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>My response</div>"`
        );
      });

      it("should remove Gmail quotes with blockquote inside gmail_quote div", () => {
        const html = `
          <div>New message</div>
          <div class="gmail_quote">
            <blockquote class="gmail_quote">
              Original Gmail message
            </blockquote>
          </div>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message</div>"`
        );
      });

      it("should remove Ymail signature and all following content", () => {
        const html = `
          <div>My message</div>
          <div class="ymail_android_signature">Sent from Yahoo Mail</div>
          <div>Quote after signature</div>
          <div>More content after</div>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(`"<div>My message</div>"`);
      });

      it("should remove Microsoft Outlook Web quotes with MSO styles", () => {
        const html = `
          <div>My reply</div>
          <div style="border:none;border-top:solid #E1E1E1 1.0mm;padding:3.0cm 0pt 0pt 0pt">
            <p class="MsoNormal">
              <b>From:</b> sender@example.com<br>
            </p>
            <div>Original Outlook message</div>
          </div>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(`"<div>My reply</div>"`);
      });

      it("should remove Microsoft Outlook Desktop quotes with divRplyFwdMsg", () => {
        const html = `
          <div>My reply to the message</div>
          <hr>
          <div id="divRplyFwdMsg">
            <b>From:</b> sender@example.com<br>
            <b>Sent:</b> Monday, January 15, 2024<br>
            <b>To:</b> recipient@example.com<br>
          </div>
          <div>Original message body</div>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>My reply to the message</div>"`
        );
      });

      it("should remove ZMail quotes with zmail_extra", () => {
        const html = `
          <div>New message</div>
          <div class="zmail_extra_hr">---</div>
          <div class="zmail_extra">
            Original ZMail message
          </div>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>New message</div>"`
        );
      });

      it("should remove Zendesk quotes (quotedReply)", () => {
        const html = `
          <div>My support response</div>
          <div class="quotedReply">
            <blockquote>
              Customer's original message
            </blockquote>
          </div>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(
          `"<div>My support response</div>"`
        );
      });

      it("should remove Zoho quotes and all following content", () => {
        const html = `
          <div>My message</div>
          <div>---</div>
          <div title="beforequote:::">Quote marker</div>
          <div>Quoted content 1</div>
          <div>Quoted content 2</div>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(`"<div>My message</div>"`);
      });

      it("should remove Alimail quotes (alimail-quote inside blockquote)", () => {
        const html = `
          <div>New message</div>
          <blockquote>
            <div class="alimail-quote">
              Alimail quoted content
            </div>
          </blockquote>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
          expect(result.content).toMatchInlineSnapshot(`"<div>New message</div>"`);
      });

      it("should remove Apple Mail quotes", () => {
        const html = `
          <html class="apple-mail-modern">
            <body>
              <div>My reply</div>
              <blockquote type="cite">
                <div dir="auto">
                  Original Apple Mail message
                </div>
              </blockquote>
            </body>
          </html>
        `;

        const result = new UnquoteMessage(html).getHtml();

        expect(result.hadQuotes).toBe(true);
        expect(result.detectionMethod).toBe("handlers");
        expect(result.content).toMatchInlineSnapshot(`"<div>My reply</div>"`);
      });

      it("should not remove Thunderbird quotes if blockquote is not adjacent", () => {
        const html = `
          <div>My reply</div>
          <div class="moz-cite-prefix">On 01/15/2024 wrote:</div>
          <div>Some other content</div>
          <blockquote type="cite">Not adjacent quote</blockquote>
        `;

        const result = new UnquoteMessage(html).getHtml();

        // Should not detect as Thunderbird quote since blockquote is not adjacent
        expect(result.content).toMatchInlineSnapshot(`
          "<div>My reply</div>
                    <div class="moz-cite-prefix">On 01/15/2024 wrote:</div>
                    <div>Some other content</div>
                    <blockquote type="cite">Not adjacent quote</blockquote>"
        `);
      });

      it("should not remove Yahoo quotes without yahoo_quoted in id", () => {
        const html = `
          <div>My reply</div>
          <div class="ydp-yahoo_quoted" id="some_other_id">
            Should not be removed
          </div>
        `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.content).toMatchInlineSnapshot(`
          "<div>My reply</div>
                    <div class="ydp-yahoo_quoted" id="some_other_id">
                      Should not be removed
                    </div>"
        `);
      });

      it("should not remove Alimail quotes if not inside blockquote", () => {
        const html = `
          <div>My message</div>
          <div class="alimail-quote">
            This should not be removed (not inside blockquote)
          </div>
        `;

        const result = new UnquoteMessage(html).getHtml();
        expect(result.content).toMatchInlineSnapshot(`
          "<div>My message</div>
                    <div class="alimail-quote">
                      This should not be removed (not inside blockquote)
                    </div>"
        `);
      });
    });
  });

  describe("Plain text quote removal", () => {
    it("should remove content with '>' prefix", () => {
      const text = `
New message content

> This is a quoted line
> Another quoted line
      `.trim();

      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"New message content"`);
    });

    it("should detect English reply pattern 'On DATE, wrote:'", () => {
      const text = `
This is my reply.

On 2024-01-15, john@example.com wrote:
> Original message here
      `.trim();

      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"This is my reply."`);
    });

    it("should detect French reply pattern 'Le DATE, a écrit:'", () => {
      const text = `
Ma réponse.

Le 15 janvier 2024, jean@example.com a écrit:
Message original
      `.trim();

      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"Ma réponse."`);
    });

    it("should detect German reply pattern 'Am DATE schrieb'", () => {
      const text = `
Meine Antwort.

Am 15.01.2024 schrieb Hans <hans@example.com>:
Ursprüngliche Nachricht
      `.trim();

      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"Meine Antwort."`);
    });

    it("should handle From:/Sent:/To: headers", () => {
      const text = `
My reply here

From: sender@example.com
Sent: Monday, January 15, 2024
To: recipient@example.com
Subject: Re: Test

Original message
      `.trim();

      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"My reply here"`);
    });

    it("should handle dashed separators", () => {
      const text = `
New content

-----
Old content
      `.trim();

      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"New content"`);
    });

    it("should handle underscore separators", () => {
      const text = `
New content

_____
Old content
      `.trim();

      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"New content"`);
    });
  });

  describe("Edge cases", () => {
    it("should handle empty HTML content", () => {
      const unquote = new UnquoteMessage("", "");
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(false);
      expect(result.detectionMethod).toBe(null);
      expect(result.content).toBe("");
    });

    it("should handle empty text content", () => {
      const unquote = new UnquoteMessage("", "");
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(false);
      expect(result.content).toBe("");
    });

    it("should handle HTML with no quotes", () => {
      const html = "<div>Just a simple message</div>";
      const unquote = new UnquoteMessage(html);
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(false);
      expect(result.content).toBe(html);
    });

    it("should handle text with no quotes", () => {
      const text = "Just a simple message";
      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(false);
      expect(result.content).toBe(text);
    });

    it("should cleanup empty elements after quote removal", () => {
      const html = `
        <div>Content</div>
        <div>
            <div class="gmail_quote">
                <blockquote class="gmail_quote">Quote</blockquote>
            </div>
        </div>
        <div></div>
        <p></p>
      `;

      const unquote = new UnquoteMessage(html, "", { mode: "remove" });
      const result = unquote.getHtml();
      expect(result.content).toMatchInlineSnapshot(`"<div>Content</div>"`);
    });

    it("should preserve self-closing tags", () => {
      const html = `
        <div>Content<br>New line</div>
        <div class="gmail_quote">
          <blockquote class="gmail_quote">Quote</blockquote>
        </div>
      `;

      const unquote = new UnquoteMessage(html);
      const result = unquote.getHtml();

      expect(result.content).toMatchInlineSnapshot(`"<div>Content<br>New line</div>"`);
    });

    it("should handle whitespace-only content", () => {
      const unquote = new UnquoteMessage("   \n\t  ", "  \n  ");
      const htmlResult = unquote.getHtml();
      const textResult = unquote.getText();

      expect(htmlResult.hadQuotes).toBe(false);
      expect(textResult.hadQuotes).toBe(false);
    });
  });

  describe("Options - HTML - Wrap mode", () => {
    it("should wrap caught quotes in details element", () => {
      const html = `
        <div>New message</div>
        <div class="gmail_quote">
          <blockquote class="gmail_quote">Quoted content</blockquote>
        </div>
      `;

      const unquote = new UnquoteMessage(html, "", { mode: "wrap" });
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
      const domContent = new DOMParser().parseFromString(
        result.content,
        "text/html"
      );
      const details = domContent.querySelector("details.email-quoted-content");
      expect(details).not.toBeNull();
      expect(details!.innerHTML).toMatchInlineSnapshot(`
        "<summary class="email-quoted-summary" data-content="Show embedded message"><span>…</span></summary><div class="gmail_quote">
                  <blockquote class="gmail_quote">Quoted content</blockquote>
                </div>"
      `);
    });
  });

  describe("Options - depth", () => {
    it("should respect depth=0 option (only top-level quotes)", () => {
      const html = `
        <div>New message</div>
        <blockquote data-type="quote-separator">
          First level quote
          <blockquote data-type="quote-separator">
            Nested quote (should not be processed)
          </blockquote>
        </blockquote>
      `;

      const unquote = new UnquoteMessage(html, "", { depth: 0, mode: "wrap" });
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
      const domContent = new DOMParser().parseFromString(
        result.content,
        "text/html"
      );
      expect(
        domContent.querySelectorAll("details.email-quoted-content").length
      ).toBe(1);
    });

    it("should respect depth=1 option", () => {
      const html = `
        <div>New message</div>
        <blockquote data-type="quote-separator">
          First level quote
          <blockquote data-type="quote-separator">
            Nested quote
            <blockquote data-type="quote-separator">
                Deep Nested quote (should not be processed)
            </blockquote>
          </blockquote>
        </blockquote>
      `;

      const unquote = new UnquoteMessage(html, "", { depth: 1, mode: "wrap" });
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
      const domContent = new DOMParser().parseFromString(
        result.content,
        "text/html"
      );
      expect(
        domContent.querySelectorAll("details.email-quoted-content").length
      ).toBe(2);
    });

    it("should handle infinite depth by default", () => {
      const html = `
        <div>New message</div>
        <blockquote data-type="quote-separator">
          First level quote
          <blockquote data-type="quote-separator">
            Nested quote (should not be processed)
            <blockquote data-type="quote-separator">
                Deep Nested quote
            </blockquote>
          </blockquote>
        </blockquote>
      `;

      const unquote = new UnquoteMessage(html, "", { mode: "wrap" });
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
      const domContent = new DOMParser().parseFromString(
        result.content,
        "text/html"
      );
      expect(
        domContent.querySelectorAll("details.email-quoted-content").length
      ).toBe(3);
    });
  });

  describe("Options - ignoreFirstForward", () => {
    it("should preserve first forward when ignoreFirstForward is true", () => {
      const html = `
        <div>New message</div>
        <blockquote data-type="quote-separator">
          Forwarded Message: This should be preserved
        </blockquote>
      `;

      const unquote = new UnquoteMessage(html, "", {
        ignoreFirstForward: true,
      });
      const result = unquote.getHtml();
      expect(result.content).toMatchInlineSnapshot(`
        "<div>New message</div>
                <blockquote data-type="quote-separator">
                  Forwarded Message: This should be preserved
                </blockquote>"
      `);
    });

    it("should remove forwards when ignoreFirstForward is false", () => {
      const html = `
        <div>New message</div>
        <blockquote data-type="quote-separator">
          Forwarded Message: This should be removed
        </blockquote>
      `;

      const unquote = new UnquoteMessage(html, "", {
        ignoreFirstForward: false,
      });
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"<div>New message</div>"`);
    });
  });

  describe("Static helper methods", () => {
    it("should provide static unquoteHtml method", () => {
      const html = `
        <div>New message</div>
        <blockquote data-type="quote-separator">Old quote</blockquote>
      `;

      const result = UnquoteMessage.unquoteHtml(html);

        expect(result).toMatchInlineSnapshot(`"<div>New message</div>"`);
    });

    it("should provide static unquoteText method", () => {
      const text = `
New message

> Old quoted text
      `.trim();

      const result = UnquoteMessage.unquoteText(text);

      expect(result).toMatchInlineSnapshot(`"New message"`);
    });

    it("should accept options in static methods", () => {
      const html = `
        <div>New message</div>
        <blockquote data-type="quote-separator">Quote</blockquote>
      `;

      const result = UnquoteMessage.unquoteHtml(html, { mode: "wrap" });

      expect(result).toMatchInlineSnapshot(`
        "<div>New message</div>
                <details class="email-quoted-content"><summary class="email-quoted-summary" data-content="Show embedded message"><span>…</span></summary><blockquote data-type="quote-separator">Quote</blockquote></details>"
      `);
    });
  });

  describe("Complex real-world scenarios", () => {
    it("should handle Gmail-style reply with multiple elements", () => {
      const html = `
        <div dir="ltr">This is my reply</div>
        <div class="gmail_quote">
          <div dir="ltr" class="gmail_attr">
            On Mon, Jan 15, 2024 at 10:00 AM John Doe &lt;john@example.com&gt; wrote:
          </div>
          <blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex">
            <div dir="ltr">Original message content</div>
          </blockquote>
        </div>
      `;

      const unquote = new UnquoteMessage(html);
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
        expect(result.content).toMatchInlineSnapshot(`"<div dir="ltr">This is my reply</div>"`);
    });

    it("should handle Outlook-style reply with hr separator", () => {
      const html = `
        <div>My reply to your message.</div>
        <hr style="display:inline-block;width:98%">
        <div id="divRplyFwdMsg" dir="ltr">
          <b>From:</b> sender@example.com<br>
          <b>Sent:</b> Monday, January 15, 2024 10:00 AM<br>
          <b>Subject:</b> Re: Discussion
        </div>
        <div>Original message body</div>
      `;

      const unquote = new UnquoteMessage(html);
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"<div>My reply to your message.</div>"`);
    });

    it("should handle mixed quote types", () => {
      const html = `
        <div>New content</div>
        <blockquote data-type="quote-separator">
            First quote
        </blockquote>
        <div class="gmail_quote">
            <div dir="ltr" class="gmail_attr">
                On Mon, Jan 15, 2024 at 10:00 AM John Doe &lt;john@example.com&gt; wrote:
            </div>
            <blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex">
                <div dir="ltr">Original message content</div>
            </blockquote>
        </div>
      `;

      const unquote = new UnquoteMessage(html);
      const result = unquote.getHtml();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"<div>New content</div>"`);
    });

    it("should handle plain text with multiple quote markers", () => {
      const text = `
My response here.

On 2024-01-15, alice@example.com wrote:
> Her message
>
> On 2024-01-14, bob@example.com wrote:
> > His message
      `.trim();

      const unquote = new UnquoteMessage("", text);
      const result = unquote.getText();

      expect(result.hadQuotes).toBe(true);
      expect(result.content).toMatchInlineSnapshot(`"My response here."`);
    });
  });
});
