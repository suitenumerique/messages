import { useCallback } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { useTranslation } from "react-i18next";
import { Message } from "@/features/api/gen/models";

const formatContact = (c: { name?: string | null; email: string }) =>
    c.name ? `${c.name} <${c.email}>` : c.email;

/**
 * Hook to print a message in a new browser window
 */
const usePrint = () => {
    const { t, i18n } = useTranslation();

    const print = useCallback((message: Message) => {
        const iframe = document
            .querySelector(`#thread-message-${message.id} .thread-message__body`) as HTMLIFrameElement | null;
        const bodyHtml = iframe?.contentDocument?.body?.innerHTML;
        if (!bodyHtml) return;

        const date = new Date(message.sent_at ?? message.created_at);
        const formattedDate = date.toLocaleString(i18n.resolvedLanguage, {
            dateStyle: 'full',
            timeStyle: 'short',
        });

        const headers = [
            { label: t('From'), value: formatContact(message.sender) },
            { label: t('To'), value: message.to.map((r) => formatContact(r.contact)).join(', ') },
            ...(message.cc.length ? [{ label: t('Cc'), value: message.cc.map((r) => formatContact(r.contact)).join(', ') }] : []),
            ...(message.bcc.length ? [{ label: t('Bcc'), value: message.bcc.map((r) => formatContact(r.contact)).join(', ') }] : []),
            { label: t('Date'), value: formattedDate },
            { label: t('Subject'), value: message.subject ?? '' },
        ];

        const html = '<!DOCTYPE html>' + renderToStaticMarkup(
            <html>
                <head>
                    <meta charSet="utf-8" />
                    <title>{message.subject ?? ''}</title>
                    <style>{`
                        body { font-family: system-ui, -apple-system, sans-serif; margin: 2em; color: #000; }
                        table { font-size: 14px; border-collapse: collapse; }
                        hr { border: none; border-top: 1px solid #ccc; margin: 16px 0; }
                        .body { font-size: 14px; }
                        .body img { max-width: 100%; }
                        @media print { body { margin: 0; } }
                    `}</style>
                </head>
                <body>
                    <table>
                        {headers.map((h, i) => (
                            <tr key={i}>
                                <td style={{ fontWeight: 'bold', padding: '2px 12px 2px 0', whiteSpace: 'nowrap', verticalAlign: 'top' }}>{h.label}</td>
                                <td style={{ padding: '2px 0' }}>{h.value}</td>
                            </tr>
                        ))}
                    </table>
                    <hr />
                    <div className="body" dangerouslySetInnerHTML={{ __html: bodyHtml }} />
                </body>
            </html>
        );

        const printWindow = window.open('');
        if (!printWindow) return;

        printWindow.document.write(html);
        printWindow.document.close();
        printWindow.addEventListener('afterprint', () => printWindow.close());
        printWindow.onload = () => printWindow.print();
    }, [t, i18n.resolvedLanguage]);

    return { print };
};

export default usePrint;
