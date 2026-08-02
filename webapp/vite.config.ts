import { fileURLToPath, URL } from "node:url"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  // Served by OpenGateway at /ui/
  base: "/ui/",
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8765",
      "/ping": "http://127.0.0.1:8765",
      "/agents": "http://127.0.0.1:8765",
      "/runs": "http://127.0.0.1:8765",
      "/meta": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
})
