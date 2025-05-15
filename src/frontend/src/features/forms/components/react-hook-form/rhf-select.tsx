import { Controller, useFormContext } from "react-hook-form";
import { Select } from "@openfun/cunningham-react";
import { SelectProps } from "@openfun/cunningham-react";

/**
 * A wrapper component for the Select component that integrates with react-hook-form.
 * 
 * This component allows you to use the Select component as a controlled component
 * with react-hook-form's form state management.
 */
export const RhfSelect = (props: SelectProps & { name: string }) => {
  const { control, setValue } = useFormContext();
  return (
    <Controller
      control={control}
      name={props.name}
      render={({ field, fieldState }) => {
        return (
          <Select
            {...field}
            {...props}
            aria-invalid={!!fieldState.error}
            state={fieldState.error ? "error" : "default"}
            onChange={(e) => setValue(field.name, e.target.value, { shouldDirty: true })}
            value={field.value}
          />
        );
      }}
    />
  );
};
