import { jsonSchemaObjectToZodRawShape } from "zod-from-json-schema";
import { JSONSchema } from "zod/v4/core";

export type ItemJsonSchema =
  | JSONSchema.StringSchema
  | JSONSchema.BooleanSchema
  | JSONSchema.IntegerSchema
  | JSONSchema.NumberSchema

export const convertJsonSchemaToZod = (schema: JSONSchema.Schema) => {
  try {
    return jsonSchemaObjectToZodRawShape(schema);
  } catch (error) {
    throw new Error(`Error converting JSON Schema to Zod: ${error}`);
  }
};
