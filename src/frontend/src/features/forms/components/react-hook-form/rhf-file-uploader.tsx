import { Controller, useFormContext } from "react-hook-form";
import { FileUploader, FileUploaderProps } from "@openfun/cunningham-react";
import { ChangeEvent } from "react";

/**
 * A wrapper component for the FileUploader component that integrates with react-hook-form.
 * 
 * This component allows you to use the FileUploader component as a controlled component
 * with react-hook-form's form state management.
 */
export const RhfFileUploader = (props: FileUploaderProps & { name: string }) => {
  const { control, setValue } = useFormContext();
  return (
    <Controller
      control={control}
      name={props.name}
      render={({ field, fieldState }) => {

        return (
          <FileUploader
            {...props}
            aria-invalid={!!fieldState.error}
            state={fieldState.error ? "error" : "default"}
            onBlur={(event) => {
              field.onBlur();
              props.onBlur?.(event);
            }}
            onFilesChange={(e) => {
                setValue(field.name, e.target.value, { shouldDirty: true });
                props.onChange?.(e as unknown as ChangeEvent<HTMLInputElement>);
            }}
          />
        );
      }}
    />
  );
};
