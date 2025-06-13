import { Controller, useFormContext } from "react-hook-form";
import { Checkbox, InputProps } from "@openfun/cunningham-react";

/**
 * A wrapper component for the Checkbox component that integrates with react-hook-form.
 * 
 * This component allows you to use the Checkbox component as a controlled component
 * with react-hook-form's form state management.
 */
export const RhfCheckbox = (props: InputProps & { name: string }) => {
  const { control, setValue } = useFormContext();
  return (
    <Controller
      control={control}
      name={props.name}
      render={({ field, fieldState }) => {
        return (
          <Checkbox
            {...props}
            aria-invalid={!!fieldState.error}
            state={fieldState.error ? "error" : "default"}
            onBlur={(event) => {
              field.onBlur();
              props.onBlur?.(event);
            }}
            onChange={(e) => setValue(field.name, e.target.checked, { shouldDirty: true })}
            checked={field.value}
          />
        );
      }}
    />
  );
};
