import { defineConfig } from "orval";

export default defineConfig({
    messages: {
        input: "../backend/core/api/openapi.json",
        output: {
            client: "react-query",
            clean: true,
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
                },
                operations: {
                    "threads_list": {
                        query: {
                            useInfinite: true,
                            useInfiniteQueryParam: "page"
                        }
                    }
                }
            },
        }
    }
});
