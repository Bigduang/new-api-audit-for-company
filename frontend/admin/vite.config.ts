import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/admin/",
  plugins: [react()],
  build: {
    outDir: "../../token_audit/admin_dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/admin/api": "http://127.0.0.1:8000",
      "/admin/reports": "http://127.0.0.1:8000",
      "/reports": "http://127.0.0.1:8000",
    },
  },
});
