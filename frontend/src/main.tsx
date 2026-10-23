import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Inter is bundled from the installed package (no font CDN).
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/pages.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
