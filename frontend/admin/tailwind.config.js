/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      boxShadow: {
        glass: "0 24px 70px rgba(0, 0, 0, 0.32), inset 0 1px 0 rgba(255, 255, 255, 0.14)",
        glow: "0 0 34px rgba(45, 212, 191, 0.22)",
      },
      colors: {
        aurora: {
          cyan: "#67e8f9",
          teal: "#2dd4bf",
          violet: "#a78bfa",
          rose: "#fb7185",
          amber: "#fbbf24",
        },
      },
    },
  },
  plugins: [],
};
