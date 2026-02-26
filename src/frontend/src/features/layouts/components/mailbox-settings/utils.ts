/**
 * Extracts the signature template ID from a raw BlockNote body.
 * Looks for a block of type "signature" and returns its templateId prop.
 */
export const extractSignatureId = (rawBody: string): string | null => {
  try {
    const blocks = JSON.parse(rawBody) as Array<{
      type: string;
      props?: { templateId?: string };
    }>;
    const signatureBlock = blocks.find((block) => block.type === "signature");
    return signatureBlock?.props?.templateId || null;
  } catch {
    return null;
  }
};
