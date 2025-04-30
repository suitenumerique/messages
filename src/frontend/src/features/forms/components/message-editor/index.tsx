"use client";
import * as locales from '@blocknote/core/locales';
import { BlockNoteView } from "@blocknote/mantine";
import "@blocknote/mantine/style.css";
import { useCreateBlockNote } from "@blocknote/react";
import { useState } from 'react';
import { useTranslation } from "react-i18next";
import { BlockNoteEditorOptions, BlockSchema, InlineContentSchema, StyleSchema } from '@blocknote/core';
import MailHelper from '@/features/utils/mail-helper';
import MessageEditorToolbar from './toolbar';
import { Field, FieldProps } from '@openfun/cunningham-react';

type MessageEditorProps = FieldProps & {
    blockNoteOptions?: Partial<BlockNoteEditorOptions<BlockSchema, InlineContentSchema, StyleSchema>>
    defaultValue?: string;
}

/**
 * A component aimed to be used within a form 
 * that allows the user to edit a message in a BlockNote editor.
 * 
 * Two hidden inputs (`htmlBody` and `textBody`) are rendered to store
 * the HTML and text content of the message. Their values are updated
 * when the editor is blurred. Those inputs must be used in the parent form
 * to retrieve text and html content.
 */
const MessageEditor = ({ blockNoteOptions, defaultValue, ...props }: MessageEditorProps) => {
    const { t, i18n } = useTranslation();
    const [html, setHtml] = useState<string>(""); 
    const [text, setText] = useState<string>(""); 
    const editor = useCreateBlockNote({
        tabBehavior: "prefer-navigate-ui",
        trailingBlock: false,
        initialContent: defaultValue ? JSON.parse(defaultValue) : undefined,
        dictionary: {
            ...locales[i18n.language as keyof typeof locales],
            placeholders: {
                ...locales[i18n.language as keyof typeof locales].placeholders,
                emptyDocument: t('message_editor.start_typing'),
                default: t('message_editor.start_typing'),
            }
        },
        ...blockNoteOptions,
    }, [i18n.resolvedLanguage, defaultValue]);

    const handleBlur = async () => {
        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const html = await MailHelper.markdownToHtml(markdown);
        setHtml(html);
        setText(markdown);
    }
    
    return (
        <Field {...props}>
            <BlockNoteView
                editor={editor}
                theme="light"
                className="message-editor"
                sideMenu={false}
                slashMenu={false}
                formattingToolbar={false}
                onChange={handleBlur}
                onBlur={handleBlur}
            >
                <MessageEditorToolbar />
            </BlockNoteView>
            <input type="hidden" name="messageEditorHtml" value={html} required />
            <input type="hidden" name="messageEditorText" value={text} required />
            <input type="hidden" name="messageEditorDraft" value={JSON.stringify(editor.document)} required />
        </Field>
    );
};

export default MessageEditor;
