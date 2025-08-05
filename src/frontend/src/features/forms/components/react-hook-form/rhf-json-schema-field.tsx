import { Checkbox, CheckboxProps, FieldProps, Input, InputProps, Select, SelectProps } from "@openfun/cunningham-react";
import { Controller, FieldValues, useFormContext, UseFormSetValue } from "react-hook-form";
import { JSONSchema } from "zod/v4/core";
import { useTranslation } from "react-i18next";
import { ItemJsonSchema } from "../zod-json-schema-serializer";

/**
 * We are using custom annotations (x-) to pass translation strings through the
 * schema. In this way we are able to have internationalized form field
 * only through env vars JSON Schema.
 *
 * https://json-schema.org/blog/posts/custom-annotations-will-continue
 */
type JSONSchemaWithI18nAnnotation = ItemJsonSchema & {
    "x-i18n"?: {
        "title"?: { [key: string]: string },
        "description"?: { [key: string]: string }
    }
}

type RhfJsonSchemaFieldProps = FieldProps & {
    name: string;
    required?: boolean;
    schema: JSONSchemaWithI18nAnnotation
}

/**
 * A component to render an input controlled by React Hook Form
 *  according to the provided schema provided.
 *
 * It requires to be rendered as a child of a <FormProvider />.
 */
export const RhfJsonSchemaField = ({ schema, text, ...props }: RhfJsonSchemaFieldProps) => {
    const { control, setValue } = useFormContext();
    const { i18n } = useTranslation();
    const label = schema["x-i18n"]?.["title"]?.[i18n.resolvedLanguage!] || schema.title;
    const helperText = schema["x-i18n"]?.["description"]?.[i18n.resolvedLanguage!] || schema.description;


    return (
        <Controller
            control={control}
            name={props.name}
            render={({ field, fieldState }) => {
                return (
                    <JSONSchemaInput
                        {...field}
                        {...props}
                        label={label || props.name}
                        text={text || helperText}
                        type={schema.type}
                        format={schema.format}
                        choices={schema.enum}
                        aria-invalid={!!fieldState.error}
                        state={fieldState.error ? "error" : "default"}
                        setValue={setValue}
                        value={field.value}
                    />
                )
            }}
        />
    )
};


type JSONSchemaInputProps = {
    name: string;
    type: JSONSchema.BaseSchema['type'],
    format?: JSONSchema.BaseSchema['format']
    choices: JSONSchema.BaseSchema['enum']
    label: string;
    setValue: UseFormSetValue<FieldValues>;
    value: unknown
} & FieldProps;

const JSONSchemaInput = ({ name, type, choices, format, value, setValue, ...props}: JSONSchemaInputProps) => {
    switch (type) {
        case "string":
            if (choices) {
                return (
                    <Select
                        {...props}
                        name={name}
                        options={choices.filter(c => c !== null).map(item => ({ label: item.toString() }))}
                        onChange={(event) => { setValue(name, event.target.value, { shouldDirty: true })}}
                        value={value as SelectProps['value']}
                    />
                );
            }
            return (
                <Input
                    {...props}
                    name={name}
                    type={format ?? "text"}
                    onChange={(event) => { setValue(name, event.target.value, { shouldDirty: true })}}
                    value={value as InputProps['value']}
                />
            );
        case "number":
        case "integer":
            return (
                <Input
                    {...props}
                    type="number"
                    name={name}
                    onChange={(event) => { setValue(name, event.target.value, { shouldDirty: true })}}
                    value={value as InputProps['value']}
                />
            );
        case "boolean":
            return (
                <Checkbox
                    {...props}
                    name={name}
                    checked={Boolean(value) || false}
                    onChange={(e) => setValue(name, e.target.checked, { shouldDirty: true })}
                    value={value as CheckboxProps['value']}
                />
            );
        case "null":
        case undefined:
            return null;
        default:
            // We do not support non primitve type (array, object)
            throw new Error(`Unsupported schema type: ${type}`);
    }
}
