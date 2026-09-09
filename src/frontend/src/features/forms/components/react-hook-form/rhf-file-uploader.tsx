import { Controller, useFormContext } from "react-hook-form";
import {
  FileUploader,
  FileUploaderProps,
  UploadFile,
} from "@gouvfr-lasuite/ui-components";

// The uploader is controlled with `UploadFile[]`, while the form keeps plain
// `File[]` — the shape the zod schemas and submit handlers work with. Ids are
// derived from the file itself rather than generated, so re-rendering the same
// selection keeps stable list keys.
const toUploadFiles = (files: File[]): UploadFile[] =>
  files.map((originalFile) => ({
    id: `${originalFile.name}-${originalFile.size}-${originalFile.lastModified}`,
    originalFile,
    status: "done",
  }));

// `files` and the mutation callbacks are driven by the form state, so they are
// not part of the consumer-facing API.
type RhfFileUploaderProps = Omit<
  FileUploaderProps,
  "files" | "onAddFiles" | "onRemoveFile"
> & { name: string };

/**
 * A wrapper component for the FileUploader component that integrates with react-hook-form.
 *
 * This component allows you to use the FileUploader component as a controlled component
 * with react-hook-form's form state management.
 */
export const RhfFileUploader = ({
  name,
  description,
  descriptionMode,
  ...props
}: RhfFileUploaderProps) => {
  const { control } = useFormContext();

  return (
    <Controller
      control={control}
      name={name}
      render={({ field, fieldState }) => {
        const files = toUploadFiles((field.value ?? []) as File[]);
        const toFieldValue = (uploadFiles: UploadFile[]) =>
          uploadFiles.map((uploadFile) => uploadFile.originalFile);

        return (
          <FileUploader
            {...props}
            name={name}
            files={files}
            description={fieldState.error?.message ?? description}
            descriptionMode={fieldState.error ? "error" : descriptionMode}
            onAddFiles={(addedFiles) => field.onChange(toFieldValue(addedFiles))}
            onRemoveFile={(removedFile) =>
              field.onChange(
                toFieldValue(files.filter((file) => file.id !== removedFile.id)),
              )
            }
          />
        );
      }}
    />
  );
};
