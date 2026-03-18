import clsx from "clsx";
import React from "react";

const URL_REGEX = /(https?:\/\/[^\s<>"{}|\\^`[\]]+)/;
const URL_TEST_REGEX = /^https?:\/\//;
const TRAILING_PUNCTUATION_REGEX = /[.,;:!?)]+$/;
const MENTION_REGEX = /(@\[[^\]]+\])/g;

/**
 * Helper class for text operations.
 * Methods operate on ReactNode[] so they can be chained:
 *   TextHelper.renderLinks(TextHelper.renderMentions(content, name))
 */
export class TextHelper {
    /**
     * Build a regex that matches @name with proper word boundaries.
     * Requires @ to be preceded by start of string or whitespace (prevents matching emails like foo@bar).
     * Requires the name to be followed by a non-word character (prevents partial matches like @John in @Johnny).
     */
    static buildMentionPattern(name: string, flags: string = "u"): RegExp {
        const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        return new RegExp(`(?<=^|\\s)@${escaped}(?![\\p{L}\\p{N}_])`, flags);
    }

    /**
     * Split @[mentions] into highlighted spans.
     * Mentions matching highlight get an extra CSS modifier.
     * It is possible to prefix the class name with a base class name.
     */
    static renderMentions(content: string, highlight?: string | undefined, options?: { baseClassName?: string }): React.ReactNode[] {
        const parts = content.split(MENTION_REGEX);
        const className = options?.baseClassName ? `${options.baseClassName}__mention` : "mention";
        return parts.map((part, i) => {
            if (part.startsWith("@[") && part.endsWith("]")) {
                const name = part.slice(2, -1);
                const isHighlighted = highlight === name;
                return (
                    <span
                        key={`mention-${i}`}
                        className={clsx(
                            className,
                            { [`${className}--highlight`]: isHighlighted }
                        )}>
                        @{name}
                    </span>
                );
            }
            return part;
        });
    }

    /**
     * Convert URLs to clickable links within an existing ReactNode[].
     * Only string nodes are processed; JSX elements are passed through.
     * Strips trailing punctuation from matched URLs.
     * Optionnaly pass additional props to the <a> element.
     */
    static renderLinks(nodes: React.ReactNode[], options?: { props: React.ComponentProps<"a"> }): React.ReactNode[] {
        return nodes.flatMap((node, i) => {
            if (typeof node !== "string") return node;

            const parts = node.split(URL_REGEX);
            if (parts.length === 1) return node;

            return parts.map((part, j) => {
                if (URL_TEST_REGEX.test(part)) {
                    const trailingMatch = part.match(TRAILING_PUNCTUATION_REGEX);
                    let url = trailingMatch ? part.slice(0, -trailingMatch[0].length) : part;
                    let trailing = trailingMatch ? trailingMatch[0] : "";

                    // Preserve balanced parentheses in URLs (e.g. Wikipedia links)
                    while (trailing[0] === ")" && url.split("(").length > url.split(")").length) {
                        url += ")";
                        trailing = trailing.slice(1);
                    }

                    return (
                        <React.Fragment key={`l-${i}-${j}`}>
                            <a
                                {...(options?.props ?? {})}
                                href={url}
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                {url}
                            </a>
                            {trailing}
                        </React.Fragment>
                    );
                }
                return part;
            });
        });
    }
}

export default TextHelper;
