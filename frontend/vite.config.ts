import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: "./" — сборка работает и на GitHub Pages (подпапка репозитория), и на своём домене.
// В разработке витрина берёт демо-каталог из папки demo/, в сборку он не попадает:
// настоящий catalog.json и фото подкладывает GitHub Actions.
export default defineConfig(({ command }) => ({
  base: "./",
  publicDir: command === "serve" ? "demo" : "public",
  plugins: [react()],
  server: { port: 5173 },
  build: { target: "es2020", assetsInlineLimit: 0 },
}));
