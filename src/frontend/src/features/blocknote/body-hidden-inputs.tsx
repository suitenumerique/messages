import { useFormContext } from 'react-hook-form';

/**
 * Hidden form inputs for htmlBody, textBody and rawBody.
 * Shared by SignatureComposer and TemplateComposer.
 */
export const BodyHiddenInputs = () => {
    const form = useFormContext();
    return (
        <>
            <input {...form.register("htmlBody")} type="hidden" />
            <input {...form.register("textBody")} type="hidden" />
            <input {...form.register("rawBody")} type="hidden" />
        </>
    );
};
