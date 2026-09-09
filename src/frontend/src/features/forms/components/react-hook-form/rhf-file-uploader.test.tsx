import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { CunninghamProvider } from "@gouvfr-lasuite/ui-components";
import { FormProvider, useForm } from "react-hook-form";
import { RhfFileUploader } from "./rhf-file-uploader";
import { installRandomUUIDPolyfill } from "@/features/utils/uuid";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const Harness = () => {
    const form = useForm<{ archive_file: File[] }>({
        defaultValues: { archive_file: [] },
    });

    return (
        <CunninghamProvider>
            <FormProvider {...form}>
                <RhfFileUploader name="archive_file" accept=".eml,.mbox,.pst" />
            </FormProvider>
        </CunninghamProvider>
    );
};

/**
 * Renders the uploader and picks a file the way a browser does: the file input
 * carries the selection and the change event is what the uploader listens to.
 * The dropzone showing the file name is the only proof the value reached the
 * form — that is exactly what the import e2e asserts on.
 */
const pickFile = async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);

    await act(async () => {
        createRoot(container).render(<Harness />);
    });

    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    const file = new File(["x"], "attachment.png", { type: "image/png" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });

    await act(async () => {
        input!.dispatchEvent(new Event("change", { bubbles: true }));
    });

    return container;
};

// jsdom always exposes `crypto.randomUUID`; the browsers running the e2e stack
// over `http://proxy` do not. Take it away to reproduce that environment.
const withoutRandomUUID = async (run: () => Promise<void>) => {
    const original = Object.getOwnPropertyDescriptor(crypto, "randomUUID");
    Object.defineProperty(crypto, "randomUUID", {
        value: undefined,
        configurable: true,
        writable: true,
    });

    try {
        await run();
    } finally {
        if (original) Object.defineProperty(crypto, "randomUUID", original);
        else delete (crypto as { randomUUID?: unknown }).randomUUID;
    }
};

describe("RhfFileUploader", () => {
    it("hands the picked file to the form", async () => {
        const container = await pickFile();

        expect(container.textContent).toContain("attachment.png");
    });

    it("keeps working when crypto.randomUUID is unavailable", async () => {
        await withoutRandomUUID(async () => {
            installRandomUUIDPolyfill();

            const container = await pickFile();

            expect(container.textContent).toContain("attachment.png");
        });
    });
});
