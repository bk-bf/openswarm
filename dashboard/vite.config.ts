import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			// In dev, proxy all /api/* requests to server.py
			'/api': 'http://localhost:7700'
		}
	}
});
