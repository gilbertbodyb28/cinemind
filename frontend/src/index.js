import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import App from "./App";
import { AuthProvider } from "@/context/AuthContext";
import { ThemeProvider, applyGlassIntensity, applyTheme, readStoredGlass, readStoredTheme, useTheme } from "@/context/ThemeContext";
import "./index.css";
import "./themes/vision-glass.css";
import "./themes/glass-slider.css";
import "./themes/apple.css";
import "./themes/perf.css";
import "./themes/poster-card.css";
import "./themes/wallpapers.css";

applyTheme(readStoredTheme());
applyGlassIntensity(readStoredGlass());

function ThemedToaster() {
  const { isApple } = useTheme();
  return (
    <Toaster
      theme="dark"
      position="top-right"
      toastOptions={{
        style: isApple
          ? {
              background: "rgba(255,255,255,var(--lg-fill))",
              border: "1px solid rgba(255,255,255,var(--lg-stroke))",
              backdropFilter: "blur(var(--lg-blur)) saturate(var(--lg-sat))",
              color: "#F5F5F7",
            }
          : {
              background: "rgba(255,246,232,var(--lg-fill))",
              border: "1px solid rgba(255,240,220,var(--lg-stroke))",
              backdropFilter: "blur(var(--lg-blur)) saturate(var(--lg-sat))",
              color: "#F6EFE4",
            },
      }}
    />
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));

root.render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <ThemeProvider>
          <App />
          <ThemedToaster />
        </ThemeProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
