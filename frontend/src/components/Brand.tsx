// The MedLens lens mark (from the Figma "Icon/lens" component) and the wordmark.

interface LensProps {
  size?: number;
  ring?: string;
  handle?: string;
  className?: string;
}

export function LensIcon({ size = 32, ring = "var(--color-primary)", handle, className }: LensProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 80 80" fill="none" aria-hidden="true" focusable="false">
      <path
        d="M56.6667 33.3333C56.6667 46.22 46.22 56.6667 33.3333 56.6667C20.4467 56.6667 10 46.22 10 33.3333C10 20.4467 20.4467 10 33.3333 10C46.22 10 56.6667 20.4467 56.6667 33.3333Z"
        stroke={ring} strokeWidth="5.66667" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M50 50L73.3333 73.3333" stroke={handle ?? ring} strokeWidth="5.66667" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo({ size = 28, inverse = false }: { size?: number; inverse?: boolean }) {
  return (
    <span className={`logo${inverse ? " logo--inverse" : ""}`}>
      <LensIcon size={size} ring={inverse ? "#FFFFFF" : undefined} handle={inverse ? "var(--color-accent)" : undefined} />
      <span className="logo__word">MEDLENS</span>
    </span>
  );
}
