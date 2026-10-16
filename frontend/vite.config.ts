import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The dev server forwards /api to the FastAPI backend, so there are no CORS problems.
// Backend on another laptop? Put VITE_BACKEND=http://192.168.x.x:8000 in a .env file here.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        '/api': { target: env.VITE_BACKEND || 'http://localhost:8000', changeOrigin: true },
      },
    },
  };
});
