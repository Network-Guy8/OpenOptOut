import { useCallback, useEffect, useState } from 'react'
import { Check, X, ExternalLink, RefreshCw, Globe } from 'lucide-react'
import api from '../api'
import { detailText } from '../errorMessage'

const statuses = ['pending', 'approved', 'dismissed', 'all']

export default function WebsiteCandidatesPanel() {
  const [status, setStatus] = useState('pending')
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [expanded, setExpanded] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const { data } = await api.get(
        `/privacy-pages/website-candidates?status=${status}`
      )
      setRows(data.candidates || [])
    } catch (e) {
      setError(detailText(e) || 'Unable to load website candidates.')
    } finally {
      setLoading(false)
    }
  }, [status])

  useEffect(() => {
    if (expanded) refresh()
  }, [expanded, refresh])

  const act = async (row, action) => {
    if (busy !== null) return

    if (action === 'approve') {
      const ok = window.confirm(
        `Approve this website for ${row.broker_name}?\n\n` +
        `${row.url}\n\n` +
        'Only approve if you have verified that this website belongs ' +
        'to the broker. Approval enables privacy-page discovery.'
      )
      if (!ok) return
    }

    setBusy(row.id)
    setError('')
    setMessage('')

    try {
      await api.post(
        `/privacy-pages/website-candidates/${row.id}/${action}`
      )
      setMessage(
        `${row.broker_name}: candidate ${action === 'approve'
          ? 'approved'
          : 'dismissed'}.`
      )
      await refresh()
    } catch (e) {
      setError(detailText(e) || `Could not ${action} candidate.`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="mt-6 bg-slate-800 rounded-xl border border-slate-700/50 overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-4">
        <button
          type="button"
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-2 text-white font-semibold text-sm"
        >
          <Globe size={17} className="text-shield-400" />
          Website Candidates
          <span className="text-slate-400 font-normal">
            {expanded ? '−' : '+'}
          </span>
        </button>

        {expanded && (
          <button
            type="button"
            onClick={refresh}
            disabled={loading || busy !== null}
            className="flex items-center gap-1.5 text-xs text-slate-300 hover:text-white disabled:opacity-50"
          >
            <RefreshCw size={13} />
            Refresh
          </button>
        )}
      </div>

      {expanded && (
        <div className="px-4 pb-4">
          <p className="text-xs text-slate-400 mb-3">
            Review discovered broker websites. Approval does not change
            existing opt-out URLs.
          </p>

          <div className="flex flex-wrap gap-2 mb-4">
            {statuses.map(s => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setStatus(s)
                  setMessage('')
                }}
                className={`px-3 py-1.5 rounded-lg text-xs border ${
                  status === s
                    ? 'bg-shield-900/40 border-shield-500 text-shield-300'
                    : 'border-slate-700 text-slate-400 hover:text-white'
                }`}
              >
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>

          {error && (
            <div role="alert" className="mb-3 p-3 rounded-lg bg-red-900/30 text-red-300 text-sm">
              {error}
            </div>
          )}

          {message && (
            <div role="status" className="mb-3 p-3 rounded-lg bg-emerald-900/20 text-emerald-300 text-sm">
              {message}
            </div>
          )}

          <div className="text-xs text-slate-400 mb-2">
            {loading ? 'Loading…' : `${rows.length} candidates`}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-slate-400 text-xs border-b border-slate-700">
                  <th className="text-left py-2 pr-3">Broker</th>
                  <th className="text-left py-2 pr-3">Website</th>
                  <th className="text-left py-2 pr-3">Source</th>
                  <th className="text-left py-2 pr-3">Status</th>
                  <th className="text-right py-2">Actions</th>
                </tr>
              </thead>

              <tbody>
                {rows.map(row => (
                  <tr key={row.id} className="border-b border-slate-700/40">
                    <td className="py-3 pr-3 text-slate-200">
                      <div>{row.broker_name}</div>
                      <div className="text-xs text-slate-500">
                        ID {row.id}
                      </div>
                    </td>

                    <td className="py-3 pr-3">
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-shield-400 hover:text-shield-300 inline-flex items-center gap-1 break-all"
                      >
                        {row.url}
                        <ExternalLink size={12} className="shrink-0" />
                      </a>
                    </td>

                    <td className="py-3 pr-3 text-xs text-slate-400">
                      {row.source || 'unknown'}
                    </td>

                    <td className="py-3 pr-3 text-xs text-slate-300">
                      {row.status}
                    </td>

                    <td className="py-3 text-right">
                      {row.status === 'pending' && (
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={() => act(row, 'approve')}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded border border-emerald-700 text-emerald-300 hover:bg-emerald-900/30 disabled:opacity-40 text-xs"
                          >
                            <Check size={12} />
                            Approve
                          </button>

                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={() => act(row, 'dismiss')}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded border border-red-800 text-red-300 hover:bg-red-900/30 disabled:opacity-40 text-xs"
                          >
                            <X size={12} />
                            Dismiss
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!loading && !error && rows.length === 0 && (
            <p className="text-slate-500 text-sm py-4 text-center">
              No candidates in this category.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
