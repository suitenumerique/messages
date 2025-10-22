import { useCallback, useEffect, useMemo, useRef } from "react";
import DomPurify from "dompurify";
import { useTranslation } from "react-i18next";
import { Attachment } from "@/features/api/gen/models";
import { getRequestUrl, getApiOrigin } from "@/features/api/utils";
import { getBlobDownloadRetrieveUrl } from "@/features/api/gen/blob/blob";
import { UnquoteMessage } from '@/features/utils/unquote-message';

type MessageBodyProps = {
    rawHtmlBody?: string;
    rawTextBody?: string;
    attachments?: readonly Attachment[];
}

const CSP = [
    // Allow images from our domain, data URIs, and API endpoints
    `img-src 'self' data: ${getApiOrigin()}`,
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

const MessageBody = ({ rawHtmlBody, rawTextBody = '', attachments = [], isHidden = false, onLoad }: MessageBodyProps) => {
    const iframeRef = useRef<HTMLIFrameElement>(null);

    // Create a mapping of CID to blob URL for CID image transformation
    const cidToBlobUrlMap = useMemo(() => {
        const map = new Map<string, string>();
        attachments.forEach(attachment => {
            if (attachment.cid) {
                const blobUrl = getRequestUrl(getBlobDownloadRetrieveUrl(attachment.blobId));
                map.set(attachment.cid, blobUrl);
            }
        });
        return map;
    }, [attachments]);

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

            // Transform CID references in img src attributes
            if (node.tagName === 'IMG' && cidToBlobUrlMap.size > 0) {
                const src = node.getAttribute('src');
                if (src && src.startsWith('cid:')) {
                    const cid = src.substring(4); // Remove 'cid:' prefix
                    const blobUrl = cidToBlobUrlMap.get(cid);
                    if (blobUrl) {
                        node.setAttribute('src', blobUrl);
                        node.setAttribute('loading', 'lazy');
                    }
                }
            }
        }
    );

    const sanitizedHtmlBody = useMemo(() => {
        const sanitizedContent = DomPurify.sanitize(rawHtmlBody || rawTextBody, {
            FORBID_TAGS: ['script', 'object', 'iframe', 'embed', 'audio', 'video'],
            ADD_ATTR: ['target', 'rel'],
        });

        const unquoteMessage = new UnquoteMessage(sanitizedContent, sanitizedContent, {
            mode: 'wrap',
            ignoreFirstForward: true,
            depth: 0,
        });

        if (rawHtmlBody) return unquoteMessage.getHtml().content;
        return unquoteMessage.getText().content;
    }, [rawHtmlBody, rawTextBody, cidToBlobUrlMap]);

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
                body > *:first-child {
                    margin-top: 0;
                    padding-top: 0 !important;
                }
                *:not(:last-child) {
                    margin-bottom: 1em;
                }
                img { max-width: 100%; height: auto; }
                a { color: #0366d6; text-decoration: none; }
                a:hover { text-decoration: underline; }

                blockquote {
                    padding: 0 1rem !important;
                    margin: 1rem 0 !important;
                    border-left-width: 1px;
                    border-left-style: solid;
                    border-color: #7C7C7C !important;
                }

                blockquote blockquote {
                    border-color: #929292 !important;
                }

                blockquote blockquote blockquote {
                    border-left-width: 2px;
                    border-color: #CECECE !important;
                }

                blockquote blockquote blockquote blockquote {
                    border-color: #E5E5E5 !important;
                }

                blockquote blockquote blockquote blockquote blockquote {
                    border-color: #eee !important;
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

                details.email-quoted-content > summary.email-quoted-summary {
                    background-color: #ECECFE;
                    cursor: pointer;
                    user-select: none;
                    padding: 0.25rem 0.5rem;
                    border-radius: 0.25rem;
                    display: grid;
                    place-items: center;
                    color: #000091;
                    vertical-align: middle;
                    list-style: none;
                    outline: none;
                    width: fit-content;
                    position: relative;
                    margin-top: 1rem;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                }

                details.email-quoted-content:not([open]) > summary.email-quoted-summary::before {
                    content: attr(data-content);
                    position: absolute;
                    left: 110%;
                    top: 50%;
                    width: 100%;
                    height: 100%;
                    background-color: #f0f1f2;
                    border: 1px solid #d2d4d8;
                    box-shadow: 0 1px 5.4px 0 rgba(0, 0, 0, 0.15);
                    width: max-content;
                    transform: translateY(-50%);
                    color: #74777c;
                    padding: 0.3rem 0.6rem;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border-radius: 0.5rem;
                    font-size: 0.75rem;
                    visibility: hidden;
                    opacity: 0;
                    transition-property: visibility, opacity;
                    transition-duration: 150ms;
                    transition-timing-function: cubic-bezier(0.32, 0, 0.67, 0);
                }

                details.email-quoted-content > summary.email-quoted-summary > span {
                    font-size: 18px;
                    font-weight: bold;
                    transform: translateY(-5px);
                    line-height: 1ex;
                }

                details.email-quoted-content > summary.email-quoted-summary:hover {
                    background-color: #cacafb;
                }
                details.email-quoted-content > summary.email-quoted-summary:hover::before {
                    visibility: visible;
                    opacity: 1;
                    transition-timing-function: cubic-bezier(0.65, 0, 0.35, 1);
                    transition-delay: 1000ms;
                }
                details.email-quoted-content > summary.email-quoted-summary::-webkit-details-marker {
                    display: none;
                }
                </style>
            </head>
            <body>
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

            // When details element is toggled, resize the iframe to fit the content
            doc.querySelectorAll('details.email-quoted-content').forEach(node => {
                node.addEventListener('toggle', resizeIframe);
            });
        }

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
