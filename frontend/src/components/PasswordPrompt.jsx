import { useEffect, useRef, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import Modal from './Modal'
import { tx } from '../i18n/tx'
import { detailText } from '../errorMessage'

export default function PasswordPrompt({
  isOpen,
  title,
  onConfirm,
  onCancel,
  autoComplete = 'current-password',
}) {
  const [password, setPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    if (!isOpen) return

    setPassword('')
    setVisible(false)
    setError('')
    setBusy(false)

    const frame = requestAnimationFrame(() => {
      inputRef.current?.focus()
    })

    return () => cancelAnimationFrame(frame)
  }, [isOpen])

  const cancel = () => {
    if (busy) return

    setPassword('')
    setVisible(false)
    setError('')
    onCancel?.()
  }

  const submit = async e => {
    e.preventDefault()

    if (!password || busy) return

    setBusy(true)
    setError('')

    try {
      await onConfirm(password)

      setPassword('')
      setVisible(false)
      onCancel?.()
    } catch (err) {
      setError(
        detailText(err) ||
        err?.message ||
        tx('Operation failed.')
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={busy ? undefined : cancel}
      showClose={!busy}
      title={title || tx('Confirm password')}
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="relative">
          <input
            ref={inputRef}
            type={visible ? 'text' : 'password'}
            autoComplete={autoComplete}
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder={tx('Enter password…')}
            aria-label={tx('Password')}
            className="w-full rounded-lg bg-slate-800 border border-slate-600 px-3 py-2 pe-10 text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            required
            disabled={busy}
          />

          <button
            type="button"
            onClick={() => setVisible(v => !v)}
            disabled={busy}
            aria-label={
              visible
                ? tx('Hide password')
                : tx('Show password')
            }
            className="absolute end-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white disabled:opacity-40"
          >
            {visible
              ? <EyeOff size={17} />
              : <Eye size={17} />}
          </button>
        </div>

        {error && (
          <p role="alert" className="text-red-400 text-sm">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={cancel}
            disabled={busy}
            className="px-4 py-2 rounded-lg text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            {tx('Cancel')}
          </button>

          <button
            type="submit"
            disabled={!password || busy}
            className="px-4 py-2 rounded-lg bg-shield-600 hover:bg-shield-700 text-white disabled:opacity-40"
          >
            {busy ? tx('Saving…') : tx('Confirm')}
          </button>
        </div>
      </form>
    </Modal>
  )
}
