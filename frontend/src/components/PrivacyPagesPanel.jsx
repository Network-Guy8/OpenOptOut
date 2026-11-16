import { useEffect, useState, useCallback, useMemo } from 'react'
import {
  ShieldQuestion, ExternalLink, X, Check, RefreshCw,
  Search
} from 'lucide-react'
import api from '../api'
import { tx } from '../i18n/tx'
import { detailText } from '../errorMessage'
import { isSafeHref } from './Markdown'

const KIND = {
  hosted_onetrust: 'OneTrust request form',
  hosted_trustarc: 'TrustArc request form',
  hosted_other: 'Hosted privacy form',
  request_form: 'Request form',
  optout_page: 'Opt-out page',
  policy: 'Privacy policy',
  email: 'Privacy email',
}

function normalizedUrl(value) {
  try {
    let url = (value || '').trim()

    if (url.toLowerCase().startsWith('mailto:')) {
      let address = decodeURIComponent(url.slice(7).split('?')[0])
        .toLowerCase()
        .trim()
      address = address.replace(
        /^(?:u003e)+(?=(?:privacy|dpo|optout|opt-out|dsar|ccpa|gdpr|removal|remove|delete|legal))/i,
        ''
      )
      return 'mailto:' + address
    }

    const parsed = new URL(url)
    if (parsed.protocol !== 'https:') return url

    parsed.hash = ''
    if (parsed.pathname !== '/') {
      parsed.pathname = parsed.pathname.replace(/\/+$/, '')
    }

    return parsed.toString().replace(/\/$/, '')
  } catch {
    return value || ''
  }
}

function category(page) {
  // Backend eligibility is separate from URL-based discovery.
  if (page.fillable === true) return 'eligible'

  const url = normalizedUrl(page.url)
  const path = (() => {
    try {
      return new URL(url).pathname.toLowerCase()
    } catch {
      return ''
    }
  })()

  if (page.kind === 'email') return 'review'

  if (page.kind.startsWith('hosted_')) return 'review'

  if (path === '/' || path === '') return 'informational'

  if (/privacy-policy|privacy-notice|privacy\.html|resources-privacy|\/legal\/privacy/.test(path) &&
      !/request-removal|\/remove|opt.?out/.test(path)) {
    return 'informational'
  }

  if (/opt.?out|unlist|request-removal|personaldata\/optout|privacy-rights-request|do-not-sell|do-not-share|\/removal|\/remove(?:\/|$)/.test(path)) {
    return 'actionable'
  }

  if (/privacy-rights|privacy-choices|privacy-center|privacy\/control|control-your-profile|privacy-request/.test(path)) {
    return 'review'
  }

  if (page.kind === 'policy') return 'informational'

  return 'review'
}

const CATEGORY = {
  eligible: {
    label: 'Automation eligible',
    color: 'text-emerald-300',
    order: 0,
  },
  actionable: {
    label: 'Likely removal endpoint',
    color: 'text-sky-300',
    order: 1,
  },
  review: {
    label: 'Needs review',
    color: 'text-amber-300',
    order: 2,
  },
  informational: {
    label: 'Informational',
    color: 'text-slate-400',
    order: 3,
  },
}

