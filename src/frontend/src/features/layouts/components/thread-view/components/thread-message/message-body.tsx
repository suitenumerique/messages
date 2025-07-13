import { useCallback, useEffect, useMemo, useRef } from "react";
import DomPurify from "dompurify";
import { useTranslation } from "react-i18next";

type MessageBodyProps = {
    rawHtmlBody: string;
    rawTextBody: string;
}

const CSP = [
    // Allow images from our domain and data URIs
    "img-src 'self' data: http://localhost:8900",
    // Disable everything else by default
    "default-src 'none'",
    // No scripts at all
    "script-src 'none'",
    // No styles from external sources
    "style-src 'unsafe-inline'",
    // No fonts
    "font-src 'none'",
    // No connections
    "connect-src 'none'",
    // No media
    "media-src 'none'",
    // No objects/embeds
    "object-src 'none'",
    // No prefetch
    "prefetch-src 'none'",
    // No frames
    "child-src 'none'",
    "frame-src 'none'",
    // No workers
    "worker-src 'none'",
    // No frame ancestors
    "frame-ancestors 'none'",
  ].join('; ');

const MessageBody = ({ rawHtmlBody, rawTextBody }: MessageBodyProps) => {
    const iframeRef = useRef<HTMLIFrameElement>(null);
    const { t } = useTranslation();

    DomPurify.addHook(
        'afterSanitizeAttributes',
        function (node) {
            // Allow anchor tags to be opened in the parent window if the href is an anchor
            // Other links are opened in a new tab and safe rel attributes is set
            if(node.tagName === 'A') {
                if (!node.getAttribute('href')?.startsWith('#')) {
                    node.setAttribute('target', '_blank');
                }
                node.setAttribute('rel', 'noopener noreferrer');
            }
        }
    );

    const sanitizedHtmlBody = useMemo(() => {
        return DomPurify.sanitize(rawHtmlBody || rawTextBody, {
            FORBID_TAGS: ['script', 'object', 'iframe', 'embed', 'audio', 'video'],
            ADD_ATTR: ['target', 'rel'],
        });
    }, []);

    const wrappedHtml = useMemo(() => {
        return `
            <html>
            <head>
                <meta http-equiv="Content-Security-Policy" content="${CSP}">
                <base target="_blank">
                <style>
                html, body {
                    margin: 0;
                    padding: 0;
                }
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                    font-size: 14px;
                    color: #24292e;
                }
                img { max-width: 100%; height: auto; }
                a { color: #0366d6; text-decoration: none; }
                a:hover { text-decoration: underline; }
                blockquote {
                    margin: 0 0 1em;
                    padding: 0 1em;
                    color: #6a737d;
                    border-left: 0.25em solid #dfe2e5;
                }
                pre {
                    background-color: #f6f8fa;
                    border-radius: 3px;
                    padding: 16px;
                    overflow: auto;
                }
                code {
                    font-family: SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace;
                    font-size: 85%;
                }
                details > summary {
                    cursor: pointer;
                    user-select: none;
                }
                </style>
            </head>
            <body onload="window.parent.postMessage(document.body.scrollHeight, '*')">
                ${sanitizedHtmlBody}
            </body>
            </html>
      `;
    }, [sanitizedHtmlBody]);

    const resizeIframe = useCallback(() => {
        if (iframeRef.current?.contentWindow) {
          const height = iframeRef.current.contentWindow.document.documentElement.getBoundingClientRect().height;
          iframeRef.current.style.height = `${height}px`;
        }
    }, [iframeRef]);

    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            if (event.data === 'iframe-loaded') {
                // Send a message to the iframe to add event listeners
                iframeRef.current?.contentWindow?.postMessage('add-toggle-listeners', '*');
            } else if (event.data === 'resize') {
                resizeIframe();
            }
        };

        window.addEventListener('message', handleMessage);
        window.addEventListener('resize', resizeIframe);

        return () => {
            window.removeEventListener('message', handleMessage);
            window.removeEventListener('resize', resizeIframe);
        };
    }, [resizeIframe]);

    const handleIframeLoad = useCallback(() => {
        if (iframeRef.current?.contentWindow?.document) {
            const doc = iframeRef.current.contentWindow.document;

            // Replace all <blockquote data-type="quote-separator">...</blockquote> by
            // a details element to automatically fold embedded messages
            // TODO : Try to detect replies and forward messages coming form external sources
            doc.querySelectorAll('blockquote[data-type="quote-separator"]').forEach(node => {
                const parentElement = node.parentElement;
                const details = doc.createElement('details');
                details.addEventListener('toggle', resizeIframe);

                const summary = doc.createElement('summary');
                summary.textContent = t('message_body.show_embedded_message');
                details.appendChild(summary);
                details.appendChild(node);
                parentElement?.appendChild(details);
            });
        }
        resizeIframe();
    }, [resizeIframe]);

    return (
        <iframe
            ref={iframeRef}
            className="thread-message__body"
            srcDoc={wrappedHtml}
            sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation"
            onLoad={handleIframeLoad}
        />
    )
}

export default MessageBody;
