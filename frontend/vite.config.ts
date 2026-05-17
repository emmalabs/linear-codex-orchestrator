import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          markdown: ["react-markdown", "remark-gfm"],
          mantine: ["@mantine/core", "@mantine/hooks"]
        }
      }
    }
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/logs": "http://127.0.0.1:8765"
    }
  }
});
