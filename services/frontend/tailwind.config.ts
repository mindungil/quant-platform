import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // Quant-trader identity: monospace dominant. Used for chrome AND hero numbers.
        sans: ['"JetBrains Mono"', '"IBM Plex Mono"', 'ui-monospace', 'monospace'],
        // Display = same mono, just sized big and tight — no serif at all.
        display: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
        // Body prose where mono would be too dense (long descriptions).
        prose: ['"IBM Plex Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        // Explicit alias for tabular data
        mono: ['"JetBrains Mono"', '"SF Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        // Cool ink — quant terminal black with a hint of blue, not warm.
        ink: {
          DEFAULT: '#0a0c10',
          50:  '#0f1217',
          100: '#13171e',
          200: '#191e26',
          300: '#222831',
        },
        // Cool off-white instead of warm cream — reads "Bloomberg pro" not "magazine".
        paper: {
          DEFAULT: '#e6ebf0',
          dim:  '#aab2bd',
          mute: '#6b7480',
          low:  '#444c56',
        },
        rule: {
          DEFAULT: '#1a1f26',
          strong: '#252b34',
          loud:   '#38404a',
        },
        // Bloomberg amber tuned slightly cooler.
        amber: {
          DEFAULT: '#fbbd2e',
          deep:    '#c98a00',
          glow:    'rgba(251, 189, 46, 0.14)',
          ring:    'rgba(251, 189, 46, 0.32)',
        },
        // Saturated trading mint — TradingView/Two Sigma class green.
        mint: {
          DEFAULT: '#2dd4bf',
          deep:    '#0e8c7c',
          soft:    'rgba(45, 212, 191, 0.12)',
        },
        // Trader red — clean coral, not OWASP red.
        coral: {
          DEFAULT: '#f87171',
          deep:    '#b8302f',
          soft:    'rgba(248, 113, 113, 0.12)',
        },
        // GitHub-class cyan for info / link accents.
        cyan: {
          DEFAULT: '#58a6ff',
          deep:    '#1d4f8c',
          soft:    'rgba(88, 166, 255, 0.12)',
        },
      },
      fontSize: {
        '3xs': ['0.625rem', { lineHeight: '0.85rem', letterSpacing: '0.18em' }],   // 10px
        '2xs': ['0.6875rem', { lineHeight: '0.95rem', letterSpacing: '0.12em' }], // 11px
        'xs':  ['0.75rem',   { lineHeight: '1.15rem' }],
        'sm':  ['0.8125rem', { lineHeight: '1.3rem' }],
        'base':['0.9375rem', { lineHeight: '1.5rem' }],
        'lg':  ['1.0625rem', { lineHeight: '1.6rem' }],
        'xl':  ['1.25rem',   { lineHeight: '1.7rem' }],
        '2xl': ['1.625rem',  { lineHeight: '1.9rem' }],
        '3xl': ['2.125rem',  { lineHeight: '2.25rem' }],
        '4xl': ['2.875rem',  { lineHeight: '2.85rem' }],
        '5xl': ['3.75rem',   { lineHeight: '3.6rem' }],
        '6xl': ['5rem',      { lineHeight: '4.6rem' }],
      },
      keyframes: {
        ticker: {
          '0%':   { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(-50%)' },
        },
        amberBlink: {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0.32' },
        },
        revealUp: {
          '0%':   { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        scan: {
          '0%':   { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100vh)' },
        },
      },
      animation: {
        ticker: 'ticker 60s linear infinite',
        'amber-blink': 'amberBlink 1.4s ease-in-out infinite',
        'reveal-up': 'revealUp 0.5s ease-out both',
      },
    }
  },
  plugins: []
};
export default config;
