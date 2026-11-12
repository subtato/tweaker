export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="tweaker-logo" x1="10" y1="8" x2="55" y2="57" gradientUnits="userSpaceOnUse">
          <stop stopColor="#c4b5fd" />
          <stop offset=".5" stopColor="#8b5cf6" />
          <stop offset="1" stopColor="#f472b6" />
        </linearGradient>
      </defs>
      <path
        d="M9.5 32C15.4 20.7 23.6 15 32 15s16.6 5.7 22.5 17C48.6 43.3 40.4 49 32 49S15.4 43.3 9.5 32Z"
        stroke="url(#tweaker-logo)"
        strokeWidth="3.5"
        strokeLinejoin="round"
      />
      <circle cx="32" cy="32" r="7.5" fill="url(#tweaker-logo)" />
      <path
        d="m49 12 1.9 4.6L55.5 18l-4.6 1.9L49 24.5l-1.9-4.6L42.5 18l4.6-1.4L49 12Z"
        fill="#fbbf24"
      />
    </svg>
  );
}

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`logo ${compact ? "logo--compact" : ""}`}>
      <LogoMark size={compact ? 30 : 34} />
      {!compact && (
        <div className="logo__text">
          <span className="logo__name">Tweaker</span>
          <span className="logo__tag">Seer service</span>
        </div>
      )}
    </div>
  );
}
