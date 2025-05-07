import { Controller, useFormContext } from "react-hook-form";
import { Input, InputProps } from "@openfun/cunningham-react";

/**
 * A wrapper component for the Input component that integrates with react-hook-form.
 * 
 * This component allows you to use the Input component as a controlled component
 * with react-hook-form's form state management.
 */
export const RhfInput = (props: InputProps & { name: string }) => {
  const { control, setValue } = useFormContext();
  return (
    <Controller
      control={control}
      name={props.name}
      render={({ field, fieldState }) => {
        return (
          <Input
            {...props}
            aria-invalid={!!fieldState.error}
            state={fieldState.error ? "error" : "default"}
            onBlur={field.onBlur}
            onChange={(e) => setValue(field.name, e.target.value, { shouldDirty: true })}
            value={field.value}
          />
        );
      }}
    />
  );
};