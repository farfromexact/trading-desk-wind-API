import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
export default defineConfig({server:{host:'127.0.0.1',proxy:{'/api':'http://127.0.0.1:8765'}},build:{chunkSizeWarningLimit:600,
  rollupOptions:{input:{main:fileURLToPath(new URL('./index.html',import.meta.url)),context:fileURLToPath(new URL('./context-preview.html',import.meta.url))}}}});