function groupPages(pages) {
  const groups = new Map()

  for (const page of pages) {
    const key = `${page.broker_id}|${normalizedUrl(page.url)}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(page)
  }

  return [...groups.values()].map(rows => {
    // Keep all underlying records. Prefer eligible, then approved, then removal.
    rows.sort((a, b) => {
      const eligible = Number(b.fillable === true) -
                       Number(a.fillable === true)
      if (eligible) return eligible

      const approved = Number(b.status === 'approved') -
                       Number(a.status === 'approved')
      if (approved) return approved

      const priority = CATEGORY[category(a)].order -
                       CATEGORY[category(b)].order
      if (priority) return priority

      return a.id - b.id
    })

    return {
      primary: rows[0],
      records: rows,
      count: rows.length,
    }
  })
}

export default function PrivacyPagesPanel({ refreshKey }) {
  const [data, setData] = useState(null)
  const [msg, setMsg] = useState('')
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [showDuplicates, setShowDuplicates] = useState(false)
  const [busyId, setBusyId] = useState(null)

  const load = useCallback(() => {
    api.get('/privacy-pages')
      .then(r => setData(r.data))
      .catch(e => {
        setMsg(detailText(e) || 'Unable to load privacy pages.')
      })
  }, [])

  useEffect(() => { load() }, [load, refreshKey])

  useEffect(() => {
    if (!data?.running) return
    const timer = setTimeout(load, 5000)
    return () => clearTimeout(timer)
  }, [data, load])

  const act = async (page, action) => {
    if (busyId !== null) return

    setBusyId(page.id)
    setMsg('')

    try {
      const { data: result } = await api.post(
        `/privacy-pages/${page.id}/${action}`
      )
      if (result.message) setMsg(result.message)
      await load()
    } catch (e) {
      setMsg(detailText(e) || tx("Couldn't save that. Please try again."))
    } finally {
      setBusyId(null)
    }
  }

  const groups = useMemo(
    () => groupPages(data?.pages || []),
    [data]
  )

  const counts = useMemo(() => {
    const result = {
      all: groups.length,
      eligible: 0,
      actionable: 0,
      review: 0,
      informational: 0,
    }

    for (const group of groups) {
      result[category(group.primary)]++
    }

    return result
  }, [groups])

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase()

    return groups.filter(group => {
      const page = group.primary

      if (filter !== 'all' && category(page) !== filter) {
        return false
      }

      if (!term) return true

      return group.records.some(p =>
        `${p.broker_name} ${p.url} ${p.kind} ${p.label || ''}`
          .toLowerCase()
          .includes(term)
      )
    }).sort((a, b) => {
      const priority =
        CATEGORY[category(a.primary)].order -
        CATEGORY[category(b.primary)].order

      if (priority) return priority

      return a.primary.broker_name.localeCompare(
        b.primary.broker_name
      )
    })
  }, [groups, filter, search])

  if (!data) return null

  return (
    <section
      aria-labelledby="pp-h"
      className="mt-6 bg-slate-800 rounded-xl border border-slate-700/50 p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h2
          id="pp-h"
          className="text-slate-200 text-sm font-medium flex items-center gap-2"
        >
          <ShieldQuestion
            size={15}
            className="text-shield-400"
            aria-hidden="true"
          />
          {tx("Privacy pages found on brokers' websites")}
        </h2>

        <button
          type="button"
          onClick={load}
          className="text-xs text-shield-400 hover:text-shield-300 flex items-center gap-1"
        >
          <RefreshCw size={13} aria-hidden="true" />
          {tx('Refresh')}
        </button>
      </div>

      <p className="text-slate-400 text-xs mt-2">
        Automation eligible means the backend permits a discovered form
        to be selected for automated processing; it does not guarantee
        successful or safe submission. Other categories are URL-based
        estimates, not verification. Email opt-outs require admin approval.
      </p>

      {data.running && (
        <p className="text-xs text-slate-400 mt-2" role="status">
          {tx('Looking…')}
        </p>
      )}

      {msg && (
        <p role="status" className="text-xs text-shield-300 mt-2">
          {msg}
        </p>
      )}

      <div className="flex flex-wrap gap-2 mt-4">
        {[
          ['all', 'All'],
          ['eligible', 'Automation eligible'],
          ['actionable', 'Likely removal'],
          ['review', 'Needs review'],
          ['informational', 'Informational'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setFilter(key)}
            aria-pressed={filter === key}
            className={
              'rounded-lg px-3 py-1.5 text-xs border ' +
              (filter === key
                ? 'bg-shield-600/30 border-shield-500 text-white'
                : 'border-slate-600 text-slate-300 hover:bg-slate-700')
            }
          >
            {label} ({counts[key]})
          </button>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-2 rounded-lg border border-slate-600 px-3">
        <Search size={14} className="text-slate-400" aria-hidden="true" />
        <input
          type="search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search broker or URL"
          aria-label="Search privacy discoveries"
          className="bg-transparent text-sm text-slate-100 py-2 outline-none w-full"
        />
      </div>

      <label className="mt-3 flex items-center gap-2 text-xs text-slate-400">
        <input
          type="checkbox"
          checked={showDuplicates}
          onChange={e => setShowDuplicates(e.target.checked)}
        />
        Show individual records in duplicate groups
      </label>

      <p className="text-xs text-slate-500 mt-3">
        Showing {visible.length} of {groups.length} grouped discoveries
        ({data.pages.length} underlying records).
      </p>

      {visible.length === 0 ? (
        <p className="text-slate-500 text-sm mt-4">
          No discoveries match the current filters.
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-slate-700/40">
          {visible.map(group => {
            const p = group.primary
            const cat = CATEGORY[category(p)]

            const renderActions = page => (
              <div className="flex gap-3 shrink-0 flex-wrap">
                {page.kind !== 'email' && isSafeHref(page.url) && (
                  <a
                    href={page.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-shield-400 hover:text-shield-300 text-xs flex items-center gap-1"
                  >
                    <ExternalLink size={12} aria-hidden="true" />
                    {tx('Open')}
                  </a>
                )}

                {page.kind === 'email' &&
                 page.status !== 'approved' &&
                 page.status !== 'dismissed' && (
                  <button
                    type="button"
                    disabled={busyId !== null}
                    onClick={() => act(page, 'approve-email')}
                    className="text-emerald-300 hover:text-emerald-200 text-xs flex items-center gap-1 disabled:opacity-50"
                  >
                    <Check size={12} aria-hidden="true" />
                    {tx('Use for email opt-outs')}
                  </button>
                )}

                {page.status !== 'dismissed' && (
                  <button
                    type="button"
                    disabled={busyId !== null}
                    onClick={() => act(page, 'dismiss')}
                    className="text-slate-400 hover:text-red-300 text-xs flex items-center gap-1 disabled:opacity-50"
                  >
                    <X size={12} aria-hidden="true" />
                    {tx('Not it')}
                  </button>
                )}
              </div>
            )

            return (
              <li key={`${p.broker_id}-${normalizedUrl(p.url)}`} className="py-3">
                <div className="flex justify-between items-start gap-3 flex-wrap">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-slate-200">
                      <span className="font-medium">{p.broker_name}</span>
                      <span className="text-slate-400">
                        {' · '}{KIND[p.kind] || p.kind}
                      </span>
                    </div>

                    <div className={`text-xs mt-1 ${cat.color}`}>
                      {cat.label}
                      {p.status === 'approved' && (
                        <span className="ms-2 text-emerald-300">
                          · Admin approved
                        </span>
                      )}
                      {p.fillable && (
                        <span className="ms-2 text-slate-400">
                          · Not submission-verified
                        </span>
                      )}
                    </div>

                    <div className="text-xs text-slate-400 break-all mt-1">
                      {p.kind === 'email'
                        ? normalizedUrl(p.url).replace(/^mailto:/, '')
                        : p.url}
                    </div>

                    {group.count > 1 && (
                      <p className="text-xs text-amber-300 mt-1">
                        {group.count} records share this normalized URL
                      </p>
                    )}
                  </div>

                  {renderActions(p)}
                </div>

                {showDuplicates && group.count > 1 && (
                  <div className="mt-2 ms-3 ps-3 border-s border-slate-600 space-y-2">
                    {group.records.map(record => (
                      <div
                        key={record.id}
                        className="text-xs text-slate-400 flex justify-between gap-3 flex-wrap"
                      >
                        <div className="min-w-0">
                          <div>
                            Record #{record.id} · {KIND[record.kind] || record.kind}
                            {' · '}{record.status}
                          </div>
                          <div className="break-all">{record.url}</div>
                        </div>
                        {renderActions(record)}
                      </div>
                    ))}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
