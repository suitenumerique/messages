"use client";
import * as locales from '@blocknote/core/locales';
import { BlockNoteView } from "@blocknote/mantine";
import "@blocknote/mantine/style.css";
import { FormattingToolbar, useCreateBlockNote } from "@blocknote/react";
import { useState } from 'react';
import { useTranslation } from "react-i18next";

type MessageEditorProps = {
    name: string;
}

const MessageEditor = ({ name }: MessageEditorProps) => {
    const { i18n } = useTranslation();
    const [html, setHtml] = useState<string>(""); 
    const editor = useCreateBlockNote({
        tabBehavior: "prefer-navigate-ui",
        trailingBlock: false,
        dictionary: locales[i18n.language as keyof typeof locales],
    }, [i18n.resolvedLanguage]);
    
    return (
        <>
            <BlockNoteView
                editor={editor}
                theme="light"
                className="message-editor"
                sideMenu={false}
                slashMenu={false}
                formattingToolbar={false}
                onBlur={async () => {
                    const html = await editor.blocksToHTMLLossy(editor.document);
                    setHtml(html);
                }}
            >
                <FormattingToolbar />
            </BlockNoteView>
            <input type="hidden" name={name} value={html} />
        </>
    );
};

export default MessageEditor;
