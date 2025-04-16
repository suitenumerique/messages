import { defineConfig } from "orval";

export default defineConfig({
    messages: {
        input: "../backend/core/api/openapi.json",
        output: {
            client: "react-query",
            httpClient: "fetch",
            mode: 'tags-split',
            workspace: 'src/features/api/gen',
            target: "api.ts",
            schemas: 'models',
            namingConvention: 'snake_case',
            prettier: true,
            override: {
                mutator: {
                    path: '../fetchApi.ts',
                    name: 'fetchAPI',
                }
            },
        }
    }
});
