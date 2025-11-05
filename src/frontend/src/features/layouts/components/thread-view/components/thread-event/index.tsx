import { useTranslation } from "react-i18next";

type ThreadEventProps = {
    type: string;
    channel?: string;
    data: Record<string, unknown>;
    createdAt: string;
};

type IframeData = {
    src?: string;
    width?: string | number;
    height?: string | number;
    title?: string;
    sandbox?: string;
    allow?: string;
    [key: string]: unknown;
};

export const ThreadEvent = ({ type, channel, data, createdAt }: ThreadEventProps) => {
    const { t } = useTranslation();

    // For iframe type, render only the iframe with border
    if (type === "iframe") {
        const iframeData = data as IframeData;
        const src = iframeData.src;
        
        if (!src || typeof src !== "string") {
            // Error message if src is missing or invalid - fallback to default display
            return (
                <div className="thread-event">
                    <p className="thread-event__error">
                        {t("This event contains an invalid iframe.")}
                    </p>
                </div>
            );
        }

        return (
            <div className="thread-event__iframe-container">
                <iframe
                    className="thread-event__iframe"
                    src={src}
                    width={iframeData.width || "100%"}
                    height={iframeData.height || "400px"}
                    title={iframeData.title || "Embedded content"}
                    sandbox={iframeData.sandbox}
                    allow={iframeData.allow}
                />
            </div>
        );
    }

    // Default fallback: render as JSON with header
    return (
        <div className="thread-event">
            <div className="thread-event__header">
                <span className="thread-event__type">{type}</span>
                {channel && (
                    <span className="thread-event__channel">{channel}</span>
                )}
                <span className="thread-event__date">
                    {new Date(createdAt).toLocaleString()}
                </span>
            </div>
            <div className="thread-event__content">
                <pre className="thread-event__json">
                    {JSON.stringify(data, null, 2)}
                </pre>
            </div>
        </div>
    );
};

