/** VISION UI LOCKED — do not change these color tokens.
 * @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ["./src/**/*.{js,jsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        background: "#0B0907",
        surface: "#17130F",
        border: "rgba(255,240,220,0.10)",
        foreground: "#F6EFE4",
        muted: "#BFB09A",
        subtle: "#8C7F6D",
        primary: {
          DEFAULT: "#D8B26A",
          hover: "#EBD3A3",
        },
        secondary: "#A8BE92",
        tertiary: "#C97A54",
        amber: "#D8B26A",
        emerald: "#A8BE92",
      },
      fontFamily: {
        heading: ["Outfit", "Plus Jakarta Sans", "sans-serif"],
        sans: ["Inter", "DM Sans", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      boxShadow: {
        glass: "0 8px 32px rgba(0,0,0,0.36)",
        brutal: "0 12px 34px -12px rgba(216,178,106,0.5)",
      },
      animation: {
        "fade-up": "fadeUp .45s ease-out both",
        "soft-pulse": "softPulse 2.4s ease-in-out infinite",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        softPulse: {
          "0%, 100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
      },
    },
  },
  plugins: [],
};
