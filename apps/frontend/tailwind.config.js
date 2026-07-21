/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--tg-theme-bg-color, #0F1115)",
        surface: "var(--tg-theme-secondary-bg-color, #1A1D24)",
        primary: "var(--color-primary, #7C5CFC)",
        success: "var(--color-success, #2FE6C9)",
        danger: "var(--color-danger, #FF5A6E)",
        text: "var(--tg-theme-text-color, #E4E6EB)",
        muted: "var(--tg-theme-hint-color, #8A8F98)",
        gold: "#F2B84B",
      },
      borderRadius: {
        card: "16px",
      },
    },
  },
  plugins: [],
};