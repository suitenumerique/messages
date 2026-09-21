import { Extension } from '@tiptap/core';
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state';
import { Decoration, DecorationSet, EditorView } from '@tiptap/pm/view';

const MIN_PREFIX_LENGTH = 2;

const COMPLETIONS = [
    // French greetings and closings
    'bonjour',
    'bonsoir',
    'bonne journee',
    'bonne soiree',
    'bien cordialement',
    'cordialement',
    'merci',
    'merci beaucoup',
    'merci par avance',
    'a bientot',
    'a votre disposition',
    'je vous remercie',
    'je reste a votre disposition',
    'n hesitez pas a me contacter',
    'pourriez-vous',
    'serait-il possible',
    'veuillez trouver ci-joint',
    'suite a notre echange',
    'comme convenu',
    'dans l attente de votre retour',
    'avec mes salutations distinguees',

    // English greetings and closings
    'hello',
    'hi',
    'good morning',
    'good afternoon',
    'good evening',
    'thanks',
    'thank you',
    'thank you very much',
    'thanks in advance',
    'best regards',
    'kind regards',
    'regards',
    'sincerely',
    'please find attached',
    'following up on',
    'as discussed',
    'let me know',
    'please let me know',
    'do not hesitate to contact me',
    'i remain at your disposal',
];

const normalize = (value: string) =>
    value
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLocaleLowerCase();

const applyPrefixCasing = (completion: string, prefix: string) => {
    if (prefix === prefix.toLocaleUpperCase()) {
        return completion.toLocaleUpperCase();
    }

    if (prefix[0] === prefix[0].toLocaleUpperCase()) {
        return completion[0].toLocaleUpperCase() + completion.slice(1);
    }

    return completion;
};

const getCompletion = (prefix: string) => {
    const normalizedPrefix = normalize(prefix);
    if (normalizedPrefix.length < MIN_PREFIX_LENGTH) return null;

    const completion = COMPLETIONS
        .filter((item) => normalize(item).startsWith(normalizedPrefix))
        .sort((a, b) => a.length - b.length)[0];

    if (!completion || normalize(completion) === normalizedPrefix) return null;
    return applyPrefixCasing(completion, prefix).slice(prefix.length);
};

const getCurrentWord = (view: EditorView) => {
    const { selection } = view.state;
    if (!(selection instanceof TextSelection) || !selection.empty) return null;

    const { $from } = selection;
    if (!$from.parent.isTextblock) return null;

    const textBeforeCursor = $from.parent.textBetween(0, $from.parentOffset, undefined, '\ufffc');
    const match = textBeforeCursor.match(/[\p{L}\p{M}'-]+$/u);
    return match?.[0] ?? null;
};

const getSuggestion = (view: EditorView) => {
    const word = getCurrentWord(view);
    if (!word) return null;

    const suffix = getCompletion(word);
    if (!suffix) return null;

    return { word, suffix };
};

export const KeywordAutocomplete = Extension.create({
    name: 'keywordAutocomplete',

    addProseMirrorPlugins() {
        return [
            new Plugin({
                key: new PluginKey('keywordAutocomplete'),
                props: {
                    decorations: (state) => {
                        const view = this.editor.view;
                        const suggestion = getSuggestion(view);
                        if (!suggestion) return DecorationSet.empty;

                        const widget = Decoration.widget(
                            state.selection.from,
                            () => {
                                const span = document.createElement('span');
                                span.className = 'keyword-autocomplete-suggestion';
                                span.textContent = suggestion.suffix;
                                return span;
                            },
                            { side: 1 },
                        );

                        return DecorationSet.create(state.doc, [widget]);
                    },
                    handleKeyDown: (view, event) => {
                        const suggestion = getSuggestion(view);
                        if (!suggestion) return false;

                        if (event.key === 'Escape') {
                            event.preventDefault();
                            return true;
                        }

                        if (event.key !== 'Tab') return false;

                        event.preventDefault();
                        view.dispatch(view.state.tr.insertText(suggestion.suffix));
                        return true;
                    },
                },
            }),
        ];
    },
});
