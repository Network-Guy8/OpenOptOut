import { useCallback, useEffect, useState } from 'react'
import api from '../api'
import { useAuth, can } from '../hooks/useAuth'
import { tx } from '../i18n/tx'
import { fmtDate, fmtDateTime } from '../i18n/format'

const button = 'px-3 py-2 rounded-lg border border-slate-600 text-sm text-slate-200 disabled:opacity-50'

export default function BreachMonitor({ memberId }) {
  const { user } = useAuth()
  const [data, setData] = useState(null)
  const [ids, setIds] = useState([])
  const [authorized, setAuthorized] = useState(false)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const load = useCallback(async () => {
    const { data: result } = await api.get(`/breach-monitor/${memberId}`)
    setData(result)
    return result
  }, [memberId])

  useEffect(() => {
    let alive = true
    setData(null); setIds([]); setError(false); setAuthorized(false); setKey('')
    api.get(`/breach-monitor/${memberId}`).then(({ data: result }) => {
      if (alive) { setData(result); setIds(result.identity_ids) }
    }).catch(() => { if (alive) setError(true) })
    return () => { alive = false }
  }, [memberId])
  useEffect(() => {
    if (!data?.running) return
    const timer = setTimeout(() => { load().catch(() => setError(true)) }, 4000)
    return () => clearTimeout(timer)
  }, [data, load])

  const act = async (kind) => {
    setBusy(true); setError(false)
    try {
      if (kind === 'key') {
        await api.put('/breach-monitor/key', { api_key: key.trim() })
        setKey('')
      } else if (kind === 'run') {
        await api.post(`/breach-monitor/${memberId}/run`)
      } else {
        await api.put(`/breach-monitor/${memberId}`, { identity_ids: kind === 'stop' ? [] : ids, authorized })
        if (kind === 'stop') { setIds([]); setAuthorized(false) }
      }
      await load()
    } catch { setError(true) }
    finally { setBusy(false) }
  }

  return <div className="mt-5 pt-4 border-t border-slate-700/50">
    <h3 className="text-slate-200 font-medium">{tx("Email breach monitoring")}</h3>
    {data && <p className="text-xs text-slate-400">{data.enabled ? tx("Enabled") : tx("Disabled")}</p>}
    <p className="text-xs text-slate-400 mt-2">{tx("Selected emails are sent to Have I Been Pwned for weekly checks. Phone numbers are not searched. Breached data is not removed.")}</p>
    <a href="https://haveibeenpwned.com/NotifyMe" target="_blank" rel="noopener noreferrer" className="text-sm text-shield-300">{tx("Free breach notifications")}</a>
    {error && <p role="alert" className="text-amber-300 text-sm mt-2">{tx("Could not save or check. Check your permissions, API key and cooldown, then try again.")}</p>}
    {!data ? <p className="text-sm text-slate-400">{tx("Loading…")}</p> : <>
      {!data.configured && <p className="text-sm text-amber-300 mt-2">{tx("An HIBP subscription API key is required for automated checks.")}</p>}
      {can(user, 'settings.system') && <div className="flex flex-wrap items-end gap-2 mt-3">
        <label className="text-sm text-slate-300">{tx("HIBP API key")}
          <input type="password" autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)}
            className="block bg-slate-900 border border-slate-600 rounded p-2" />
        </label>
        <button type="button" className={button} disabled={busy || !/^[a-f0-9]{32}$/i.test(key.trim())} onClick={() => act('key')}>{tx("Save")}</button>
      </div>}
      <fieldset className="mt-3 space-y-2" disabled={busy}>
        <legend className="text-sm text-slate-300">{tx("Choose email addresses")}</legend>
        {data.emails.map(e => <label key={e.id} className="flex gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={ids.includes(e.id)} onChange={event => setIds(previous => event.target.checked ? [...previous, e.id] : previous.filter(id => id !== e.id))} />{e.email}
        </label>)}
        <label className="flex gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={authorized} onChange={e => setAuthorized(e.target.checked)} />{tx("I am authorized to monitor these email addresses.")}
        </label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={button} disabled={!authorized || !ids.length || ids.length > 20} onClick={() => act('save')}>{tx("Save monitoring choices")}</button>
          <button type="button" className={button} disabled={data.running || !data.enabled || !data.configured} onClick={() => act('run')}>{tx("Check now")}</button>
          <button type="button" className={button} disabled={!data.enabled} onClick={() => act('stop')}>{tx("Stop monitoring and clear findings")}</button>
        </div>
      </fieldset>
      {data.running && <p role="status" className="text-sm text-slate-300 mt-2">{tx("Checking…")}</p>}
      {data.error && <p role="alert" className="text-sm text-amber-300 mt-2">{tx("Check failed. Previous findings are retained.")}</p>}
      {data.last_success && <p className="text-sm text-slate-400 mt-2">{tx("Last successful check")}: {fmtDateTime(data.last_success)}</p>}
      {data.last_success && !data.findings.length && <p className="text-sm text-slate-300 mt-2">{tx("No known breaches in the last completed check. This does not guarantee your data is safe.")}</p>}
      <ul className="mt-3 divide-y divide-slate-700">
        {data.findings.map(f => <li key={`${f.identity_id}:${f.name}`} className="py-2 text-sm text-slate-300">
          <a className="text-shield-300" href={`https://haveibeenpwned.com/PwnedWebsites#${encodeURIComponent(f.name)}`} target="_blank" rel="noopener noreferrer">{f.title}</a>
          <span className="block text-xs text-slate-400">{data.emails.find(e => e.id === f.identity_id)?.email} · {f.date ? fmtDate(f.date) : ''}</span>
        </li>)}
      </ul>
    </>}
  </div>
}
