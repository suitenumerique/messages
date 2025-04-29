import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DomPurify from "dompurify";
import clsx from "clsx";

type MessageBodyProps = {
    rawHtmlBody: string;
    rawTextBody: string;
}

const CSP = [
    // Allow images from our domain and data URIs
    "img-src 'self' data: http://localhost:3001",
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
    const [isLoaded, setIsLoaded] = useState(false);

    const sanitizedHtmlBody = useMemo(() => {
        return DomPurify.sanitize(rawHtmlBody || rawTextBody, {
            FORBID_TAGS: ['script', 'object', 'iframe', 'embed', 'audio', 'video'],
        });
    }, []);

    const wrappedHtml = useMemo(() => {
        return `
            <html>
            <head>
                <meta http-equiv="Content-Security-Policy" content="${CSP}">
                <base target="_blank">
                <style>
                body {
                    margin: 0;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                    font-size: 14px;
                    line-height: 1.5;
                    color: #24292e;
                    overflow-y: hidden;
                    padding-bottom: 1rem;
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
                </style>
            </head>
            <body onload="window.parent.postMessage(document.body.scrollHeight, '*')">
                ${sanitizedHtmlBody}
            </body>
            </html>
      `;
    }, [sanitizedHtmlBody]);

    const resizeIframe = useCallback(() => {
        if (!isLoaded) setIsLoaded(true);
        if (iframeRef.current?.contentWindow) {
          const height = iframeRef.current.contentWindow.document.body.getBoundingClientRect().height;
          iframeRef.current.style.height = `${height}px`;
        }
      }, [iframeRef]);

    useEffect(() => {
        window.addEventListener('resize', resizeIframe);
        return () => window.removeEventListener('resize', resizeIframe);
    }, []);

    return (
        <iframe
            ref={iframeRef}
            className={clsx(
                'thread-message__body',
                {
                    'thread-message__body--loaded': isLoaded,
                }
            )}
            srcDoc={wrappedHtml}
            sandbox="allow-same-origin allow-popups"
            onLoad={resizeIframe}

        />
    )
}

export default MessageBody;