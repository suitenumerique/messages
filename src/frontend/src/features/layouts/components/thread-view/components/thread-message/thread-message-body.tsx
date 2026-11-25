import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { getRequestUrl, getApiOrigin } from "@/features/api/utils";
import { getBlobDownloadRetrieveUrl } from "@/features/api/gen/blob/blob";
import { UnquoteMessage } from '@/features/utils/unquote-message';
import { useTranslation } from "react-i18next";
import { tokens } from '@/styles/cunningham-tokens'
import { useTheme } from "@/features/providers/theme";
import { useConfig } from "@/features/providers/config";
import { useMailboxContext } from "@/features/providers/mailbox";
import { Icon } from "@/features/ui/components/icon";
import { Banner } from "@/features/ui/components/banner";
import { getMailboxesImageProxyListUrl } from "@/features/api/gen/mailboxes/mailboxes";
import { getNativeServerUrl, toNativeMediaUrl } from "@/features/native/media-url";
import { EXTERNAL_IMAGES_CONSENT_KEY } from "@/features/config/constants";
import { handle } from "@/features/utils/errors";
import { renderBodyParts } from "./renderers";
import { linkifyHtml } from "./renderers/linkify";
import { mayHaveViewportHeightLengths, removeViewportHeightLengths } from "./renderers/utils";
import { isHostTrusted } from "./is-host-trusted";
import { useLinkConfirmation } from "./use-link-confirmation";
import { ThreadMessageBodyProps } from "./types";

