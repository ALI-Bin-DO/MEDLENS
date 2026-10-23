import { useState } from "react";
import { LoginPage } from "./pages/LoginPage";
import { Workspace } from "./pages/Workspace";

/** Demo navigation only: "signed in" is a flag in memory, not authentication. */
export default function App() {
  const [session, setSession] = useState<number | null>(null);
  return session === null ? (
    <LoginPage onEnter={() => setSession(Date.now())} />
  ) : (
    // A new key per sign-in guarantees a fresh, empty workspace.
    <Workspace key={session} onSignOut={() => setSession(null)} />
  );
}
