import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
const ALBERT_API_KEY = "sk-eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjo4NDQ4LCJ0b2tlbl9pZCI6MTUwNywiZXhwaXJlc19hdCI6MTc4MDQzNzYwMH0.AJd2FyLOwpt2rQX0zzbiTQNTkVpmXXxisJE474l47M8";
const ALBERT_MODEL = 'neuralmagic/Meta-Llama-3.1-70B-Instruct-FP8'

export const model = createOpenAICompatible({
    name: "albert-etalab",
    baseURL: "https://albert.api.etalab.gouv.fr/v1",
    apiKey: ALBERT_API_KEY,
})(ALBERT_MODEL)
