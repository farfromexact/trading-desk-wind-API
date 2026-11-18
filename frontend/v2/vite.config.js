import {defineConfig} from 'vite';
import {fileURLToPath} from 'node:url';
const here = fileURLToPath(new URL('.', import.meta.url));
const out = fileURLToPath(new URL('../../data/dashboard-v2/site/', import.meta.url));
export default defineConfig({
  root: here, base: './', publicDir: false,
  build: {outDir: out, emptyOutDir: true},
});
