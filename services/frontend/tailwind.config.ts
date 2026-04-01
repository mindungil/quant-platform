import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#08111f",
        sea: "#203a43",
        mist: "#eef7fb",
        sand: "#f6bd60",
        mint: "#84dcc6"
      }
    }
  },
  plugins: []
};

export default config;
