import type { IconProps } from './types'

// The three throbber variants from the VCpod icon spec. Quadrant is the
// default -- "any wait under a few seconds: button spinners, table
// refresh" (the spec's own words) -- Drawing/CounterRotating are for
// longer/full-screen waits and are exported for completeness.
//
// The design's own three reference sizes (44/24/16px) drop the stroke
// width down and drop the hub entirely at 20px and under ("small sizes:
// at 20px and under, strokes go to 3-5px and the hub drops out") --
// bucketed the same way here rather than continuously interpolated, to
// stay a faithful copy of the three drawn examples.
function strokeForSize(size: number): { stroke: number; hub: boolean } {
  if (size >= 32) return { stroke: 2.5, hub: true }
  if (size >= 20) return { stroke: 4, hub: false }
  return { stroke: 5, hub: false }
}

export function Spinner({ size = 20, className }: IconProps) {
  const { stroke, hub } = strokeForSize(size)
  return (
    <svg width={size} height={size} viewBox="0 0 52 52" fill="none" className={className} aria-hidden="true">
      <circle cx="26" cy="26" r="23" stroke="var(--color-neutral-800)" strokeWidth={stroke} />
      <g style={{ transformOrigin: '26px 26px', animation: 'vc-spin 1.4s cubic-bezier(0.5,0.1,0.5,0.9) infinite' }}>
        <path
          d="M26 3a23 23 0 0 1 23 23"
          stroke="var(--color-accent)"
          strokeWidth={stroke}
          strokeLinecap="round"
        />
      </g>
      {hub && <circle cx="26" cy="26" r="8.5" stroke="var(--color-accent-700)" strokeWidth="2" />}
    </svg>
  )
}

export function DrawingSpinner({ size = 20, className }: IconProps) {
  const { stroke, hub } = strokeForSize(size)
  return (
    <svg width={size} height={size} viewBox="0 0 52 52" fill="none" className={className} aria-hidden="true">
      <circle cx="26" cy="26" r="23" stroke="var(--color-neutral-800)" strokeWidth={stroke} />
      <circle
        cx="26"
        cy="26"
        r="23"
        stroke="var(--color-accent)"
        strokeWidth={stroke}
        strokeLinecap="round"
        pathLength={145}
        style={{ transformOrigin: '26px 26px', transform: 'rotate(-90deg)', animation: 'vc-dash 1.8s ease-in-out infinite' }}
      />
      {hub && <circle cx="26" cy="26" r="8.5" stroke="var(--color-accent-700)" strokeWidth="2" />}
    </svg>
  )
}

export function CounterRotatingSpinner({ size = 44, className }: IconProps) {
  const { stroke } = strokeForSize(size)
  return (
    <svg width={size} height={size} viewBox="0 0 52 52" fill="none" className={className} aria-hidden="true">
      <g style={{ transformOrigin: '26px 26px', animation: 'vc-spin 2.6s linear infinite' }}>
        <path d="M26 3a23 23 0 0 1 23 23" stroke="var(--color-accent-600)" strokeWidth={stroke - 0.5} strokeLinecap="round" />
      </g>
      <g style={{ transformOrigin: '26px 26px', animation: 'vc-spin-rev 1.7s linear infinite' }}>
        <path d="M26 10.5A15.5 15.5 0 0 1 41.5 26" stroke="var(--color-accent)" strokeWidth={stroke} strokeLinecap="round" />
      </g>
      <circle cx="26" cy="26" r="4" fill="var(--color-accent)" style={{ animation: 'vc-breathe 1.7s ease-in-out infinite' }} />
    </svg>
  )
}