const CSP = [
    // Allow images from our domain, data URIs, and API endpoints. Inside the
    // native shell, blob and proxied images go through the bridge's HTTP
    // interceptor on the shell origin — which is only 'self' when the page is
    // served from the embedded bundle, not from the Vite dev server.
    `img-src 'self' data: ${getApiOrigin()} ${getNativeServerUrl()}`.trimEnd(),
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

// The iframe takes the height of its root element. An email stylesheet sizing
// the root elements against the viewport (e.g. a quoted template with
// `html, body { height: 100% }`) would make that height depend on the iframe
// height itself, stuck at 0 when the message was folded. Inline !important
// declarations win over any declaration of the email stylesheets.
const ROOT_SIZING_GUARD = "height: auto !important; min-height: 0 !important; max-height: none !important;";

// Content changes that alter the message height after the iframe load event:
// late image loads and failures, quote toggles, form controls revealing content
// (CSS `:checked` tricks) and finished CSS animations. Some of these events do
// not bubble, so they are caught in the capture phase. `transitionend` is left
// out on purpose: a resize of the iframe can itself start a transition (a style
// depending on the iframe height), and following it would loop.
const CONTENT_CHANGE_EVENTS = ['load', 'error', 'toggle', 'change', 'animationend'] as const;

/**
 * Detect link masking: the link text pretends to be a URL but points to a
 * different host than the real target, a common phishing technique
 * (e.g. <a href="http://malicious.com">http://safe.fr</a>).
 */
const isLinkMasked = (href: string, text: string): boolean => {
    const trimmedText = text.trim();
    if (!/^(https?:\/\/|www\.)/i.test(trimmedText)) {
        return false;
    }
    try {
        const textUrl = new URL(/^https?:\/\//i.test(trimmedText) ? trimmedText : `https://${trimmedText}`);
        const hrefUrl = new URL(href);
        return textUrl.host.replace(/^www\./, "") !== hrefUrl.host.replace(/^www\./, "");
    } catch {
        // The text pretends to be a URL but one side cannot be parsed, stay cautious.
        return true;
    }
};

const ThreadMessageBody = ({ bodyParts, attachments = [], isHidden = false, areLinksDisabled = false, messageId, onLoad }: ThreadMessageBodyProps) => {
    const { t } = useTranslation();
    const iframeRef = useRef<HTMLIFrameElement>(null);
    const { cunninghamTheme, variant } = useTheme();
    const { selectedMailbox } = useMailboxContext();
    const confirmLinkOpening = useLinkConfirmation();
    // Links of spam messages are inert. A ref keeps the iframe click handler
    // in sync without having to re-attach listeners when the flag changes.
    const areLinksDisabledRef = useRef(areLinksDisabled);
    areLinksDisabledRef.current = areLinksDisabled;
    const { IMAGE_PROXY_ENABLED: isImageProxyEnabled, MESSAGE_TRUSTED_LINK_DOMAINS: trustedLinkDomains } = useConfig();
    // The image proxy is scoped to a mailbox, without one external images cannot be displayed.
    const canDisplayExternalImages = isImageProxyEnabled && Boolean(selectedMailbox);
    const [displayExternalImages, setDisplayExternalImages] = useState(() => {
        const consentMessageIds = sessionStorage.getItem(EXTERNAL_IMAGES_CONSENT_KEY);
        if (consentMessageIds) {
            return consentMessageIds.split('|').includes(messageId);
        }
        return false;
    });
    const hasExternalImagesRef = useRef(false);
    const showExternalImages = () => {
        const oldConsentMessageIds = new Set(sessionStorage.getItem(EXTERNAL_IMAGES_CONSENT_KEY)?.split('|') ?? []);
        oldConsentMessageIds.add(messageId);
        sessionStorage.setItem(EXTERNAL_IMAGES_CONSENT_KEY, Array.from(oldConsentMessageIds).join('|'));
        setDisplayExternalImages(true);
    };

    // Build CID to blob URL mapping for inline image resolution. The iframe
    // loads these as <img> subresources, which carry no session inside the
    // native shell — hence the rewrite (see toNativeMediaUrl).
    const cidToBlobUrlMap = useMemo(() => {
        const map = new Map<string, string>();
        attachments.forEach(attachment => {
            if (attachment.cid) {
                const blobUrl = toNativeMediaUrl(getRequestUrl(getBlobDownloadRetrieveUrl(attachment.blobId)));
                map.set(attachment.cid, blobUrl);
            }
        });
        return map;
    }, [attachments]);

    // Options for external image handling, passed to renderers
    const externalImageOptions = useMemo(() => ({
        canDisplayExternalImages,
        displayExternalImages,
        onExternalImageDetected: () => { hasExternalImagesRef.current = true; },
        getProxiedUrl: (url: string) => selectedMailbox
            ? toNativeMediaUrl(getRequestUrl(getMailboxesImageProxyListUrl(selectedMailbox.id, { url })))
            : null,
    }), [canDisplayExternalImages, displayExternalImages, selectedMailbox]);

    const sanitizedHtmlBody = useMemo(() => {
        const renderedContent = renderBodyParts(bodyParts, cidToBlobUrlMap, externalImageOptions);

        if (!renderedContent) {
            return "";
        }

        // Check content types to determine processing mode
        const hasHtmlPart = bodyParts.some(part => part.type === "text/html");
        const hasOnlyPlainText = bodyParts.every(part => part.type === "text/plain");

        let htmlContent = renderedContent;

        if (hasHtmlPart) {
            // Process HTML content with UnquoteMessage for quote detection
            const unquoteMessage = new UnquoteMessage(renderedContent, '', {
                mode: 'wrap',
                ignoreFirstForward: true,
                depth: 0,
            });
            htmlContent = unquoteMessage.getHtml().content;
        } else if (hasOnlyPlainText) {
            // Pure plain text - process original content through UnquoteMessage for quote detection
            const rawTextContent = bodyParts.map(part => part.content).join("\n");
            const unquoteMessage = new UnquoteMessage('', rawTextContent, {
                mode: 'wrap',
                ignoreFirstForward: true,
                depth: 0,
            });
            const unquotedText = unquoteMessage.getText().content;

            htmlContent = renderToStaticMarkup(<p className="text-plain-content">{unquotedText}</p>);
        }
        // Otherwise, mixed content (plain text + images, etc.) - use rendered content directly

        // Turn bare URLs (text not already wrapped in a link) into anchors
        return linkifyHtml(htmlContent);
    }, [bodyParts, cidToBlobUrlMap, externalImageOptions]);

    const wrappedHtml = useMemo(() => {
        return `
            <html style="${ROOT_SIZING_GUARD}">
            <head>
                <meta http-equiv="Content-Security-Policy" content="${CSP}">
                <base target="_blank">
                <style>
                html, body {
                    margin: 0;
                    padding: 0;
                }
                body {
                    font-family: ${tokens.themes[cunninghamTheme].globals.font.families.base};
                    padding: ${tokens.themes[cunninghamTheme].globals.spacings.base};
                    padding-top: 0;
                    font-size: ${tokens.themes[cunninghamTheme].globals.font.sizes.sm};
                    color: ${tokens.themes[cunninghamTheme].contextuals.content.semantic.neutral.primary};
                }
                .text-plain-content {
                    white-space: pre-wrap;
                    word-wrap: break-word;
                }
                .body-part + .body-part {
                    margin-top: ${tokens.themes[cunninghamTheme].globals.spacings.base};
                }
                /* Trim the empty space above the message, not its padding:
                   a padding lies within the background of the email layout. */
                body > *:first-child {
                    margin-top: 0 !important;
                }
                body > *:last-child {
                    margin-bottom: ${tokens.themes[cunninghamTheme].globals.spacings.base};
                }
                table, div {
                    max-width: 100%;
                }
                p, ul, ol, li, blockquote, pre, code, h1, h2, h3, h4, h5, h6 {
                    margin: 0;
                }
                p, ul {
                    line-height: 1.3;
                }
                a {
                    color: ${tokens.themes[cunninghamTheme].contextuals.background.semantic.info.primary};
                    text-decoration: none;
                }
                a:hover { text-decoration: underline; }
                ${areLinksDisabled ? `
                /* Spam: links are inert and rendered as plain text */
                a, a:hover {
                    pointer-events: none;
                    color: inherit !important;
                    text-decoration: none !important;
                    cursor: default;
                }` : ''}

                blockquote {
                    padding: 0 ${tokens.themes[cunninghamTheme].globals.spacings.base} !important;
                    margin: ${tokens.themes[cunninghamTheme].globals.spacings.base} 0 !important;
                    border-left-style: solid;
                    border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-500"]} !important;
                }

                blockquote blockquote {
                    border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-400"]} !important;
                }

                blockquote blockquote blockquote {
                    border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-300"]} !important;
                }

                blockquote blockquote blockquote blockquote {
                    border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-200"]} !important;
                }

                blockquote blockquote blockquote blockquote blockquote {
                    border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-100"]} !important;
                }

                body[data-theme-variant="dark"] {
                    blockquote {
                        border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-450"]} !important;
                    }

                    blockquote blockquote {
                        border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-550"]} !important;
                    }

                    blockquote blockquote blockquote {
                        border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-650"]} !important;
                    }

                    blockquote blockquote blockquote blockquote {
                        border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-750"]} !important;
                    }


                    blockquote blockquote blockquote blockquote blockquote {
                        border-color: ${tokens.themes[cunninghamTheme].globals.colors["gray-850"]} !important;
                    }
                }

                img {
                    max-width: 100%;
                }

                pre {
                    background-color: ${tokens.themes[cunninghamTheme].contextuals.background.semantic.overlay.primary};
                    border-radius: 4px;
                    padding: ${tokens.themes[cunninghamTheme].globals.spacings.base};
                    overflow: auto;
                }
                code {
                    font-family: SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace;
                    font-size: 85%;
                }

                details.email-quoted-content > summary.email-quoted-summary {
                    background-color: ${tokens.themes[cunninghamTheme].contextuals.background.semantic.brand.tertiary};
                    cursor: pointer;
                    user-select: none;
                    padding: ${tokens.themes[cunninghamTheme].globals.spacings['3xs']} ${tokens.themes[cunninghamTheme].globals.spacings.xs};
                    border-radius: 4px;
                    display: grid;
                    place-items: center;
                    color: ${tokens.themes[cunninghamTheme].contextuals.content.semantic.brand.primary};
                    vertical-align: middle;
                    list-style: none;
                    outline: none;
                    width: fit-content;
                    position: relative;
                    margin-top: ${tokens.themes[cunninghamTheme].globals.spacings.base};
                    font-family: ${tokens.themes[cunninghamTheme].globals.font.families.base};
                    transition: background-color ${tokens.themes.default.globals.transitions.duration} ${tokens.themes.default.globals.transitions['ease-in-out']};
                }

                details.email-quoted-content:not([open]) > summary.email-quoted-summary::before {
                    content: attr(data-content);
                    position: absolute;
                    left: 110%;
                    top: 50%;
                    width: 100%;
                    height: 100%;
                    background-color: ${tokens.themes[cunninghamTheme].contextuals.background.semantic.neutral.tertiary};
                    border: 1px solid ${tokens.themes[cunninghamTheme].contextuals.border.semantic.neutral.tertiary};
                    box-shadow: 0 1px 5.4px 0 rgba(0, 0, 0, 0.15);
                    width: max-content;
                    transform: translateY(-50%);
                    color: ${tokens.themes[cunninghamTheme].contextuals.content.semantic.neutral.tertiary};
                    padding: ${tokens.themes[cunninghamTheme].globals.spacings['3xs']} ${tokens.themes[cunninghamTheme].globals.spacings.xs};
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border-radius: 0.5rem;
                    font-size: ${tokens.themes[cunninghamTheme].globals.font.sizes.s};
                    visibility: hidden;
                    opacity: 0;
                    transition-property: visibility, opacity;
                    transition-duration: 150ms;
                    transition-timing-function: ${tokens.themes.default.globals.transitions['ease-in']};
                }

                details.email-quoted-content > summary.email-quoted-summary > span {
                    font-size: ${tokens.themes[cunninghamTheme].globals.font.sizes.lg};
                    font-weight: 700;
                    transform: translateY(-5px);
                    line-height: 1ex;
                }

                details.email-quoted-content > summary.email-quoted-summary:hover {
                    background-color: ${tokens.themes[cunninghamTheme].contextuals.background.semantic.brand["tertiary-hover"]};
                }
                details.email-quoted-content > summary.email-quoted-summary:hover::before {
                    visibility: visible;
                    opacity: 1;
                    transition-timing-function: ${tokens.themes.default.globals.transitions['ease-in-out']};
                    transition-delay: 1000ms;
                }
                details.email-quoted-content > summary.email-quoted-summary::-webkit-details-marker {
                    display: none;
                }
                </style>
            </head>
            <body style="${ROOT_SIZING_GUARD}" data-theme-variant="${variant}">
                ${sanitizedHtmlBody}
            </body>
            </html>
      `;
    }, [sanitizedHtmlBody, cunninghamTheme, variant, areLinksDisabled]);

    const resizeObserverRef = useRef<ResizeObserver | null>(null);
    const isIframeLoadedRef = useRef(false);

    const resizeIframe = useCallback(() => {
        if (iframeRef.current?.contentWindow?.document.documentElement) {
            const height = iframeRef.current.contentWindow.document.documentElement.getBoundingClientRect().height;
            // Round up: Safari shows a scrollbar for a sub-pixel overflow.
            iframeRef.current.style.height = `${Math.ceil(height)}px`;
        }
    }, []);

    // Delegated handler for link activations inside the iframe (mouse click,
    // keyboard activation and middle click through the auxclick event).
    const handleLinkActivation = useCallback((event: MouseEvent) => {
        const target = event.target as Element | null;
        const link = target?.closest?.('a[href]') as HTMLAnchorElement | null;
        if (!link) {
            return;
        }

        // In-page anchors are safe and stay within the iframe
        if (link.getAttribute('href')?.startsWith('#')) {
            return;
        }

        if (areLinksDisabledRef.current) {
            event.preventDefault();
            return;
        }

        // Let other protocols (mailto:, tel:, ...) follow their default behavior
        if (!/^https?:/i.test(link.href)) {
            return;
        }

        event.preventDefault();
        // Middle clicks are only blocked, the confirmation flow targets primary clicks
        if (event.type === 'auxclick') {
            return;
        }

        const url = link.href;
        const isMasked = isLinkMasked(url, link.textContent ?? '');
        // Trusted domains skip the confirmation, unless the link is masked: a
        // masked link is exactly the phishing case the confirmation exists for.
        if (!isMasked && isHostTrusted(new URL(url).hostname, trustedLinkDomains)) {
            window.open(url, '_blank', 'noopener,noreferrer');
            return;
        }
        void confirmLinkOpening(url, isMasked).then((confirmed) => {
            if (confirmed) {
                window.open(url, '_blank', 'noopener,noreferrer');
            }
        });
    }, [confirmLinkOpening, trustedLinkDomains]);

    const handleIframeLoad = useCallback(() => {
        try {
            resizeObserverRef.current?.disconnect();
            if (iframeRef.current?.contentWindow?.document) {
                const doc = iframeRef.current.contentWindow.document;
                isIframeLoadedRef.current = true;

                // Intercept link activations to reveal the real target before
                // opening it. Done first, so that no step below can leave the
                // links of a message unchecked.
                doc.addEventListener('click', handleLinkActivation);
                doc.addEventListener('auxclick', handleLinkActivation);

                try {
                    if (mayHaveViewportHeightLengths(sanitizedHtmlBody)) {
                        removeViewportHeightLengths(doc);
                    }
                } catch (error) {
                    // Best effort: a message keeping its viewport sizes only
                    // overflows its iframe, it must still be sized and shown.
                    handle(new Error("Failed to drop the viewport sizes of a message."), { extra: { error } });
                }
                // Sized synchronously so the height is right when onLoad fires.
                resizeIframe();

                // Follow what changes the content height: reflows when the iframe
                // width changes and the content change events. Never the size of
                // the root element itself: an email style may still depend on the
                // iframe viewport (e.g. a `@media (max-height)` query), so every
                // new iframe height could change it again and loop.
                let observedWidth: number | null = null;
                resizeObserverRef.current = new ResizeObserver(([entry]) => {
                    if (entry.contentRect.width === observedWidth) {
                        return;
                    }
                    observedWidth = entry.contentRect.width;
                    // Next frame: resizing the observed iframe within its own
                    // notification makes the browser report a ResizeObserver
                    // loop error.
                    requestAnimationFrame(resizeIframe);
                });
                resizeObserverRef.current.observe(iframeRef.current);
                CONTENT_CHANGE_EVENTS.forEach((type) => doc.addEventListener(type, resizeIframe, true));
            }
        } finally {
            // The message stays behind its loader until onLoad runs: whatever
            // fails above must not keep it hidden. The error still propagates.
            onLoad?.();
        }
    }, [onLoad, resizeIframe, handleLinkActivation, sanitizedHtmlBody]);

    // A message rendered folded was measured with a 0px high iframe viewport: a
    // style depending on the viewport height (e.g. a `@media (orientation)`
    // query) may change its content once it is shown.
    useEffect(() => {
        if (!isHidden && isIframeLoadedRef.current) {
            resizeIframe();
        }
    }, [isHidden, resizeIframe]);

    useEffect(() => () => resizeObserverRef.current?.disconnect(), []);

    return (
        <>
            {!isHidden && canDisplayExternalImages && hasExternalImagesRef.current && !displayExternalImages &&
                <Banner
                    type="neutral"
                    icon={<Icon name="security" />}
                    compact
                    actions={[
                        {
                            label: t("Display those images"),
                            onClick: showExternalImages,
                        }
                    ]}
                >
                    {t("For your security and privacy, external images are not displayed.")}
                </Banner>
            }
            <iframe
                title={t("Message content")}
                style={{
                    maxHeight: isHidden ? '0px' : undefined,
                    visibility: isHidden ? 'hidden' : 'visible',
                    margin: isHidden ? '0' : undefined,
                }}
                ref={iframeRef}
                className="thread-message__body"
                srcDoc={wrappedHtml}
                sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation"
                onLoad={handleIframeLoad}
            />
        </>
    )
}

export default ThreadMessageBody;
