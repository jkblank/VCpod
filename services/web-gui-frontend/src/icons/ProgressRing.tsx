import type { IconProps } from './types'

type ProgressRingProps = IconProps & {
  /** 0-100. The arc grows from twelve o'clock -- at 25% it's exactly the mark. */
  progress: number
  strokeWidth?: number
  showHub?: boolean
}

// Determinate progress from the VCpod icon spec: same ring/hub as the
// mark, with an accent arc whose length tracks `progress` via
// stroke-dashoffset (pathLength=100 makes the math trivial -- offset
// is just 100 - progress). Turns accent-300 once complete, same as the
// spec's own `ringColor` rule.
export default function ProgressRing({
  progress,
  size = 24,
  strokeWidth = 2.5,
  showHub = true,
  className,
}: ProgressRingProps) {
  const clamped = Math.max(0, Math.min(100, progress))
  const ringColor = clamped >= 100 ? 'var(--color-accent-300)' : 'var(--color-accent)'
  return (
    <svg width={size} height={size} viewBox="0 0 52 52" fill="none" className={className} aria-hidden="true">
      <circle cx="26" cy="26" r="23" stroke="var(--color-neutral-800)" strokeWidth={strokeWidth} />
      <circle
        cx="26"
        cy="26"
        r="23"
        stroke={ringColor}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        pathLength={100}
        strokeDasharray={100}
        strokeDashoffset={100 - clamped}
        style={{ transformOrigin: '26px 26px', transform: 'rotate(-90deg)', transition: 'stroke-dashoffset 120ms linear' }}
      />
      {showHub && <circle cx="26" cy="26" r="8.5" stroke="var(--color-accent-700)" strokeWidth="2" />}
    </svg>
  )
}
