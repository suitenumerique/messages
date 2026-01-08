import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DomPurify from "dompurify";
import { Attachment } from "@/features/api/gen/models";
import { getRequestUrl, getApiOrigin } from "@/features/api/utils";
import { getBlobDownloadRetrieveUrl } from "@/features/api/gen/blob/blob";
import { UnquoteMessage } from '@/features/utils/unquote-message';
import { useTranslation } from "react-i18next";
import { tokens } from '@/styles/cunningham-tokens'
import { useTheme } from "@/features/providers/theme";
import { useConfig } from "@/features/providers/config";
import { useMailboxContext } from "@/features/providers/mailbox";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { Banner } from "@/features/ui/components/banner";
import { getMailboxesImageProxyListUrl } from "@/features/api/gen/mailboxes/mailboxes";
import { EXTERNAL_IMAGES_CONSENT_KEY } from "@/features/config/constants";

type MessageBodyProps = {
    rawHtmlBody?: string;
    rawTextBody?: string;
    attachments?: readonly Attachment[];
    messageId: string;
    isHidden?: boolean;
    onLoad?: () => void;
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

const MessageBody = ({ rawHtmlBody, rawTextBody = '', attachments = [], isHidden = false, messageId, onLoad }: MessageBodyProps) => {
    const { t } = useTranslation();
    const iframeRef = useRef<HTMLIFrameElement>(null);
    const { cunninghamTheme, variant } = useTheme();
    const { selectedMailbox } = useMailboxContext();
    const { IMAGE_PROXY_ENABLED: canDisplayExternalImages } = useConfig();
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

    const domPurify = useMemo(() => {
        const instance = DomPurify();
        instance.addHook(
            'afterSanitizeAttributes',
            function (node: Element) {
                // Allow anchor tags to be opened in the parent window if the href is an anchor
                // Other links are opened in a new tab and safe rel attributes is set

                if (node.tagName === 'A') {
                    if (!node.getAttribute('href')?.startsWith('#')) {
                        node.setAttribute('target', '_blank');
                    }
                    node.setAttribute('rel', 'noopener noreferrer');
                }

                // Remove pixel trackers,
                // Transform CID references in img src attributes
                // and add lazy loading to all images
                if (node.tagName === 'IMG') {
                    const MIN_IMAGE_SIZE = 4;
                    if (node.getAttribute('width') || node.getAttribute('height')) {
                        const width = parseInt(node.getAttribute('width') ?? '0');
                        const height = parseInt(node.getAttribute('height') ?? '0');
                        if (Math.max(width, height) < MIN_IMAGE_SIZE) {
                            node.remove();
                        }
                    }
                    // Add lazy loading to all images for better performance
                    node.setAttribute('loading', 'lazy');

                    // Transform CID references if applicable
                    if (cidToBlobUrlMap.size > 0) {
                        const src = node.getAttribute('src');
                        if (src && src.startsWith('cid:')) {
                            const cid = src.substring(4); // Remove 'cid:' prefix
                            const blobUrl = cidToBlobUrlMap.get(cid);
                            if (blobUrl) {
                                node.setAttribute('src', blobUrl);
                            }
                        }
                        return;
                    }

                    if (node.getAttribute('src')?.startsWith('http')) {
                        hasExternalImagesRef.current = true;
                        if (!canDisplayExternalImages || !displayExternalImages) {
                            node.remove();
                            return;
                        }

                        const src = node.getAttribute('src');
                        if (src) {
                            node.setAttribute(
                                'src',
                                getRequestUrl(getMailboxesImageProxyListUrl(
                                    selectedMailbox!.id,
                                    { url: src },
                                )),
                            );
                        }
                    }
                }
            }
        );
        return instance;
    }, [cidToBlobUrlMap, selectedMailbox, canDisplayExternalImages, displayExternalImages]);

    const sanitizedHtmlBody = useMemo(() => {
        if (rawHtmlBody) {
            // For HTML content, sanitize as HTML
            const sanitizedContent = domPurify.sanitize(rawHtmlBody, {
                FORBID_TAGS: ['script', 'object', 'iframe', 'embed', 'audio', 'video'],
                ADD_ATTR: ['target', 'rel'],
            });

            const unquoteMessage = new UnquoteMessage(sanitizedContent, '', {
                mode: 'wrap',
                ignoreFirstForward: true,
                depth: 0,
            });

            return unquoteMessage.getHtml().content;
        } else {
            // For plain text, process with UnquoteMessage first (preserving newlines)
            const unquoteMessage = new UnquoteMessage('', rawTextBody, {
                mode: 'wrap',
                ignoreFirstForward: true,
                depth: 0,
            });

            const unquotedText = unquoteMessage.getText().content;

            // Use browser-native HTML escaping for security
            // textContent automatically escapes all HTML entities correctly
            const tempDiv = document.createElement('div');
            tempDiv.textContent = unquotedText;
            const escapedText = tempDiv.innerHTML;

            // Wrap in a div with class for CSS styling
            return `<div class="text-plain-content">${escapedText}</div>`;
        }
    }, [rawHtmlBody, rawTextBody, domPurify]);

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
                body > *:first-child {
                    padding-top: 0 !important;
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
                    max-width: 100vw;
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
            <body data-theme-variant="${variant}">
                ${sanitizedHtmlBody}
            </body>
            </html>
      `;
    }, [sanitizedHtmlBody, cunninghamTheme]);

    const resizeIframe = useCallback(() => {
        if (iframeRef.current?.contentWindow) {
            const height = iframeRef.current.contentWindow.document.documentElement.getBoundingClientRect().height;
            iframeRef.current.style.height = `${height}px`;
        }
    }, [iframeRef]);

    const handleIframeLoad = useCallback(() => {
        resizeIframe();
        if (iframeRef.current?.contentWindow?.document) {
            const doc = iframeRef.current.contentWindow.document;

            // When details element is toggled, resize the iframe to fit the content
            doc.querySelectorAll('details.email-quoted-content').forEach(node => {
                node.addEventListener('toggle', resizeIframe);
            });
        }
        onLoad?.();
    }, [onLoad, resizeIframe]);

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

    useEffect(() => {
        if (!isHidden) {
            resizeIframe();
        }
    }, [isHidden, showExternalImages]);

    return (
        <>
            {canDisplayExternalImages && hasExternalImagesRef.current && !displayExternalImages &&
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

export default MessageBody;
