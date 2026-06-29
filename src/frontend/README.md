# Messages frontend

This is the Messages frontend, built with [Vite](https://vite.dev/) and [TanStack Router](https://tanstack.com/router/).

## Getting Started

Install dependencies and run the development server:

```bash
npm install
npm run dev
```

Open [http://localhost:8900](http://localhost:8900) when running through `make start`,
or [http://localhost:3000](http://localhost:3000) when running `npm run dev` directly.

Routes are file-based under `src/routes/`. Compatibility helpers for legacy
`next/router`, `next/navigation`, and `next/link` call sites live in
`src/lib/router-compat.ts`.

## Useful scripts

- `npm run dev` — start the Vite dev server.
- `npm run build` — type-check and produce a production build in `dist/`.
- `npm run preview` — preview the production build locally.
- `npm run test` — run the Vitest test suite.
- `npm run lint` — run ESLint.
- `npm run ts:check` — run TypeScript in `--noEmit` mode.
