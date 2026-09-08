import type { ReactNode } from 'react'

// Shared modal shell (backdrop + .dialog card) -- every confirm/review
// flow in the app (View YAML, save-diff review, execute-sync confirm)
// renders through this instead of rolling its own overlay.
export default function Dialog({
  title,
  onClose,
  actions,
  children,
}: {
  title: string
  onClose: () => void
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-title">{title}</div>
        <div className="dialog-body">{children}</div>
        {actions && <div className="dialog-actions">{actions}</div>}
      </div>
    </div>
  )
}
