// Demo login: both buttons open the app immediately, whatever the fields contain.
// The values are never validated, sent, logged or stored, and are cleared on entry.
import { useState, type FormEvent } from "react";
import { LensIcon, Logo } from "../components/Brand";

export function LoginPage({ onEnter }: { onEnter: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const enter = () => {
    setUsername("");
    setPassword("");
    onEnter();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    enter();
  };

  return (
    <div className="login">
      <section className="login__brand" aria-hidden="true">
        <div className="login__brand-inner">
          <LensIcon size={96} ring="#FFFFFF" handle="var(--color-accent)" />
          <p className="login__wordmark">MEDLENS</p>
          <span className="login__rule" />
          <p className="login__tagline">Clarity in every question.</p>
          <p className="login__sub">Ask about the hospital database in plain English.</p>
        </div>
        <p className="login__facts">LOCAL PROCESSING · READ-ONLY</p>
      </section>

      <main className="login__main">
        <div className="login__card">
          <Logo size={28} />
          <h1 className="login__title">Welcome back</h1>
          <p className="login__notice" role="note">Demo access — no authentication.</p>
          <p className="login__hint">Any or no username and password opens the demo. Nothing you type here is checked, sent or stored.</p>

          <button type="button" className="btn btn--primary btn--block" onClick={enter}>Continue with SSO</button>

          <div className="login__divider"><span>or use your username</span></div>

          <form onSubmit={submit} autoComplete="off" noValidate>
            <label className="field">
              <span className="field__label">Username</span>
              <input className="field__input" name="demo-username" autoComplete="off" spellCheck={false}
                value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label className="field">
              <span className="field__label">Password</span>
              <input className="field__input" type="password" name="demo-password" autoComplete="off"
                value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button type="submit" className="btn btn--primary btn--block">Sign in</button>
          </form>
        </div>
        <p className="login__footer">Demo access — no authentication · Local, read-only data access</p>
      </main>
    </div>
  );
}
