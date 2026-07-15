import { useState } from "react";
import { StepForm, ImporterFormStep } from "./step-form";

type ImportNewViewProps = {
  mailboxId: string;
  /** The import run was created and is now processing — return to the list. */
  onStarted: () => void;
};

/**
 * The "new import" sub-view of the Imports settings tab: hosts StepForm (file
 * archive upload or IMAP credentials). It covers only the submission — once the
 * import run is created, `onStarted` returns to the imports list, which tracks
 * server-side progress and cancellation. The "back to imports" affordance and the
 * heading live in the settings tab title (see ImportNewTitle), above the form.
 */
export const ImportNewView = ({ mailboxId, onStarted }: ImportNewViewProps) => {
  // Drives StepForm's heading and the archive progress overlay (idle → uploading);
  // on success `onStarted` hands off to the list.
  const [step, setStep] = useState<ImporterFormStep>("idle");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="import-new-view">
      <StepForm
        mailboxId={mailboxId}
        step={step}
        error={error}
        onUploading={() => {
          setError(null);
          setStep("uploading");
        }}
        // The run exists and is processing server-side; the list takes over.
        onSuccess={onStarted}
        onError={(message) => {
          setStep("idle");
          setError(message);
        }}
      />
    </div>
  );
};
