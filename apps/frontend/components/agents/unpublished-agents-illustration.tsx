/**
 * Artwork for the employee empty state of a tenant whose administrator has
 * published nothing yet: an empty marketplace slot with a dormant agent in it.
 * The accessible name carries the fact the drawing stands for, because the
 * illustration is the only shape-level cue an employee sees.
 */
export function UnpublishedAgentsIllustration() {
  return (
    <svg
      role="img"
      aria-label="尚未发布智能体"
      focusable="false"
      viewBox="0 0 160 120"
      className="h-28 w-36 text-muted-foreground"
      fill="none"
    >
      <rect
        x="26"
        y="16"
        width="108"
        height="76"
        rx="12"
        stroke="currentColor"
        strokeDasharray="6 6"
      />
      <rect x="58" y="40" width="44" height="34" rx="10" stroke="currentColor" />
      <circle cx="72" cy="55" r="3" fill="currentColor" />
      <circle cx="88" cy="55" r="3" fill="currentColor" />
      <path d="M70 66h20" stroke="currentColor" strokeLinecap="round" />
      <path d="M80 40v-6" stroke="currentColor" strokeLinecap="round" />
      <circle cx="80" cy="29" r="3" fill="currentColor" />
      <path d="M62 104h36" stroke="currentColor" strokeLinecap="round" strokeDasharray="4 6" />
    </svg>
  );
}
