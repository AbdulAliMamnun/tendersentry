import type { Config } from "tailwindcss";

/**
 * Warm editorial, light throughout. There are no dark sections anywhere on this
 * site: #292524 is type and primary buttons, never a background band.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: "#faf9f7",
        card: "#ffffff",
        hairline: "#f0ede6",
        heading: "#292524",
        body: "#57534e",
        muted: "#a8a29e",
        brand: { red: "#A32D2D", redSoft: "#FCEBEB" },
        fit: { green: "#477054", greenSoft: "#eaf5ed" },
      },
      borderRadius: { card: "16px", pill: "999px", control: "12px" },
      maxWidth: { shell: "1040px" },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto",
          "Helvetica Neue", "Arial", "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
export default config;
