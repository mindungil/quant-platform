import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          950: '#09090b',
          900: '#18181b',
          800: '#27272a',
          700: '#3f3f46',
        },
      },
      keyframes: {
        'white-glow': {
          '0%, 100%': { boxShadow: '0 0 20px rgba(255, 255, 255, 0.05)' },
          '50%': { boxShadow: '0 0 40px rgba(255, 255, 255, 0.10)' },
        },
      },
      animation: {
        'white-glow': 'white-glow 3s ease-in-out infinite',
      },
    }
  },
  plugins: []
};

export default config;
