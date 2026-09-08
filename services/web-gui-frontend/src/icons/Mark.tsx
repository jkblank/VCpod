import type { IconProps } from './types'

// The VCpod mark itself: a 46px clickwheel ring, a 90° accent arc
// starting at twelve o'clock, and an 8.5px inner hub -- the shape
// every icon in this set is built from. See "VCpod Icons" (Claude
// Design project) for the full spec this was ported from.
export default function Mark({ size = 24, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 52 52"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <circle cx="26" cy="26" r="23" stroke="var(--color-neutral-700)" strokeWidth="1.5" />
      <path d="M26 3a23 23 0 0 1 23 23" stroke="var(--color-accent)" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="26" cy="26" r="8.5" stroke="var(--color-accent)" strokeWidth="2" />
    </svg>
  )
}
