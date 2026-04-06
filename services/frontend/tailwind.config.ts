import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: {
          cyan: '#06b6d4',
          purple: '#a855f7',
          blue: '#3b82f6',
          indigo: '#6366f1',
        },
      },
      keyframes: {
        'glow-pulse': {
          '0%, 100%': { boxShadow: '0 0 20px rgba(6, 182, 212, 0.15)' },
          '50%': { boxShadow: '0 0 40px rgba(6, 182, 212, 0.3)' },
        },
        'gradient-shift': {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
        },
      },
      animation: {
        'glow-pulse': 'glow-pulse 3s ease-in-out infinite',
        'gradient-shift': 'gradient-shift 6s ease infinite',
      },
    }
  },
  plugins: []
};

export default config;
