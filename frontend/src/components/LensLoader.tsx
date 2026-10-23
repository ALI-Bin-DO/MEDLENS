import { useEffect, useState } from "react";
import { LensIcon } from "./Brand";

const SLOW_HINT_MS = 15000;

/** Generic waiting indicator: the backend reports no progress, so no stages are shown. */
export function LensLoader() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), SLOW_HINT_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="loader" role="status" aria-live="polite">
      <LensIcon size={44} handle="var(--color-accent)" className="loader__lens" />
      <div>
        <p className="loader__text">Preparing your answer…</p>
        {slow && <p className="loader__hint">This can take a few minutes when the local model is loading.</p>}
      </div>
    </div>
  );
}
