import type { ReactNode } from 'react'
import type { IconProps } from './types'

// The "states" set from the VCpod icon spec: every icon keeps the same
// 46px ring and replaces only the arc/hub to say where the work is --
// a full quadrant for active, absent for idle, dashed for unreachable.
// Ported 1:1 from the "VCpod Icons" Claude Design project (states
// section) -- geometry/colors intentionally not parameterized beyond
// size, to stay a faithful copy of the spec rather than a reinterpretation.

const ring = <circle cx="26" cy="26" r="23" stroke="var(--color-neutral-700)" strokeWidth="1.5" />

function arc(color: string) {
  return <path d="M26 3a23 23 0 0 1 23 23" stroke={color} strokeWidth="2.5" strokeLinecap="round" />
}

function svg(children: ReactNode, { size = 20, className }: IconProps = {}) {
  return (
    <svg width={size} height={size} viewBox="0 0 52 52" fill="none" className={className} aria-hidden="true">
      {children}
    </svg>
  )
}

export function SyncedIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-accent)')}
      <path
        d="M20.5 26.5 24.5 30.5 32 22.5"
        stroke="var(--color-accent)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>,
    props,
  )
}

export function SyncingIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      <g style={{ transformOrigin: '26px 26px', animation: 'vc-spin 1.6s linear infinite' }}>
        {arc('var(--color-accent)')}
      </g>
      <circle cx="26" cy="26" r="8.5" stroke="var(--color-accent-600)" strokeWidth="2" />
    </>,
    props,
  )
}

export function QueuedIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-neutral-600)')}
      <circle cx="18.5" cy="26" r="2" fill="var(--color-accent-500)" />
      <circle cx="26" cy="26" r="2" fill="var(--color-accent-500)" />
      <circle cx="33.5" cy="26" r="2" fill="var(--color-accent-500)" />
    </>,
    props,
  )
}

export function PausedIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-neutral-600)')}
      <path d="M22.5 20.5v11M29.5 20.5v11" stroke="var(--color-neutral-300)" strokeWidth="2.5" strokeLinecap="round" />
    </>,
    props,
  )
}

export function NeedsAttentionIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-accent-400)')}
      <path d="M26 19v8.5" stroke="var(--color-accent-200)" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="26" cy="32.5" r="1.6" fill="var(--color-accent-200)" />
    </>,
    props,
  )
}

export function UnreachableIcon(props: IconProps) {
  return svg(
    <>
      <circle cx="26" cy="26" r="23" stroke="var(--color-neutral-700)" strokeWidth="1.5" strokeDasharray="4 4" />
      <path d="M19 19l14 14M33 19 19 33" stroke="var(--color-neutral-500)" strokeWidth="2.5" strokeLinecap="round" />
    </>,
    props,
  )
}

export function DeviceConnectedIcon(props: IconProps) {
  return svg(
    <>
      <circle cx="26" cy="26" r="23" stroke="var(--color-accent-700)" strokeWidth="1.5" />
      {arc('var(--color-accent)')}
      <circle cx="26" cy="26" r="8.5" fill="var(--color-accent)" />
    </>,
    props,
  )
}

export function IdleIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-neutral-600)')}
      <path d="M17.5 26h17" stroke="var(--color-neutral-400)" strokeWidth="2.5" strokeLinecap="round" />
    </>,
    props,
  )
}

export function ScheduledIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-accent)')}
      <path
        d="M26 18v9.5l6.5 3.5"
        stroke="var(--color-accent-300)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>,
    props,
  )
}

export function ToAddIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-accent)')}
      <path
        d="M26 32.5V19M21.5 23.5 26 19l4.5 4.5"
        stroke="var(--color-accent-200)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>,
    props,
  )
}

export function ToRemoveIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-neutral-600)')}
      <path
        d="M26 19v13.5M21.5 28l4.5 4.5 4.5-4.5"
        stroke="var(--color-neutral-300)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>,
    props,
  )
}

export function ProfileIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      {arc('var(--color-accent)')}
      <path d="M19 29.5c0-3.9 3.1-7 7-7s7 3.1 7 7" stroke="var(--color-accent-300)" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="26" cy="19.5" r="2.4" fill="var(--color-accent-300)" />
    </>,
    props,
  )
}
