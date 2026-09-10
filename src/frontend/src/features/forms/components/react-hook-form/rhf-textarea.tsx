import { Controller, useFormContext } from "react-hook-form";
import { TextAreaProps, TextArea } from "@gouvfr-lasuite/ui-components";

/**
 * A wrapper component for the TextArea component that integrates with react-hook-form.
 *
 * This component allows you to use the TextArea component as a controlled component
 * with react-hook-form's form state management.
 */
export const RhfTextArea = (props: TextAreaProps & { name: string }) => {
  const { control, setValue } = useFormContext();
  return (
    <Controller
      control={control}
      name={props.name}
      render={({ field, fieldState }) => {
        return (
          <TextArea
            {...props}
            aria-invalid={!!fieldState.error}
            state={fieldState.error ? "error" : "default"}
            onBlur={(event) => {
              field.onBlur();
              props.onBlur?.(event);
            }}
            onChange={(e) => setValue(field.name, e.target.value, { shouldDirty: true })}
            value={field.value}
          />
        );
      }}
    />
  );
};
