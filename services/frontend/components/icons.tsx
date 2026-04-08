// Minimal monochrome SVG icon set — replaces generic emojis throughout the app.

export const IconUp = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" className="inline-block">
    <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="1.5" className="text-green-500"/>
    <path d="M10 14V7M10 7l3 3M10 7l-3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-green-500"/>
  </svg>
);

export const IconDown = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" className="inline-block">
    <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="1.5" className="text-red-500"/>
    <path d="M10 6v7M10 13l3-3M10 13l-3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-red-500"/>
  </svg>
);

export const IconPause = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" className="inline-block">
    <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="1.5" className="text-zinc-400"/>
    <path d="M8 7v6M12 7v6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-zinc-400"/>
  </svg>
);

export const IconChart = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="inline-block">
    <path d="M2 14l4-5 3 2 5-7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-400"/>
  </svg>
);

export const IconShield = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="inline-block">
    <path d="M9 2l6 3v4c0 3.5-2.5 6.5-6 7.5-3.5-1-6-4-6-7.5V5l6-3z" stroke="currentColor" strokeWidth="1.5" className="text-zinc-400"/>
  </svg>
);

export const IconEmpty = () => (
  <svg width="32" height="32" viewBox="0 0 32 32" fill="none" className="inline-block">
    <circle cx="16" cy="16" r="12" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 3" className="text-zinc-600"/>
    <path d="M12 16h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-zinc-600"/>
  </svg>
);

export const IconSignal = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="inline-block">
    <path d="M3 13v-2M7 13V9M11 13V6M15 13V3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-zinc-400"/>
  </svg>
);

export const IconLink = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="inline-block">
    <path d="M7 11l4-4M6.5 8.5L4.7 10.3a2.5 2.5 0 003.5 3.5l1.8-1.8M11.5 9.5l1.8-1.8a2.5 2.5 0 00-3.5-3.5L8 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-zinc-400"/>
  </svg>
);

export const IconSearch = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="inline-block">
    <circle cx="8" cy="8" r="5" stroke="currentColor" strokeWidth="1.5" className="text-zinc-400"/>
    <path d="M12 12l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-zinc-400"/>
  </svg>
);
