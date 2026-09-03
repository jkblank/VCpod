import type { ReactNode } from 'react'
import type { IconProps } from './types'

// The "sources" set from the VCpod icon spec: same ring, but the hub
// area becomes the object -- a note flag for a playlist, a folder for
// an external library, and so on. See icons/StateIcons.tsx's header
// for the shared porting notes.

const ring = <circle cx="26" cy="26" r="23" stroke="var(--color-neutral-700)" strokeWidth="1.5" />

function svg(children: ReactNode, { size = 20, className }: IconProps = {}) {
  return (
    <svg width={size} height={size} viewBox="0 0 52 52" fill="none" className={className} aria-hidden="true">
      {children}
    </svg>
  )
}

export function StreamingPlaylistIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      <path
        d="M19 32V20.5l13-2.5V29"
        stroke="var(--color-accent)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="17" cy="32.5" r="2.6" stroke="var(--color-accent)" strokeWidth="2" />
      <circle cx="34.2" cy="30" r="2.6" stroke="var(--color-accent)" strokeWidth="2" />
    </>,
    props,
  )
}

export function VideoSourceIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      <rect x="15" y="19" width="22" height="14" rx="4" stroke="var(--color-accent)" strokeWidth="2.5" />
      <path d="M23.5 22.5 30.5 26l-7 3.5z" fill="var(--color-accent)" />
    </>,
    props,
  )
}

export function PodcastIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      <circle cx="26" cy="26" r="3" fill="var(--color-accent)" />
      <path
        d="M20.5 20.5a7.8 7.8 0 0 0 0 11M31.5 20.5a7.8 7.8 0 0 1 0 11"
        stroke="var(--color-accent)"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <path
        d="M16.5 16.5a13.4 13.4 0 0 0 0 19M35.5 16.5a13.4 13.4 0 0 1 0 19"
        stroke="var(--color-accent-600)"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </>,
    props,
  )
}

export function AudiobookIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      <path
        d="M26 20.5c-2.6-1.8-5.2-2.3-8.5-2.3v15c3.3 0 5.9.5 8.5 2.3 2.6-1.8 5.2-2.3 8.5-2.3v-15c-3.3 0-5.9.5-8.5 2.3z"
        stroke="var(--color-accent)"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />
      <path d="M26 20.5v15" stroke="var(--color-accent-600)" strokeWidth="2" />
    </>,
    props,
  )
}

export function ExternalLibraryIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      <path
        d="M15.5 33.5v-14h7l2.5 3h11.5v11z"
        stroke="var(--color-accent)"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />
    </>,
    props,
  )
}

export function CredentialsIcon(props: IconProps) {
  return svg(
    <>
      {ring}
      <path
        d="M26 33.5V21M21 26l5-5 5 5"
        stroke="var(--color-accent)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M17 17.5h18" stroke="var(--color-accent-600)" strokeWidth="2" strokeLinecap="round" />
    </>,
    props,
  )
}
