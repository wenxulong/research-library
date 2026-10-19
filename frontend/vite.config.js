import { fileURLToPath } from "node:url"
import { defineConfig, loadEnv } from "vite"
import vue from "@vitejs/plugin-vue"

// 配置路径始终相对前端目录；只读取前端自己的VITE_配置。
const frontendRoot = fileURLToPath(new URL(".", import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, frontendRoot, "VITE_")
  const proxy = {
    "/api": {
      target: env.VITE_API_TARGET || "http://127.0.0.1:8000",
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ""),
    },
  }

  return {
    plugins: [vue()],
    base: "/app/",
    server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
    preview: {
      host: "127.0.0.1", port: 4173, strictPort: true,
      proxy: {
        ...proxy,
        ...Object.fromEntries(["/papers", "/research-tasks", "/research-question-sets", "/experiment-imports", "/paper-drafts", "/paper-preparation-jobs", "/report-jobs", "/health"].map(path => [path, { target: env.VITE_API_TARGET || "http://127.0.0.1:8000", changeOrigin: true }])),
      },
    },
  }
})
