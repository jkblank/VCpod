// The big, explicit warning every credential-capture form shows before
// its input, not a tooltip or a collapsed disclosure. Same wording for
// all three forms (Apple Music/YouTube cookies, Pocket Casts login) --
// only the storage path differs.
export default function CredentialWarning({ path }: { path: string }) {
  return (
    <div className="warning-banner">
      <strong>This is stored in plain text on this server's disk, unencrypted</strong> — at{' '}
      <code>{path}</code>. Anyone with filesystem access to this machine can read it back out.
      Only use this on a machine you trust, on <code>localhost</code> or your own private LAN —
      never expose this web UI to the open internet.
    </div>
  )
}
