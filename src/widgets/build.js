import { build } from 'vite'
import { readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))

const widgetsDir = join(__dirname, 'src', 'widgets')

function discoverWidgets() {
  return readdirSync(widgetsDir, { withFileTypes: true })
    .filter(dirent => dirent.isDirectory())
    .map(dirent => dirent.name)
}

// Run an independent build for each widget
for (const widget of discoverWidgets()) {
    await build({
        build: {
            emptyOutDir:false,
            rollupOptions: {
                input: join(widgetsDir, widget, 'main.ts'),
                output: {
                    entryFileNames: widget + '.js',
                    format: 'iife'
                }
            }
        },
    })
}
