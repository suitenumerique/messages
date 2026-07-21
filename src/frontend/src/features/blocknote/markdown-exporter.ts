import type {
    BlockNoteEditor,
    BlockSchema,
    InlineContentSchema,
    PartialBlock,
    StyleSchema,
} from '@blocknote/core';

/**
 * Serializes BlockNote blocks to plain-text markdown.
 *
 * Thin wrapper over BlockNote's `blocksToMarkdownLossy` that trims surrounding
 * whitespace. Since BlockNote >=0.51 it emits a trailing "\n" even for an empty
 * document; trimming preserves our contract of returning '' for empty content.
 */
export const blocksToMarkdown = async <
    BSchema extends BlockSchema,
    ISchema extends InlineContentSchema,
    SSchema extends StyleSchema,
>(
    editor: BlockNoteEditor<BSchema, ISchema, SSchema>,
    blocks: PartialBlock<BSchema, ISchema, SSchema>[],
): Promise<string> => {
    const markdown = await editor.blocksToMarkdownLossy(blocks);
    return markdown.trim();
};

// A fenced block spans from its opening fence line to the next fence line,
// or to the end of input when unclosed. Its body (captured group) is literal
// code: it must escape every transformation below — only the fence marker
// lines themselves are dropped.
const FENCED_BLOCK_RE =
    /^[ \t]*(?:```|~~~).*(?:\n|$)([\s\S]*?)(?:^[ \t]*(?:```|~~~).*(?:\n|$)|$(?![\s\S]))/gm;
const IMAGE_RE = /!\[([^\]]*)\]\(([^)\s]*)[^)]*\)/g;
const LINK_RE = /\[([^\]]*)\]\(([^)\s]*)[^)]*\)/g;
const HEADER_RE = /^[ \t]{0,3}#{1,6}[ \t]+/gm;
// Underscores are stripped only at word edges so snake_case survives.
// The leading lookbehind matches the one on the tag patterns below: a
// backslash-escaped marker is a character the user typed, not formatting.
// Without it the marker is dropped here and its backslash survives
// MD_ESCAPE_RE (which needs the pair), turning `2 \* 3` into `2 \ 3`.
const EMPHASIS_RE = /(?<!\\)(?:\*{1,3}|~~|`+|(?<!\w)_{1,3}|_{1,3}(?!\w))/g;
// The serializer emits `<url>` (or `<user@host>`) when a link's label
// is its target — unwrap before the tag strip so the target survives.
// Literal `<` typed by the user is backslash-escaped by the
// serializer, hence the lookbehinds: escaped brackets are user content
// and must be left alone (they are un-escaped at the very end).
const AUTOLINK_RE = /(?<!\\)<((?:https?:\/\/|mailto:)[^>\s]+|[^@>\s]+@[^@>\s]+\.[^@>\s]+)>/g;
const BR_TAG_RE = /(?<!\\)<br[ \t]*\/?>/gi;
const HTML_TAG_RE = /(?<!\\)<[^<>]+>/g;
// Hard breaks are serialized as a trailing backslash: keep the line
// break, drop the marker (tolerate CRLF endings).
const HARD_BREAK_RE = /\\[ \t]*\r?$/gm;
const MD_ESCAPE_RE = /\\([\\`*_{}[\]()#+\-.!<>~|])/g;

const labelWithUrl = (_match: string, label: string, url: string): string => {
    if (label && url) return `${label} (${url})`;
    return label || url;
};

// A link target is a literal, not prose: the passes below would eat characters
// that are perfectly legal in a URL — `_` at a segment edge (`…/Foo_(bar)`),
// `*`, `~~` — and hand the recipient a broken link. Targets are swapped for
// placeholders before the prose is cleaned, then restored. NUL is the sentinel
// because a BlockNote export cannot contain one.
const URL_PLACEHOLDER_RE = /\u0000(\d+)\u0000/g;

const holdUrls = () => {
    const urls: string[] = [];
    return {
        // An empty target stays empty so `labelWithUrl` can still fall back to
        // the label alone rather than emit a bare "label ()".
        hold: (url: string) =>
            url ? `\u0000${urls.push(url) - 1}\u0000` : '',
        // Escapes are still resolved on the target itself: markdown escapes
        // parentheses inside URLs, and the recipient wants them back.
        // An out-of-range index means the sentinel came from the source text
        // rather than from `hold`, so it is left verbatim: content the user
        // typed, and never a crash on the composer's export path.
        restore: (text: string) =>
            text.replace(URL_PLACEHOLDER_RE, (match, index: string) =>
                urls[Number(index)]?.replace(MD_ESCAPE_RE, '$1') ?? match,
            ),
    };
};

// Removing a nested tag can reassemble an outer one (`<scr<b>ipt>` →
// `<script>`), so strip until a fixed point is reached.
const stripHtmlTags = (text: string): string => {
    let previous: string;
    do {
        previous = text;
        text = text.replace(HTML_TAG_RE, '');
    } while (text !== previous);
    return text;
};

const stripMarkdownSyntax = (prose: string): string => {
    const { hold, restore } = holdUrls();
    const withoutTags = stripHtmlTags(
        prose
            .replace(IMAGE_RE, (match, label: string, url: string) =>
                labelWithUrl(match, label, hold(url)),
            )
            .replace(LINK_RE, (match, label: string, url: string) =>
                labelWithUrl(match, label, hold(url)),
            )
            .replace(AUTOLINK_RE, (_match, url: string) => hold(url))
            .replace(BR_TAG_RE, '\n'),
    );
    return restore(
        withoutTags
            .replace(HEADER_RE, '')
            .replace(HARD_BREAK_RE, '')
            .replace(EMPHASIS_RE, '')
            .replace(MD_ESCAPE_RE, '$1'),
    );
};

/**
 * Converts markdown to readable plain text for the email `text/plain` part.
 *
 * Unlike a preview snippet, this is the full content shown to recipients on
 * text-only clients, so links and images keep their URL (`label (url)`), and
 * the line structure — lists, blockquotes — is preserved as-is since it reads
 * naturally in plain text. Only pure syntax noise is removed: emphasis
 * markers, backticks, heading hashes, code-fence lines and the raw HTML tags
 * the serializer falls back to for constructs markdown cannot express.
 * Fenced code bodies are kept verbatim: their content is literal code, so
 * angle brackets or markdown-looking text inside them is not user formatting
 * to strip.
 *
 * @param markdown The markdown source (BlockNote export).
 * @returns The plain-text rendition, trimmed.
 */
export const markdownToPlainText = (markdown: string): string => {
    let result = '';
    let lastIndex = 0;
    for (const match of markdown.matchAll(FENCED_BLOCK_RE)) {
        result += stripMarkdownSyntax(markdown.slice(lastIndex, match.index));
        result += match[1];
        lastIndex = match.index + match[0].length;
    }
    result += stripMarkdownSyntax(markdown.slice(lastIndex));
    return result.trim();
};

/**
 * Serializes BlockNote blocks to readable plain text for the email text body.
 *
 * This is the export every composer should use for `textBody`: recipients on
 * text-only clients see prose, not markdown syntax.
 */
export const blocksToPlainText = async <
    BSchema extends BlockSchema,
    ISchema extends InlineContentSchema,
    SSchema extends StyleSchema,
>(
    editor: BlockNoteEditor<BSchema, ISchema, SSchema>,
    blocks: PartialBlock<BSchema, ISchema, SSchema>[],
): Promise<string> => {
    return markdownToPlainText(await blocksToMarkdown(editor, blocks));
};
