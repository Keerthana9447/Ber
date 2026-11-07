/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        forest: {
          50: "#eef7f1", 100: "#d5ebdd", 200: "#aed7bd", 300: "#7dbc96", 400: "#4f9f71",
          500: "#2f8457", 600: "#1f6b45", 700: "#18563a", 800: "#14442f", 900: "#0e3122", 950: "#08201a",
        },
        ivory: { 50: "#fdfcf8", 100: "#faf7ef", 200: "#f3eedf", 300: "#e8e1cc" },
        gold: { 300: "#e6cf8f", 400: "#d8b95e", 500: "#c9a43c", 600: "#a9862c" },
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "Arial", "sans-serif"] },
      boxShadow: { card: "0 1px 2px rgba(14,49,34,0.06), 0 1px 3px rgba(14,49,34,0.08)" },
    },
  },
  plugins: [],
};
