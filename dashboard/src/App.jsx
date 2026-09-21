import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { defaultKeeper, findDuplicateGroups } from './duplicates.js'

const API = 'http://127.0.0.1:8000'

const STATUSES = ['NEW', 'REVIEWED', 'SKIP', 'MISMATCH', 'EXP_GAP', 'EXPIRED', 'DUPLICATE']
// Full triage list for the filter dropdown (APPLIED rows live in the
// Applications tab; REJECTED is set only from there).
const ALL_STATUSES = ['NEW', 'REVIEWED', 'APPLIED', 'SKIP', 'REJECTED', 'MISMATCH', 'EXP_GAP', 'EXPIRED', 'DUPLICATE']
// "No, I didn't apply" reasons offered in the post-Apply confirm strip.
const NO_APPLY_REASONS = ['SKIP', 'EXP_GAP', 'MISMATCH', 'EXPIRED']
const SOURCES = ['indeed', 'linkedin', 'jobstreet', 'glassdoor', 'google', 'greenhouse', 'lever']

// Hiring-funnel stages for APPLIED rows (separate axis from triage
// status; a row carries a stage ⟺ its status is APPLIED).
const STAGES = ['APPLIED', 'INITIAL', 'TECHNICAL', 'FINAL', 'OFFER', 'ACCEPTED', 'DECLINED', 'OUT']
const INTERVIEW_STAGES = ['INITIAL', 'TECHNICAL', 'FINAL', 'OUT']
const OFFER_STAGES = ['OFFER', 'ACCEPTED', 'DECLINED']

const STAGE_LABELS = {
  APPLIED: 'Applied',
  INITIAL: 'Initial',
  TECHNICAL: 'Technical',
  FINAL: 'Final',
  OFFER: 'Offer',
  ACCEPTED: 'Accepted',
  DECLINED: 'Declined (you said no)',
  OUT: 'Out (they said no)',
}

const STAGE_STYLES = {
  APPLIED: 'bg-emerald-100 text-emerald-700 ring-emerald-200 dark:bg-emerald-900 dark:text-emerald-300 dark:ring-emerald-800',
  INITIAL: 'bg-sky-100 text-sky-700 ring-sky-200 dark:bg-sky-900 dark:text-sky-300 dark:ring-sky-800',
  TECHNICAL: 'bg-indigo-100 text-indigo-700 ring-indigo-200 dark:bg-indigo-900 dark:text-indigo-300 dark:ring-indigo-800',
  FINAL: 'bg-blue-100 text-blue-700 ring-blue-200 dark:bg-blue-900 dark:text-blue-300 dark:ring-blue-800',
  OUT: 'bg-neutral-300 text-neutral-700 ring-neutral-400 dark:bg-neutral-700 dark:text-neutral-300 dark:ring-neutral-600',
  OFFER: 'bg-yellow-100 text-yellow-700 ring-yellow-200 dark:bg-yellow-900 dark:text-yellow-300 dark:ring-yellow-800',
  ACCEPTED: 'bg-emerald-600 text-white ring-emerald-600 dark:bg-emerald-500 dark:ring-emerald-500',
  DECLINED: 'bg-neutral-200 text-neutral-500 ring-neutral-300 dark:bg-neutral-800 dark:text-neutral-400 dark:ring-neutral-700',
}

const STATUS_STYLES = {
  NEW: 'bg-neutral-800 text-white ring-neutral-800 dark:bg-neutral-100 dark:text-neutral-900 dark:ring-neutral-100',
  REVIEWED: 'bg-amber-100 text-amber-700 ring-amber-200 dark:bg-amber-900 dark:text-amber-300 dark:ring-amber-800',
  APPLIED: 'bg-emerald-100 text-emerald-700 ring-emerald-200 dark:bg-emerald-900 dark:text-emerald-300 dark:ring-emerald-800',
  SKIP: 'bg-neutral-200 text-neutral-600 ring-neutral-300 dark:bg-neutral-700 dark:text-neutral-300 dark:ring-neutral-600',
  REJECTED: 'bg-rose-100 text-rose-700 ring-rose-200 dark:bg-rose-900 dark:text-rose-300 dark:ring-rose-800',
  MISMATCH: 'bg-orange-100 text-orange-700 ring-orange-200 dark:bg-orange-900 dark:text-orange-300 dark:ring-orange-800',
  EXP_GAP: 'bg-violet-100 text-violet-700 ring-violet-200 dark:bg-violet-900 dark:text-violet-300 dark:ring-violet-800',
  EXPIRED: 'bg-neutral-400 text-white ring-neutral-400 dark:bg-neutral-600 dark:text-neutral-200 dark:ring-neutral-600',
  DUPLICATE: 'bg-neutral-200 text-neutral-500 ring-neutral-300 line-through dark:bg-neutral-800 dark:text-neutral-400 dark:ring-neutral-700',
}

// Negative-status hide pills: active (hiding) color per status.
const HIDE_ACTIVE_STYLES = {
  SKIP: 'bg-neutral-500 text-white ring-neutral-500',
  REJECTED: 'bg-rose-600 text-white ring-rose-600 dark:bg-rose-500 dark:ring-rose-500',
  MISMATCH: 'bg-orange-500 text-white ring-orange-500 dark:bg-orange-500 dark:ring-orange-500',
  EXP_GAP: 'bg-violet-500 text-white ring-violet-500 dark:bg-violet-500 dark:ring-violet-500',
  EXPIRED: 'bg-neutral-600 text-white ring-neutral-600 dark:bg-neutral-500 dark:ring-neutral-500',
  DUPLICATE: 'bg-neutral-400 text-white ring-neutral-400 dark:bg-neutral-600 dark:ring-neutral-600',
}
const HIDEABLE = ['SKIP', 'REJECTED', 'MISMATCH', 'EXP_GAP', 'EXPIRED', 'DUPLICATE']

const SOURCE_STYLES = {
  indeed: 'bg-sky-100 text-sky-700 dark:bg-sky-900 dark:text-sky-300',
  linkedin: 'bg-violet-100 text-violet-700 dark:bg-violet-900 dark:text-violet-300',
  jobstreet: 'bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900 dark:text-fuchsia-300',
  glassdoor: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300',
  google: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300',
  greenhouse: 'bg-teal-100 text-teal-700 dark:bg-teal-900 dark:text-teal-300',
  lever: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-300',
}

function fmtDate(v) {
  if (!v) return '—'
  return String(v).slice(0, 10)
}

function fmtDateTime(v) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return String(v).slice(0, 16).replace('T', ' ')
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function timeAgo(iso) {
  if (!iso) return 'never'
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const STALE_AFTER_DAYS = 30

function isStale(job) {
  const seen = job.last_seen || job.scraped_at
  if (!seen) return false
  return Date.now() - new Date(seen).getTime() > STALE_AFTER_DAYS * 86400e3
}

function isFollowupDue(job) {
  if (!job.follow_up_at) return false
  return String(job.follow_up_at).slice(0, 10) <= new Date().toISOString().slice(0, 10)
}

function Highlight({ text, query }) {
  const terms = (Array.isArray(query) ? query : [query])
    .map((q) => (q || '').trim().toLowerCase())
    .filter(Boolean)
  const s = String(text || '')
  if (!terms.length || !s) return s
  const low = s.toLowerCase()
  let best = -1
  let bestLen = 0
  for (const t of terms) {
    const i = low.indexOf(t)
    if (i !== -1 && (best === -1 || i < best)) {
      best = i
      bestLen = t.length
    }
  }
  if (best === -1) return s
  return (
    <>
      {s.slice(0, best)}
      <mark className="rounded bg-amber-200 px-0.5 text-inherit dark:bg-amber-700 dark:text-amber-100">
        {s.slice(best, best + bestLen)}
      </mark>
      {s.slice(best + bestLen)}
    </>
  )
}

// Mini search syntax (all case-insensitive substring matches):
//   "exact phrase"   quoted phrases stay together
//   a, b | c | d OR e  comma / pipe / OR separate alternatives (match ANY)
//   python backend   bare words in one alternative must ALL match (AND)
//   title:foo / company:foo   restrict the whole query to titles / companies
function splitBranches(input) {
  const branches = []
  let cur = ''
  let inQuotes = false
  const push = () => {
    if (cur.trim()) branches.push(cur.trim())
    cur = ''
  }
  for (const ch of input) {
    if (ch === '"') {
      inQuotes = !inQuotes
      cur += ch
      continue
    }
    if (!inQuotes && (ch === '|' || ch === ',')) {
      push()
      continue
    }
    cur += ch
  }
  push()
  return branches
}

function parseSearchQuery(raw) {
  let s = (raw || '').trim()
  let scope = 'both'
  const scopeMatch = s.match(/^(title|company):/i)
  if (scopeMatch) {
    scope = scopeMatch[1].toLowerCase()
    s = s.slice(scopeMatch[0].length).trim()
  }
  const alternatives = []
  for (const branch of splitBranches(s)) {
    const toks = []
    const re = /"([^"]*)"|(\S+)/g
    let t
    while ((t = re.exec(branch))) {
      toks.push((t[1] !== undefined ? t[1] : t[2]).toLowerCase())
    }
    let cur = []
    const flush = () => {
      const terms = cur.filter((x) => x)
      if (terms.length) alternatives.push(terms)
      cur = []
    }
    for (const tok of toks) {
      if (tok === 'or') flush()
      else cur.push(tok)
    }
    flush()
  }
  const terms = [...new Set(alternatives.flat())].sort((a, b) => b.length - a.length)
  return { scope, alternatives, terms }
}

function matchesSearch(job, parsed) {
  if (!parsed.alternatives.length) return true
  const title = String(job.title || '').toLowerCase()
  const company = String(job.company || '').toLowerCase()
  const haystacks =
    parsed.scope === 'title' ? [title] : parsed.scope === 'company' ? [company] : [title, company]
  return parsed.alternatives.some((alt) =>
    alt.every((term) => haystacks.some((h) => h.includes(term)))
  )
}

function useDarkMode() {
  const [dark, setDark] = useState(() => {
    const stored = localStorage.getItem('theme')
    if (stored) return stored === 'dark'
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  useEffect(() => {
    const root = document.documentElement
    if (dark) {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem('theme', dark ? 'dark' : 'light')
  }, [dark])

  return [dark, setDark]
}

function ApplicationRow({ job, terms, onStage, onStatus, onMoveBack, onChanged }) {
  const [open, setOpen] = useState(false)
  const [history, setHistory] = useState(null)
  const [salary, setSalary] = useState(job.offer_salary || '')
  const [benefits, setBenefits] = useState(job.offer_benefits || '')
  const [pros, setPros] = useState(job.offer_pros || '')
  const [cons, setCons] = useState(job.offer_cons || '')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

  const stage = job.stage || 'APPLIED'
  const isOffer = OFFER_STAGES.includes(stage)

  // Refresh the editor when a refetch replaces the job object.
  useEffect(() => {
    setSalary(job.offer_salary || '')
    setBenefits(job.offer_benefits || '')
    setPros(job.offer_pros || '')
    setCons(job.offer_cons || '')
  }, [job.offer_salary, job.offer_benefits, job.offer_pros, job.offer_cons])

  const loadHistory = useCallback(async () => {
    if (history !== null) return
    try {
      const r = await fetch(`${API}/jobs/${job.id}/history`)
      setHistory(r.ok ? await r.json() : [])
    } catch (e) {
      console.error('history fetch failed:', e)
      setHistory([])
    }
  }, [job.id, history])

  const toggle = () => {
    if (!open) loadHistory()
    setOpen((v) => !v)
  }

  // Which stage the OUT came after (predecessor in stage history).
  const outAfter = useMemo(() => {
    if (stage !== 'OUT' || !history) return ''
    const outEntry = [...history].reverse()
      .find((h) => h.kind === 'stage' && h.new_stage === 'OUT')
    return (outEntry && outEntry.old_stage) || ''
  }, [stage, history])

  const saveOffer = useCallback(async (patch) => {
    setSaving(true)
    setNote('')
    try {
      const r = await fetch(`${API}/jobs/${job.id}/offer`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!r.ok) throw new Error(`save failed (${r.status})`)
      setNote('Saved.')
      await onChanged()
    } catch (e) {
      console.error('offer save failed:', e)
      setNote(`Save failed: ${e.message}`)
    }
    setSaving(false)
  }, [job.id, onChanged])

  return (
    <>
      <tr className="hover:bg-neutral-50 dark:hover:bg-neutral-800/50">
        <td className="px-4 py-3">
          <a href={job.url} target="_blank" rel="noreferrer"
            className="font-medium text-neutral-800 hover:text-black hover:underline dark:text-neutral-200 dark:hover:text-white">
            <Highlight text={job.title || 'Untitled'} query={terms} />
          </a>
          {stage === 'OUT' && outAfter && (
            <span className="block text-xs text-neutral-400 dark:text-neutral-500">
              out after {STAGE_LABELS[outAfter] || outAfter}
            </span>
          )}
          {stage === 'ACCEPTED' && (
            <span className="block text-xs font-medium text-emerald-600 dark:text-emerald-400">accepted ✓</span>
          )}
          {isOffer && job.offer_salary && (
            <span className="block text-xs text-neutral-500 dark:text-neutral-400">{job.offer_salary}</span>
          )}
        </td>
        <td className="px-4 py-3 text-neutral-600 dark:text-neutral-400">
          <Highlight text={job.company || '—'} query={terms} />
        </td>
        <td className="px-4 py-3">
          <select
            value={stage}
            onChange={(e) => onStage(job, e.target.value)}
            className={`rounded-full border-0 px-3 py-1 text-xs font-medium ring-1 text-center ${
              STAGE_STYLES[stage] || 'bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300'
            }`}
            title="Funnel stage (Applied → Initial → Technical → Final → Offer, or Out)"
          >
            {STAGES.map((s) => (
              <option key={s} value={s}>{STAGE_LABELS[s] || s}</option>
            ))}
          </select>
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-neutral-600 dark:text-neutral-400">
          {fmtDateTime(job.status_updated_at)}
          <span className="block text-xs text-neutral-400 dark:text-neutral-500">
            {timeAgo(job.status_updated_at)}
          </span>
        </td>
        <td className="px-4 py-3 text-right sticky right-0 bg-white dark:bg-neutral-900">
          <button
            onClick={toggle}
            title="Timeline, offer details, and move-back"
            className="rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-500 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800"
          >
            {open ? 'Hide' : 'Details'}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="bg-neutral-50 dark:bg-neutral-800/30">
          <td colSpan={5} className="px-8 py-3">
            <h4 className="text-xs font-medium text-neutral-500 dark:text-neutral-400">Timeline</h4>
            {history === null ? (
              <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">Loading…</p>
            ) : history.length === 0 ? (
              <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
                No recorded transitions yet — stages you set from here on are tracked.
              </p>
            ) : (
              <ol className="mt-1 space-y-1 text-xs text-neutral-600 dark:text-neutral-400">
                {history.map((h, i) => (
                  <li key={i}>
                    <span className="font-medium text-neutral-700 dark:text-neutral-300">
                      {h.kind === 'stage'
                        ? `stage: ${h.old_stage || '—'} → ${h.new_stage}`
                        : `${h.old_status || '—'} → ${h.new_status}`}
                    </span>
                    <span className="ml-2 text-neutral-400 dark:text-neutral-500">
                      {fmtDateTime(h.changed_at)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
            {isOffer && (
              <div className="mt-3 border-t border-neutral-200 pt-3 dark:border-neutral-700">
                <h4 className="text-xs font-medium text-neutral-500 dark:text-neutral-400">
                  Offer details — compare before you decide
                </h4>
                <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-xs text-neutral-600 dark:text-neutral-400">
                    Salary
                    <input value={salary} onChange={(e) => setSalary(e.target.value)}
                      onBlur={() => { if (salary !== (job.offer_salary || '')) saveOffer({ offer_salary: salary }) }}
                      placeholder="e.g. ₱35k/mo"
                      className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-800" />
                  </label>
                  <label className="text-xs text-neutral-600 dark:text-neutral-400">
                    Benefits
                    <textarea value={benefits} onChange={(e) => setBenefits(e.target.value)} rows={2}
                      placeholder="HMO, 13th month, hybrid…"
                      className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-800" />
                  </label>
                  <label className="text-xs text-neutral-600 dark:text-neutral-400">
                    Pros
                    <textarea value={pros} onChange={(e) => setPros(e.target.value)} rows={2}
                      placeholder="Growth, stack, mentorship…"
                      className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-800" />
                  </label>
                  <label className="text-xs text-neutral-600 dark:text-neutral-400">
                    Cons
                    <textarea value={cons} onChange={(e) => setCons(e.target.value)} rows={2}
                      placeholder="Commute, on-call, lowball…"
                      className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-800" />
                  </label>
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <button
                    onClick={() => saveOffer({ offer_salary: salary, offer_benefits: benefits, offer_pros: pros, offer_cons: cons })}
                    disabled={saving}
                    className="rounded-lg bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-neutral-700 disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
                  >
                    {saving ? 'Saving…' : 'Save details'}
                  </button>
                  {note && <span className="text-xs text-neutral-500 dark:text-neutral-400" role="status">{note}</span>}
                </div>
              </div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-neutral-200 pt-2 dark:border-neutral-700">
              <button
                onClick={() => onMoveBack(job)}
                title="Send back to the Jobs tab as REVIEWED (clears the stage)"
                className="rounded-lg px-2 py-1 text-xs text-neutral-500 hover:bg-neutral-200 dark:text-neutral-400 dark:hover:bg-neutral-800"
              >
                ↩ Move back to Jobs
              </button>
              <button
                onClick={() => onStatus(job, 'REJECTED')}
                title="They said no — moves to Jobs as REJECTED (clears the stage)"
                className="rounded-lg border border-rose-300 px-2 py-1 text-xs font-medium text-rose-600 hover:bg-rose-50 dark:border-rose-700 dark:text-rose-400 dark:hover:bg-rose-950"
              >
                Mark rejected
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function App() {
  const [dark, setDark] = useDarkMode()
  const [jobs, setJobs] = useState([])
  const [stats, setStats] = useState(null)
  const [lastRun, setLastRun] = useState(null)
  const [loading, setLoading] = useState(true)

  const [status, setStatus] = useState('')
  const [source, setSource] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [search, setSearch] = useState('')
  const [hidden, setHidden] = useState([])
  const [selected, setSelected] = useState([])
  const [bulkStatus, setBulkStatus] = useState('')
  const [bulkBusy, setBulkBusy] = useState(false)
  const [pendingApply, setPendingApply] = useState(null)
  const [confirmApplyId, setConfirmApplyId] = useState(null)
  const [scraping, setScraping] = useState(false)
  const [scrapeNote, setScrapeNote] = useState('')
  const [tab, setTab] = useState('jobs')
  const [filteredJobs, setFilteredJobs] = useState([])
  const [scrapedDir, setScrapedDir] = useState(null) // null | 'desc' | 'asc'
  const [scoreDir, setScoreDir] = useState('desc') // null | 'desc' | 'asc' (default: best fit first)
  const [dueOnly, setDueOnly] = useState(false)
  const [filteredNote, setFilteredNote] = useState('')
  const [restoring, setRestoring] = useState(null)
  const [showDupes, setShowDupes] = useState(false)
  const [keepers, setKeepers] = useState({})
  const [dupeBusy, setDupeBusy] = useState(null)

  const [resumes, setResumes] = useState([])
  const [defaultResume, setDefaultResume] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadNote, setUploadNote] = useState('')
  const [dragActive, setDragActive] = useState(false)

  const [tailorJob, setTailorJob] = useState(null)
  const [tailorText, setTailorText] = useState('')
  const [tailorResume, setTailorResume] = useState('')
  const [tailorResult, setTailorResult] = useState(null)
  const [tailorLoading, setTailorLoading] = useState(false)
  const [tailorError, setTailorError] = useState('')

  const toggleHidden = useCallback((s) => {
    setHidden((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]))
  }, [])

  const toggleSelect = useCallback((id) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }, [])

  const toggleSelectAll = useCallback(
    (rows) => {
      const ids = rows.map((job) => job.id)
      setSelected((prev) => {
        const allIn = ids.length > 0 && ids.every((id) => prev.includes(id))
        if (allIn) return prev.filter((id) => !ids.includes(id))
        return [...new Set([...prev, ...ids])]
      })
    },
    []
  )

  const fetchAll = useCallback(async () => {
    try {
      const [jobsRes, statsRes, runRes, resumesRes, filteredRes] = await Promise.all([
        fetch(API + '/jobs'),
        fetch(API + '/stats'),
        fetch(API + '/runs/latest'),
        fetch(API + '/resumes'),
        fetch(API + '/filtered'),
      ])
      const [j, st, r, res, fj] = await Promise.all([
        jobsRes.json(),
        statsRes.json(),
        runRes.json(),
        resumesRes.json().catch(() => ({ resumes: [], default: '' })),
        filteredRes.json().catch(() => []),
      ])
      setJobs(j)
      setStats(st)
      setLastRun(r)
      setResumes(res.resumes || [])
      setDefaultResume(res.default || '')
      setFilteredJobs(Array.isArray(fj) ? fj : [])
      // Drop selections for rows that no longer exist.
      setSelected((prev) => prev.filter((id) => j.some((job) => job.id === id)))
    } catch (e) {
      console.error('dashboard fetch failed:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const t = setInterval(fetchAll, 60000)
    return () => clearInterval(t)
  }, [fetchAll])

  const bulkApplyStatus = useCallback(async () => {
    if (!bulkStatus || selected.length === 0 || bulkBusy) return
    setBulkBusy(true)
    try {
      await Promise.all(
        selected.map((id) =>
          fetch(`${API}/jobs/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: bulkStatus }),
          })
        )
      )
      setSelected([])
      setBulkStatus('')
      await fetchAll()
    } catch (e) {
      console.error('bulk status update failed:', e)
      await fetchAll()
    }
    setBulkBusy(false)
  }, [bulkStatus, selected, bulkBusy, fetchAll])

  const parsedSearch = useMemo(() => parseSearchQuery(search), [search])

  const filtered = useMemo(() => {
    return jobs.filter((job) => {
      if (status && job.status !== status) return false
      // Negative filter-out tags: hidden statuses apply only when no
      // explicit status is selected (the dropdown takes precedence).
      if (!status && hidden.includes(job.status)) return false
      if (source && job.source !== source) return false
      if (dateFrom && String(job.date_posted || '').slice(0, 10) < dateFrom) return false
      if (dateTo && String(job.date_posted || '').slice(0, 10) > dateTo) return false
      if (dueOnly && !isFollowupDue(job)) return false
      if (!matchesSearch(job, parsedSearch)) return false
      return true
    })
  }, [jobs, status, hidden, source, dateFrom, dateTo, dueOnly, parsedSearch])

  const dueCount = useMemo(() => jobs.filter(isFollowupDue).length, [jobs])

  const hiddenCounts = useMemo(() => {
    const counts = {}
    for (const job of jobs) counts[job.status] = (counts[job.status] || 0) + 1
    return counts
  }, [jobs])

  const pendingFiltered = useMemo(
    () => filteredJobs.filter((j) => !j.restored),
    [filteredJobs]
  )

  const dupeGroups = useMemo(() => findDuplicateGroups(jobs), [jobs])

  const dupeExtraRows = useMemo(
    () => dupeGroups.reduce((n, g) => n + g.rows.length - 1, 0),
    [dupeGroups]
  )

  const visibleFiltered = useMemo(
    () => pendingFiltered.filter((job) => matchesSearch(job, parsedSearch)),
    [pendingFiltered, parsedSearch]
  )

  // APPLIED rows "move out" of Jobs into the Applications tab, where the
  // funnel stage (not the triage status) is tracked.
  const jobsBase = useMemo(
    () => filtered.filter((job) => job.status !== 'APPLIED'),
    [filtered]
  )

  const [appFilter, setAppFilter] = useState('all') // all | interviews | offers

  const applicationRows = useMemo(
    () =>
      jobs
        .filter((job) => {
          if (job.status !== 'APPLIED') return false
          if (appFilter === 'interviews' && !INTERVIEW_STAGES.includes(job.stage || '')) return false
          if (appFilter === 'offers' && !OFFER_STAGES.includes(job.stage || '')) return false
          return matchesSearch(job, parsedSearch)
        })
        .sort((a, b) => new Date(b.status_updated_at || 0).getTime() - new Date(a.status_updated_at || 0).getTime()),
    [jobs, appFilter, parsedSearch]
  )

  const appCounts = useMemo(() => {
    const applied = jobs.filter((job) => job.status === 'APPLIED')
    return {
      total: applied.length,
      interviews: applied.filter((job) => INTERVIEW_STAGES.includes(job.stage || '')).length,
      offers: applied.filter((job) => OFFER_STAGES.includes(job.stage || '')).length,
    }
  }, [jobs])

  const sortedJobs = useMemo(() => {
    // Explicit scraped-time sort wins when active; otherwise score-first
    // (the API already returns score order, this keeps client-side
    // filtering/sorting consistent).
    if (scrapedDir) {
      const dir = scrapedDir === 'asc' ? 1 : -1
      return [...jobsBase].sort(
        (a, b) =>
          dir *
          (new Date(a.scraped_at || 0).getTime() - new Date(b.scraped_at || 0).getTime())
      )
    }
    if (!scoreDir) return jobsBase
    const dir = scoreDir === 'asc' ? 1 : -1
    return [...jobsBase].sort(
      (a, b) => dir * ((a.score ?? 0) - (b.score ?? 0)) ||
        (new Date(b.scraped_at || 0).getTime() - new Date(a.scraped_at || 0).getTime())
    )
  }, [jobsBase, scrapedDir, scoreDir])

  const cycleScrapedSort = useCallback(() => {
    // Only one sort wins at a time.
    setScoreDir(null)
    setScrapedDir((d) => (d === null ? 'desc' : d === 'desc' ? 'asc' : null))
  }, [])

  const cycleScoreSort = useCallback(() => {
    // Activating score sort clears the scraped sort so only one wins.
    setScrapedDir(null)
    setScoreDir((d) => (d === 'desc' ? 'asc' : d === 'asc' ? null : 'desc'))
  }, [])

  const updateJobs = useCallback(
    (id, patch) => {
      setJobs((prev) => prev.map((j) => (j.id === id ? { ...j, ...patch } : j)))
    },
    []
  )

  const changeStatus = useCallback(
    async (job, nextStatus) => {
      updateJobs(job.id, { status: nextStatus })
      if (confirmApplyId === job.id) setConfirmApplyId(null)
      try {
        await fetch(`${API}/jobs/${job.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: nextStatus }),
        })
        fetchAll()
      } catch (e) {
        console.error('status update failed:', e)
        fetchAll()
      }
    },
    [updateJobs, fetchAll, confirmApplyId]
  )

  const setFollowup = useCallback(
    async (job, nextDate) => {
      updateJobs(job.id, { follow_up_at: nextDate || null })
      try {
        await fetch(`${API}/jobs/${job.id}/followup`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ follow_up_at: nextDate || null }),
        })
        fetchAll()
      } catch (e) {
        console.error('follow-up update failed:', e)
        fetchAll()
      }
    },
    [updateJobs, fetchAll]
  )

  const setStage = useCallback(
    async (job, nextStage) => {
      updateJobs(job.id, { stage: nextStage, status: 'APPLIED' })
      try {
        await fetch(`${API}/jobs/${job.id}/stage`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ stage: nextStage }),
        })
        fetchAll()
      } catch (e) {
        console.error('stage update failed:', e)
        fetchAll()
      }
    },
    [updateJobs, fetchAll]
  )

  const moveBackToJobs = useCallback(
    async (job) => {
      // Back to triage as REVIEWED; the API clears the stage.
      await changeStatus(job, 'REVIEWED')
    },
    [changeStatus]
  )

    const handleApply = useCallback(async (job) => {
      updateJobs(job.id, { status: 'REVIEWED' })
      setPendingApply(job.id)
      try {
        window.open(job.url, '_blank')
      } catch (e) {
        console.error('apply trigger failed:', e)
      }
      try {
        await fetch(`${API}/jobs/${job.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'REVIEWED' }),
        })
      } catch (e) {
        console.error('apply status persist failed:', e)
      }
      setPendingApply(null)
      // Ask what actually happened: confirm the application, or file why not.
      setConfirmApplyId(job.id)
    },
    [updateJobs]
  )

  const restoreFiltered = useCallback(
    async (job) => {
      setRestoring(job.id)
      setFilteredNote('')
      try {
        const r = await fetch(`${API}/filtered/${job.id}/restore`, {
          method: 'POST',
        })
        if (!r.ok) throw new Error(`restore failed (${r.status})`)
        setFilteredNote(
          `Restored "${job.title || 'Untitled'}" to Jobs as NEW — mark it REVIEWED/APPLIED there so the learner adjusts.`
        )
        setTab('jobs')
      } catch (e) {
        console.error('restore failed:', e)
        setFilteredNote(`Restore failed: ${e.message}`)
      }
      await fetchAll()
      setRestoring(null)
    },
    [fetchAll]
  )

  const deleteFiltered = useCallback(
    async (job) => {
      if (!window.confirm(`Dismiss "${job.title || 'Untitled'}" from review? (A future scrape can hold it again.)`))
        return
      try {
        await fetch(`${API}/filtered/${job.id}`, { method: 'DELETE' })
      } catch (e) {
        console.error('filtered delete failed:', e)
      }
      await fetchAll()
    },
    [fetchAll]
  )

  const markGroupDupe = useCallback(
    async (group) => {
      const kept = new Set(
        (keepers[group.key] ?? [defaultKeeper(group)]).filter((id) =>
          group.rows.some((r) => r.id === id)
        )
      )
      const rest = group.rows.filter((r) => !kept.has(r.id))
      if (!rest.length) return
      setDupeBusy(group.key)
      try {
        await Promise.all(
          rest.map((r) =>
            fetch(`${API}/jobs/${r.id}`, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ status: 'DUPLICATE' }),
            })
          )
        )
        setKeepers((prev) => {
          const next = { ...prev }
          delete next[group.key]
          return next
        })
      } catch (e) {
        console.error('mark duplicates failed:', e)
      }
      await fetchAll()
      setDupeBusy(null)
    },
    [keepers, fetchAll]
  )

  const handleScrape = useCallback(async () => {
    if (scraping) return
    setScraping(true)
    setScrapeNote('Starting scrape…')
    try {
      const startRes = await fetch(`${API}/scrape`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (startRes.status === 409) {
        setScrapeNote('A scrape is already running — waiting for it to finish…')
      } else if (!startRes.ok) {
        throw new Error(`scrape start failed (${startRes.status})`)
      } else {
        setScrapeNote('Scraping new jobs… you can keep reviewing while it runs.')
      }
      // Poll until the background scrape finishes, then refresh.
      for (;;) {
        await new Promise((r) => setTimeout(r, 3000))
        const sRes = await fetch(`${API}/scrape/status`)
        const s = await sRes.json()
        if (s.state === 'running' || s.state === 'idle') {
          continue
        }
        if (s.state === 'done') {
          const reasons = Object.entries(s.filter_reasons || {})
            .sort((a, b) => b[1] - a[1])
            .slice(0, 5)
            .map(([k, v]) => `${k}×${v}`)
            .join(', ')
          const filt =
            s.filtered != null && s.filtered > 0
              ? `, ${s.filtered} auto-filtered${reasons ? ` (${reasons})` : ''}`
              : ''
          const held =
            s.filtered_saved != null && s.filtered_saved > 0
              ? `, ${s.filtered_saved} held for review in the Filtered tab`
              : ''
          setScrapeNote(
            s.added != null
              ? `Scrape finished: +${s.added} new (${s.scraped ?? 0} checked${filt}${held})${
                  s.warnings?.length ? ` ⚠ ${s.warnings.join(' | ')}` : ''
                }`
              : 'Scrape finished.'
          )
        } else {
          setScrapeNote(s.error ? `Scrape failed: ${s.error}` : 'Scrape failed.')
        }
        break
      }
    } catch (e) {
      console.error('scrape trigger failed:', e)
      setScrapeNote('Could not start scrape — is the API running?')
    }
    await fetchAll()
    setScraping(false)
  }, [scraping, fetchAll])

  const uploadResumes = useCallback(
    async (files) => {
      const docs = [...(files || [])].filter((f) => /\.docx$/i.test(f.name))
      if (!docs.length) {
        setUploadNote('Drop a .docx resume file.')
        return
      }
      setUploading(true)
      setUploadNote('')
      try {
        for (const f of docs) {
          const form = new FormData()
          form.append('file', f)
          const r = await fetch(API + '/resumes/upload', {
            method: 'POST',
            body: form,
          })
          if (!r.ok) {
            const err = await r.json().catch(() => ({}))
            throw new Error(err.detail || `upload failed (${r.status})`)
          }
          const entry = await r.json()
          setUploadNote(
            `Added ${entry.name}: ${entry.jobs} jobs, ${entry.bullets} bullets parsed.`
          )
        }
      } catch (e) {
        console.error('resume upload failed:', e)
        setUploadNote(`Upload failed: ${e.message}`)
      }
      await fetchAll()
      setUploading(false)
    },
    [fetchAll]
  )

  const chooseDefaultResume = useCallback(
    async (name) => {
      try {
        await fetch(API + '/resumes/default', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        })
        setDefaultResume(name)
      } catch (e) {
        console.error('default resume update failed:', e)
      }
    },
    []
  )

  const removeResume = useCallback(
    async (name) => {
      if (!window.confirm(`Delete resume "${name}" from the library?`)) return
      try {
        await fetch(`${API}/resumes/${encodeURIComponent(name)}`, {
          method: 'DELETE',
        })
      } catch (e) {
        console.error('resume delete failed:', e)
      }
      await fetchAll()
    },
    [fetchAll]
  )

  const openTailor = useCallback(
    (job) => {
      setTailorJob(job)
      setTailorResult(null)
      setTailorError('')
      setTailorResume(defaultResume || (resumes[0] && resumes[0].name) || '')
      setTailorText(
        `Title: ${job.title || ''}\nCompany: ${job.company || ''}\n` +
          `Location: ${job.location || ''}\nURL: ${job.url || ''}\n\n` +
          `--- paste the full posting description below this line ---\n`
      )
    },
    [defaultResume, resumes]
  )

  const runTailor = useCallback(async () => {
    if (!tailorJob || tailorLoading) return
    setTailorLoading(true)
    setTailorError('')
    setTailorResult(null)
    try {
      const r = await fetch(`${API}/jobs/${tailorJob.id}/tailor`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume: tailorResume, job_text: tailorText }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.detail || `tailoring failed (${r.status})`)
      setTailorResult(data)
    } catch (e) {
      console.error('tailor failed:', e)
      setTailorError(e.message)
    }
    setTailorLoading(false)
  }, [tailorJob, tailorLoading, tailorResume, tailorText])

  const downloadTailored = useCallback(async () => {
    if (!tailorJob || !tailorResult) return
    try {
      const r = await fetch(`${API}/jobs/${tailorJob.id}/tailor/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          resume: tailorResume,
          tailored: tailorResult.tailored,
        }),
      })
      if (!r.ok) {
        const err = await r.json().catch(() => ({}))
        throw new Error(err.detail || `download failed (${r.status})`)
      }
      const blob = await r.blob()
      const cd = r.headers.get('content-disposition') || ''
      const m = cd.match(/filename="?([^";]+)"?/)
      const fname = m ? m[1] : 'Resume_tailored.docx'
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = fname
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(a.href), 5000)
    } catch (e) {
      console.error('tailored download failed:', e)
      setTailorError(e.message)
    }
  }, [tailorJob, tailorResult, tailorResume])

  const barData = useMemo(() => {
    if (stats?.outcome_series?.length) return stats.outcome_series
    // Back-compat with older /stats that only sent applied_series.
    if (stats?.applied_series?.length)
      return stats.applied_series.map((r) => ({
        date: String(r.week || '').slice(0, 10),
        applied: r.count || 0,
        rejected: 0,
        skipped: 0,
        mismatch: 0,
        expgap: 0,
      }))
    return []
  }, [stats])

  const decidedThisWeek = useMemo(() => {
    if (!stats) return 0
    if (
      stats.applied_this_week !== undefined ||
      stats.rejected_this_week !== undefined ||
      stats.skipped_this_week !== undefined
    )
      return (
        (stats.applied_this_week ?? 0) +
        (stats.rejected_this_week ?? 0) +
        (stats.skipped_this_week ?? 0) +
        (stats.mismatch_this_week ?? 0) +
        (stats.expgap_this_week ?? 0)
      )
    // Fallback: sum the latest chart bucket.
    const last = barData[barData.length - 1]
    return last
      ? (last.applied || 0) + (last.rejected || 0) + (last.skipped || 0) +
        (last.mismatch || 0) + (last.expgap || 0)
      : 0
  }, [stats, barData])

  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-800 dark:bg-neutral-950 dark:text-neutral-200">
      <header className="sticky top-0 z-10 border-b border-neutral-200 bg-white px-6 py-4 dark:border-neutral-800 dark:bg-neutral-900">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold">Job Scraper Dashboard</h1>
            <p className="text-sm text-neutral-500 dark:text-neutral-400">
              Junior/entry-level roles · Metro Manila
            </p>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-medium ring-1 ${
                lastRun
                  ? 'bg-neutral-100 text-neutral-600 ring-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:ring-neutral-700'
                  : 'bg-neutral-100 text-neutral-400 ring-neutral-200 dark:bg-neutral-800 dark:text-neutral-500 dark:ring-neutral-700'
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  lastRun ? 'bg-emerald-500' : 'bg-neutral-400'
                }`}
              />
              Last scraped: {lastRun ? timeAgo(lastRun.finished_at) : '—'}
            </span>
            {lastRun?.new_jobs_count >= 0 && (
              <span className="text-neutral-500 dark:text-neutral-400">
                ({lastRun.new_jobs_count} new last run)
              </span>
            )}
            <button
              onClick={handleScrape}
              disabled={scraping}
              title="Scrape new jobs now (same as running main.py)"
              className="rounded-lg bg-neutral-900 px-3 py-2 text-xs font-medium text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
            >
              {scraping ? 'Scraping…' : 'Scrape new jobs'}
            </button>
            <button
              onClick={() => setDark((d) => !d)}
              className="rounded-lg border border-neutral-300 bg-neutral-100 p-2 hover:bg-neutral-200 dark:border-neutral-700 dark:bg-neutral-800 dark:hover:bg-neutral-700"
              title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {dark ? (
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="4"/>
                  <path d="M12 2v2"/>
                  <path d="M12 20v2"/>
                  <path d="m4.93 4.93 1.41 1.41"/>
                  <path d="m17.66 17.66 1.41 1.41"/>
                  <path d="M2 12h2"/>
                  <path d="M20 12h2"/>
                  <path d="m6.34 17.66-1.41 1.41"/>
                  <path d="m19.07 4.93-1.41 1.41"/>
                </svg>
              ) : (
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>
                </svg>
              )}
            </button>
          </div>
        </div>
        {scrapeNote && (
          <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400" role="status">
            {scrapeNote}
          </p>
        )}
      </header>

      <main className="mx-auto max-w-7xl px-6 py-6">
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <StatCard
            label="Total tracked"
            value={jobs.length}
            accent="bg-neutral-800 dark:bg-neutral-200"
          />
          <StatCard
            label="NEW this week"
            value={stats?.new_this_week ?? 0}
            accent="bg-neutral-800"
          />
          <StatCard
            label="Applied (all time)"
            value={stats?.by_status?.APPLIED ?? 0}
            accent="bg-emerald-600"
          />
          <StatCard
            label="Rejected (all time)"
            value={stats?.by_status?.REJECTED ?? 0}
            accent="bg-rose-600"
          />
          <StatCard
            label="Skipped (all time)"
            value={stats?.by_status?.SKIP ?? 0}
            accent="bg-amber-500"
          />
          <StatCard
            label="Mismatch (all time)"
            value={stats?.by_status?.MISMATCH ?? 0}
            accent="bg-orange-500"
          />
          <StatCard
            label="Exp. gap (all time)"
            value={stats?.by_status?.EXP_GAP ?? 0}
            accent="bg-violet-500"
          />
          <StatCard
            label="Expired (all time)"
            value={stats?.by_status?.EXPIRED ?? 0}
            accent="bg-neutral-400"
          />
          <StatCard
            label="Duplicates (all time)"
            value={stats?.by_status?.DUPLICATE ?? 0}
            accent="bg-neutral-300"
          />
          <StatCard
            label="In interviews"
            value={appCounts.interviews}
            accent="bg-sky-500"
          />
          <StatCard
            label="Offers"
            value={appCounts.offers}
            accent="bg-yellow-500"
          />
          <StatCard
            label={`Decided this week (${barData.length} day${barData.length === 1 ? '' : 's'})`}
            value={decidedThisWeek}
            accent="bg-violet-600"
          />
        </section>

        {barData.length > 0 && (
          <section className="mt-6 rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
            <h2 className="mb-2 text-sm font-medium text-neutral-600 dark:text-neutral-400">
              Outcomes per day — applied vs rejected vs skipped vs mismatch vs exp. gap
            </h2>
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={barData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={dark ? '#334155' : '#e2e8f0'} />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(v) => String(v).slice(5, 10)}
                    tick={{ fontSize: 12, fill: dark ? '#94a3b8' : '#64748b' }}
                  />
                  <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: dark ? '#94a3b8' : '#64748b' }} />
                  <Tooltip
                    labelFormatter={(v) => String(v).slice(0, 10)}
                    contentStyle={{
                      backgroundColor: dark ? '#1e293b' : '#fff',
                      border: dark ? '1px solid #334155' : '1px solid #e2e8f0',
                      borderRadius: '0.5rem',
                      color: dark ? '#e2e8f0' : '#1e293b',
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Line type="monotone" dataKey="applied" name="Applied" stroke="#10b981" strokeWidth={2} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="rejected" name="Rejected" stroke="#f43f5e" strokeWidth={2} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="skipped" name="Skipped" stroke="#f59e0b" strokeWidth={2} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="mismatch" name="Mismatch" stroke="#fb923c" strokeWidth={2} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="expgap" name="Exp. gap" stroke="#8b5cf6" strokeWidth={2} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>
        )}

        <section className="mt-6 rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-medium text-neutral-600 dark:text-neutral-400">
              Resumes — drag &amp; drop a .docx to add it to the tailoring library
            </h2>
            {uploading && (
              <span className="text-xs text-neutral-500 dark:text-neutral-400">Parsing…</span>
            )}
          </div>
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragActive(true)
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragActive(false)
              uploadResumes(e.dataTransfer.files)
            }}
            className={`flex items-center gap-4 rounded-lg border-2 border-dashed p-4 transition ${
              dragActive
                ? 'border-neutral-500 bg-neutral-100 dark:border-neutral-400 dark:bg-neutral-900'
                : 'border-neutral-300 dark:border-neutral-700'
            }`}
          >
            <label className="cursor-pointer rounded-lg bg-neutral-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-neutral-700 dark:bg-neutral-200 dark:text-neutral-800 dark:hover:bg-neutral-300">
              Choose .docx
              <input
                type="file"
                accept=".docx"
                multiple
                className="hidden"
                onChange={(e) => {
                  uploadResumes(e.target.files)
                  e.target.value = ''
                }}
              />
            </label>
            <p className="text-xs text-neutral-500 dark:text-neutral-400">
              …or drop files here. Parsed locally into skills/bullets — only the
              trimmed, contact-free bank is ever sent to the AI when you press Tailor.
            </p>
          </div>
          {uploadNote && (
            <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400" role="status">
              {uploadNote}
            </p>
          )}
          {resumes.length > 0 && (
            <ul className="mt-3 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
              {resumes.map((r) => (
                <li key={r.name} className="flex items-center gap-3 py-2">
                  <input
                    type="radio"
                    name="default-resume"
                    checked={r.name === defaultResume}
                    onChange={() => chooseDefaultResume(r.name)}
                    title="Use as default for Tailor"
                    className="accent-neutral-800"
                  />
                  <span className="font-medium text-neutral-700 dark:text-neutral-300">
                    {r.name}
                  </span>
                  <span className="text-xs text-neutral-500 dark:text-neutral-400">
                    {r.bank_summary}
                  </span>
                  <button
                    onClick={() => removeResume(r.name)}
                    title={`Delete ${r.name}`}
                    className="ml-auto rounded px-2 py-0.5 text-xs text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950"
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="mt-6 flex flex-wrap items-center gap-2">
          <button
            onClick={() => setTab('jobs')}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
              tab === 'jobs'
                ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800'
            }`}
          >
            Jobs ({jobsBase.length})
          </button>
          <button
            onClick={() => setTab('filtered')}
            title="Postings the auto-filter held out — restore the good ones, dismiss the rest"
            className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
              tab === 'filtered'
                ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800'
            }`}
          >
            Filtered for review ({pendingFiltered.length})
          </button>
          <button
            onClick={() => setTab('applications')}
            title="Submitted applications — track interview stages and offers here"
            className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
              tab === 'applications'
                ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800'
            }`}
          >
            Applications ({appCounts.total})
          </button>
          {tab === 'filtered' && (
            <span className="text-sm text-neutral-500 dark:text-neutral-400">
              Held out by the auto-filter — check the reason, restore what looks
              good, dismiss the rest.
            </span>
          )}
        </section>

        {tab === 'jobs' && dupeGroups.length > 0 && (
        <section className="mt-6 overflow-hidden rounded-xl border border-amber-200 bg-white dark:border-amber-900 dark:bg-neutral-900">
          <button
            onClick={() => setShowDupes((v) => !v)}
            className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-amber-50 dark:hover:bg-neutral-800/50"
            title="Same normalized title at the same company — tick the rows to keep per group, mark the rest DUPLICATE"
          >
            <span className="text-sm font-medium text-neutral-700 dark:text-neutral-200">
              {showDupes ? '▾' : '▸'} Possible duplicates — {dupeGroups.length} group{dupeGroups.length === 1 ? '' : 's'}, {dupeExtraRows} repeat row{dupeExtraRows === 1 ? '' : 's'}
            </span>
            <span className="text-xs text-neutral-400 dark:text-neutral-500">
              same title + company · you confirm each group
            </span>
          </button>
          {showDupes && (
          <div className="divide-y divide-neutral-100 border-t border-amber-200 dark:divide-neutral-800 dark:border-amber-900">
            {dupeGroups.map((group) => {
              const keptIds = new Set(
                (keepers[group.key] ?? [defaultKeeper(group)]).filter((id) =>
                  group.rows.some((r) => r.id === id)
                )
              )
              const nMark = group.rows.length - keptIds.size
              const busy = dupeBusy === group.key
              return (
                <div key={group.key} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-neutral-800 dark:text-neutral-200">
                      {group.rows[0].title}{' '}
                      <span className="font-normal text-neutral-500 dark:text-neutral-400">
                        @ {group.rows[0].company} ({group.rows.length})
                      </span>
                    </p>
                    <button
                      onClick={() => markGroupDupe(group)}
                      disabled={busy || nMark === 0}
                      title="Checked rows stay, unchecked rows become DUPLICATE"
                      className="ml-auto rounded-lg bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
                    >
                      {busy ? 'Marking…' : nMark === 0 ? 'All checked — nothing to mark' : `Mark unchecked ${nMark} DUPLICATE`}
                    </button>
                  </div>
                  <ul className="mt-2 space-y-1.5">
                    {group.rows.map((row) => (
                      <li key={row.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                        <input
                          type="checkbox"
                          checked={keptIds.has(row.id)}
                          onChange={() =>
                            setKeepers((prev) => {
                              const cur = new Set(
                                (prev[group.key] ?? [defaultKeeper(group)]).filter((id) =>
                                  group.rows.some((r) => r.id === id)
                                )
                              )
                              if (cur.has(row.id)) {
                                cur.delete(row.id)
                              } else {
                                cur.add(row.id)
                              }
                              return { ...prev, [group.key]: [...cur] }
                            })
                          }
                          title="Keep this one — unchecked rows get marked DUPLICATE"
                          className="accent-neutral-800"
                        />
                        <a
                          href={row.url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium text-neutral-700 hover:underline dark:text-neutral-300"
                        >
                          #{row.id} · {row.location || '—'}
                        </a>
                        <span
                          className={`inline-block rounded px-2 py-0.5 text-xs font-medium capitalize ${
                            SOURCE_STYLES[row.source] || 'bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300'
                          }`}
                        >
                          {row.source || '—'}
                        </span>
                        <span
                          className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${
                            STATUS_STYLES[row.status] || 'bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300'
                          }`}
                        >
                          {row.status}
                        </span>
                        <span className="text-xs text-neutral-400 dark:text-neutral-500">
                          scraped {timeAgo(row.scraped_at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )
            })}
          </div>
          )}
        </section>
        )}

        {tab === 'jobs' && (
        <section className="mt-6 flex flex-wrap items-center gap-3 rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800"
          >
            <option value="">All statuses</option>
            {ALL_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          {HIDEABLE.map((s) => {
            const isHidden = hidden.includes(s)
            return (
              <button
                key={s}
                onClick={() => toggleHidden(s)}
                title={isHidden ? `Show ${s} postings` : `Hide ${s} postings`}
                className={`rounded-full px-3 py-1.5 text-xs font-medium ring-1 transition ${
                  isHidden
                    ? HIDE_ACTIVE_STYLES[s]
                    : 'bg-white text-neutral-500 ring-neutral-300 hover:bg-neutral-100 dark:bg-neutral-800 dark:text-neutral-400 dark:ring-neutral-700 dark:hover:bg-neutral-700'
                }`}
              >
                {isHidden ? '✕' : '◌'} {s} ({hiddenCounts[s] || 0})
              </button>
            )
          })}
          <button
            onClick={() => setDueOnly((v) => !v)}
            title="Show only postings with a follow-up date of today or earlier"
            className={`rounded-full px-3 py-1.5 text-xs font-medium ring-1 transition ${
              dueOnly
                ? 'bg-violet-600 text-white ring-violet-600'
                : 'bg-white text-neutral-500 ring-neutral-300 hover:bg-neutral-100 dark:bg-neutral-800 dark:text-neutral-400 dark:ring-neutral-700 dark:hover:bg-neutral-700'
            }`}
          >
            {dueOnly ? '✕' : '◌'} Due follow-ups ({dueCount})
          </button>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800"
          >
            <option value="">All sources</option>
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-sm text-neutral-600 dark:text-neutral-400">
            From
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="rounded-lg border border-neutral-300 bg-white px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800"
            />
          </label>
          <label className="flex items-center gap-1.5 text-sm text-neutral-600 dark:text-neutral-400">
            To
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="rounded-lg border border-neutral-300 bg-white px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800"
            />
          </label>
          <input
            type="search"
            placeholder='title/company: python backend, jr | junior, "exact phrase"'
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="min-w-52 flex-1 rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800"
          />
          <span className="text-sm text-neutral-500 dark:text-neutral-400">
            {jobsBase.length} of {jobs.length}
          </span>
        </section>
        )}

        {tab === 'jobs' && selected.length > 0 && (
          <section className="mt-6 flex flex-wrap items-center gap-3 rounded-xl border border-neutral-300 bg-neutral-100 p-4 dark:border-neutral-700 dark:bg-neutral-900">
            <span className="text-sm font-medium text-neutral-700 dark:text-neutral-200">
              {selected.length} selected
            </span>
            <select
              value={bulkStatus}
              onChange={(e) => setBulkStatus(e.target.value)}
              className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800"
            >
              <option value="">Set status…</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button
              onClick={bulkApplyStatus}
              disabled={!bulkStatus || bulkBusy}
              className="rounded-lg bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
            >
              {bulkBusy ? 'Applying…' : 'Apply to selected'}
            </button>
            <button
              onClick={() => {
                setSelected([])
                setBulkStatus('')
              }}
              className="rounded-lg px-3 py-1.5 text-sm text-neutral-500 hover:bg-neutral-200 dark:text-neutral-400 dark:hover:bg-neutral-800"
            >
              Clear
            </button>
          </section>
        )}

        {tab === 'jobs' && (
        <section className="mt-6 overflow-x-auto rounded-xl border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          {loading ? (
            <div className="p-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
              Loading jobs…
            </div>
          ) : sortedJobs.length === 0 ? (
            <div className="p-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
              No jobs match your filters.
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="border-b border-neutral-200 bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:bg-neutral-800 dark:text-neutral-400">
                <tr>
                  <th className="w-10 px-4 py-3 font-medium">
                    <input
                      type="checkbox"
                      checked={sortedJobs.length > 0 && sortedJobs.every((job) => selected.includes(job.id))}
                      onChange={() => toggleSelectAll(sortedJobs)}
                      title={selected.length ? 'Deselect these rows' : 'Select these rows'}
                      className="accent-neutral-800"
                    />
                  </th>
                  <th className="px-4 py-3 font-medium">Title</th>
                  <th className="px-4 py-3 font-medium">Company</th>
                  <th className="px-4 py-3 font-medium">
                    <button
                      onClick={cycleScoreSort}
                      title="Sort by fit score (skill overlap with your resume + junior fit − reject patterns; hover a score for the breakdown)"
                      className="uppercase tracking-wide hover:text-neutral-800 dark:hover:text-neutral-200"
                    >
                      Fit {scoreDir === 'desc' ? '▼' : scoreDir === 'asc' ? '▲' : '↕'}
                    </button>
                  </th>
                  <th className="px-4 py-3 font-medium">Source</th>
                  <th className="px-4 py-3 font-medium">Posted</th>
                  <th className="px-4 py-3 font-medium">
                    <button
                      onClick={cycleScrapedSort}
                      title="Sort by time scraped (API order → newest → oldest)"
                      className="uppercase tracking-wide hover:text-neutral-800 dark:hover:text-neutral-200"
                    >
                      Scraped {scrapedDir === 'desc' ? '▼' : scrapedDir === 'asc' ? '▲' : '↕'}
                    </button>
                  </th>
                  <th className="px-4 py-3 font-medium" title="Ping-if-no-response reminder (toggle with the Due follow-ups chip above)">
                    Follow-up
                  </th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="sticky right-0 bg-white px-4 py-3 text-right font-medium shadow-[-8px_0_12px_-8px_rgba(0,0,0,0.15)] dark:bg-neutral-900">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {sortedJobs.map((job) => (
                  <Fragment key={job.id}>
                  <tr className="hover:bg-neutral-50 dark:hover:bg-neutral-800/50">
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selected.includes(job.id)}
                        onChange={() => toggleSelect(job.id)}
                        title={`Select "${job.title || 'Untitled'}"`}
                        className="accent-neutral-800"
                      />
                    </td>
                    <td className="px-4 py-3">
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium text-neutral-800 hover:text-black hover:underline dark:text-neutral-200 dark:hover:text-white"
                      >
                        <Highlight text={job.title || 'Untitled'} query={parsedSearch.terms} />
                      </a>
                    </td>
                    <td className="px-4 py-3 text-neutral-600 dark:text-neutral-400">
                      <Highlight text={job.company || '—'} query={parsedSearch.terms} />
                    </td>
                    <td className="px-4 py-3" title={job.score_reason || 'No breakdown recorded (pre-score row)'}>
                      <span
                        className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ${
                          (job.score ?? 0) >= 60
                            ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300'
                            : (job.score ?? 0) >= 45
                              ? 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300'
                              : 'bg-neutral-200 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300'
                        }`}
                      >
                        {job.score ?? 0}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block rounded px-2 py-0.5 text-xs font-medium capitalize ${
                          SOURCE_STYLES[job.source] || 'bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300'
                        }`}
                      >
                        {job.source || '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-neutral-600 dark:text-neutral-400">
                      {fmtDate(job.date_posted)}
                    </td>
                    <td
                      className="whitespace-nowrap px-4 py-3 text-neutral-600 dark:text-neutral-400"
                      title={job.scraped_at ? `First seen ${timeAgo(job.scraped_at)} · last seen ${timeAgo(job.last_seen || job.scraped_at)}` : 'Scrape time unknown'}
                    >
                      {fmtDateTime(job.scraped_at)}
                      <span className="block text-xs text-neutral-400 dark:text-neutral-500">
                        seen {timeAgo(job.last_seen || job.scraped_at)}
                        {isStale(job) && (
                          <span className="ml-1 rounded bg-amber-100 px-1 py-px font-medium text-amber-700 dark:bg-amber-900 dark:text-amber-300" title="Not seen in any scrape for 30+ days — link may be dead">
                            stale
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <input
                        type="date"
                        value={job.follow_up_at ? String(job.follow_up_at).slice(0, 10) : ''}
                        onChange={(e) => setFollowup(job, e.target.value)}
                        title={job.follow_up_at ? `Follow up by ${String(job.follow_up_at).slice(0, 10)}` : 'Set a ping-if-no-response date'}
                        className={`rounded-lg border px-2 py-1 text-xs dark:bg-neutral-800 ${
                          isFollowupDue(job)
                            ? 'border-rose-400 bg-rose-50 text-rose-700 dark:border-rose-600 dark:bg-rose-950 dark:text-rose-300'
                            : 'border-neutral-300 dark:border-neutral-700'
                        }`}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={job.status}
                        onChange={(e) => changeStatus(job, e.target.value)}
                        className={`rounded-full border-0 px-3 py-1 text-xs font-medium ring-1 text-center ${
                          STATUS_STYLES[job.status] || 'bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300'
                        }`}
                        title="Update status"
                      >
                        {STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="sticky right-0 bg-white px-4 py-3 text-right shadow-[-8px_0_12px_-8px_rgba(0,0,0,0.15)] dark:bg-neutral-900">
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => handleApply(job)}
                          disabled={pendingApply === job.id}
                          className="rounded-lg bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
                        >
                          {pendingApply === job.id ? 'Opening…' : 'Apply'}
                        </button>
                        <button
                          onClick={() => openTailor(job)}
                          title="Tailor your resume to this posting (preview + download, you still apply manually)"
                          className="rounded-lg border border-violet-300 px-3 py-1.5 text-xs font-medium text-violet-700 hover:bg-violet-50 dark:border-violet-700 dark:text-violet-300 dark:hover:bg-violet-950"
                        >
                          Tailor
                        </button>
                      </div>
                    </td>
                  </tr>
                  {confirmApplyId === job.id && (
                    <tr className="bg-emerald-50 dark:bg-emerald-950/40">
                      <td colSpan={10} className="px-4 py-2.5">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <span className="font-medium text-neutral-700 dark:text-neutral-200">
                            Did you submit the application for “{job.title || 'Untitled'}”?
                          </span>
                          <button
                            onClick={() => changeStatus(job, 'APPLIED')}
                            title="Yes — move to the Applications tab"
                            className="rounded-lg bg-emerald-600 px-3 py-1.5 font-medium text-white hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                          >
                            Yes, applied ✓
                          </button>
                          <span className="text-neutral-400 dark:text-neutral-500">No —</span>
                          {NO_APPLY_REASONS.map((r) => (
                            <button
                              key={r}
                              onClick={() => changeStatus(job, r)}
                              title={`Mark ${r} instead`}
                              className="rounded-lg border border-neutral-300 px-2.5 py-1.5 font-medium text-neutral-600 hover:bg-neutral-100 dark:border-neutral-600 dark:text-neutral-300 dark:hover:bg-neutral-800"
                            >
                              {r}
                            </button>
                          ))}
                          <button
                            onClick={() => setConfirmApplyId(null)}
                            title="Dismiss (stays REVIEWED)"
                            className="ml-auto rounded px-2 py-1 text-neutral-400 hover:bg-neutral-200 dark:text-neutral-500 dark:hover:bg-neutral-800"
                          >
                            ✕
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </section>
        )}

        {tab === 'filtered' && (
        <section className="mt-6 overflow-x-auto rounded-xl border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          {filteredNote && (
            <p className="border-b border-neutral-200 px-4 py-2 text-sm text-neutral-600 dark:border-neutral-800 dark:text-neutral-400" role="status">
              {filteredNote}
            </p>
          )}
          {loading ? (
            <div className="p-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
              Loading filtered postings…
            </div>
          ) : visibleFiltered.length === 0 ? (
            <div className="p-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
              Nothing held for review. Filtered-out postings from future scrapes
              will appear here with the reason they were dropped.
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="border-b border-neutral-200 bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:bg-neutral-800 dark:text-neutral-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Title</th>
                  <th className="px-4 py-3 font-medium">Company</th>
                  <th className="px-4 py-3 font-medium">Source</th>
                  <th className="px-4 py-3 font-medium">Posted</th>
                  <th className="px-4 py-3 font-medium">Filter reason</th>
                  <th className="px-4 py-3 font-medium">Filtered</th>
                  <th className="sticky right-0 bg-white px-4 py-3 text-right font-medium shadow-[-8px_0_12px_-8px_rgba(0,0,0,0.15)] dark:bg-neutral-900">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {visibleFiltered.map((job) => (
                  <tr key={job.id} className="hover:bg-neutral-50 dark:hover:bg-neutral-800/50">
                    <td className="px-4 py-3">
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium text-neutral-800 hover:text-black hover:underline dark:text-neutral-200 dark:hover:text-white"
                      >
                        <Highlight text={job.title || 'Untitled'} query={parsedSearch.terms} />
                      </a>
                      {job.search_term && (
                        <span className="block text-xs text-neutral-400 dark:text-neutral-500">
                          via “{job.search_term}”
                        </span>
                      )}
                      {job.description && (
                        <details className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
                          <summary className="cursor-pointer hover:underline">
                            Posting text
                          </summary>
                          <p className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap">
                            {job.description}
                          </p>
                        </details>
                      )}
                    </td>
                    <td className="px-4 py-3 text-neutral-600 dark:text-neutral-400">
                      <Highlight text={job.company || '—'} query={parsedSearch.terms} />
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block rounded px-2 py-0.5 text-xs font-medium capitalize ${
                          SOURCE_STYLES[job.source] || 'bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300'
                        }`}
                      >
                        {job.source || '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-neutral-600 dark:text-neutral-400">
                      {fmtDate(job.date_posted)}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className="inline-block rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-900 dark:text-amber-300"
                        title="Why the auto-filter held this posting out"
                      >
                        {job.filter_reason || 'filtered'}
                      </span>
                    </td>
                    <td
                      className="whitespace-nowrap px-4 py-3 text-neutral-600 dark:text-neutral-400"
                      title={job.filtered_at ? `Filtered ${timeAgo(job.filtered_at)}` : ''}
                    >
                      {fmtDateTime(job.filtered_at)}
                      <span className="block text-xs text-neutral-400 dark:text-neutral-500">
                        {timeAgo(job.filtered_at)}
                      </span>
                    </td>
                    <td className="sticky right-0 bg-white px-4 py-3 text-right shadow-[-8px_0_12px_-8px_rgba(0,0,0,0.15)] dark:bg-neutral-900">
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => restoreFiltered(job)}
                          disabled={restoring === job.id}
                          title="Move back to Jobs as NEW so you can apply"
                          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                        >
                          {restoring === job.id ? 'Restoring…' : 'Restore'}
                        </button>
                        <button
                          onClick={() => deleteFiltered(job)}
                          title="Dismiss — it was filtered correctly"
                          className="rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-500 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800"
                        >
                          Dismiss
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        )}

        {tab === 'applications' && (
        <section className="mt-6 overflow-x-auto rounded-xl border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          <div className="flex flex-wrap items-center gap-2 border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
            {[
              ['all', `All (${appCounts.total})`],
              ['interviews', `Interviews (${appCounts.interviews})`],
              ['offers', `Offers (${appCounts.offers})`],
            ].map(([key, label]) => (
              <button
                key={key}
                onClick={() => setAppFilter(key)}
                className={`rounded-full px-3 py-1.5 text-xs font-medium ring-1 transition ${
                  appFilter === key
                    ? 'bg-neutral-900 text-white ring-neutral-900 dark:bg-neutral-100 dark:text-neutral-900 dark:ring-neutral-100'
                    : 'bg-white text-neutral-500 ring-neutral-300 hover:bg-neutral-100 dark:bg-neutral-800 dark:text-neutral-400 dark:ring-neutral-700 dark:hover:bg-neutral-700'
                }`}
              >
                {label}
              </button>
            ))}
            <input
              type="search"
              placeholder="Filter applications…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="min-w-40 flex-1 rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800"
            />
          </div>
          {loading ? (
            <div className="p-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
              Loading applications…
            </div>
          ) : applicationRows.length === 0 ? (
            <div className="p-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
              Nothing here yet. Set a Jobs row to APPLIED and it moves into
              this tab, where you track its interview stage.
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="border-b border-neutral-200 bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:bg-neutral-800 dark:text-neutral-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Title</th>
                  <th className="px-4 py-3 font-medium">Company</th>
                  <th className="px-4 py-3 font-medium">Stage</th>
                  <th className="px-4 py-3 font-medium">Updated</th>
                  <th className="px-4 py-3 text-right font-medium sticky right-0 bg-neutral-50 dark:bg-neutral-800">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {applicationRows.map((job) => (
                  <ApplicationRow key={job.id} job={job} terms={parsedSearch.terms}
                    onStage={setStage} onStatus={changeStatus} onMoveBack={moveBackToJobs} onChanged={fetchAll} />
                ))}
              </tbody>
            </table>
          )}
        </section>
        )}
      </main>

      {tailorJob && (
        <div
          className="fixed inset-0 z-20 flex items-start justify-center overflow-y-auto bg-neutral-900/50 p-4"
          onClick={() => !tailorLoading && setTailorJob(null)}
        >
          <div
            className="mt-8 w-full max-w-3xl rounded-xl border border-neutral-200 bg-white p-6 shadow-xl dark:border-neutral-700 dark:bg-neutral-900"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold">Tailor resume</h2>
                <p className="text-sm text-neutral-500 dark:text-neutral-400">
                  {tailorJob.title} @ {tailorJob.company}
                </p>
              </div>
              <button
                onClick={() => !tailorLoading && setTailorJob(null)}
                className="rounded-lg px-2 py-1 text-neutral-500 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
                title="Close"
              >
                ✕
              </button>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="text-sm text-neutral-600 dark:text-neutral-400">
                Resume
                <select
                  value={tailorResume}
                  onChange={(e) => setTailorResume(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800"
                >
                  {resumes.map((r) => (
                    <option key={r.name} value={r.name}>
                      {r.name}
                      {r.name === defaultResume ? ' (default)' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <p className="self-end text-xs text-neutral-500 dark:text-neutral-400">
                Paste the full posting description below — the tracker stores
                titles only. Bullets are selected/reworded from your resume,
                never invented.
              </p>
            </div>
            <textarea
              value={tailorText}
              onChange={(e) => setTailorText(e.target.value)}
              rows={8}
              placeholder="Paste the full job posting description here…"
              className="mt-3 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-800"
            />
            {tailorError && (
              <p className="mt-2 text-sm text-rose-600 dark:text-rose-400" role="alert">
                {tailorError}
              </p>
            )}
            <div className="mt-3 flex items-center gap-3">
              <button
                onClick={runTailor}
                disabled={tailorLoading}
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-violet-500 dark:hover:bg-violet-600"
              >
                {tailorLoading ? 'Tailoring…' : 'Run tailoring'}
              </button>
              {tailorResult && (
                <span className="text-xs text-neutral-500 dark:text-neutral-400">
                  via {tailorResult.provider}
                </span>
              )}
            </div>

            {tailorResult && (
              <div className="mt-4 space-y-4 border-t border-neutral-200 pt-4 text-sm dark:border-neutral-700">
                <div>
                  <h3 className="font-medium text-neutral-700 dark:text-neutral-300">Summary</h3>
                  <p className="mt-1 text-neutral-600 dark:text-neutral-400">
                    {tailorResult.tailored.summary}
                  </p>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="rounded-lg bg-emerald-50 p-3 dark:bg-emerald-950">
                    <h3 className="font-medium text-emerald-700 dark:text-emerald-300">
                      Consider adding ({tailorResult.suggested_add.length})
                    </h3>
                    <ul className="mt-1 list-disc pl-5 text-emerald-800 dark:text-emerald-200">
                      {tailorResult.suggested_add.map((s) => (
                        <li key={s}>{s} — in posting, not on your resume</li>
                      ))}
                      {tailorResult.suggested_add.length === 0 && (
                        <li>No gaps detected.</li>
                      )}
                    </ul>
                  </div>
                  <div className="rounded-lg bg-amber-50 p-3 dark:bg-amber-950">
                    <h3 className="font-medium text-amber-700 dark:text-amber-300">
                      Consider dropping ({tailorResult.suggested_remove.length})
                    </h3>
                    <ul className="mt-1 list-disc pl-5 text-amber-800 dark:text-amber-200">
                      {tailorResult.suggested_remove.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                      {tailorResult.suggested_remove.length === 0 && (
                        <li>Everything overlaps the posting.</li>
                      )}
                    </ul>
                  </div>
                </div>
                <div>
                  <h3 className="font-medium text-neutral-700 dark:text-neutral-300">
                    Reordered skills
                  </h3>
                  {tailorResult.tailored.skills.map((g) => (
                    <p key={g.group} className="mt-1 text-neutral-600 dark:text-neutral-400">
                      <span className="font-medium">{g.group}: </span>
                      {g.items.join(', ')}
                    </p>
                  ))}
                </div>
                <div>
                  <h3 className="font-medium text-neutral-700 dark:text-neutral-300">
                    Selected bullets
                  </h3>
                  {tailorResult.tailored.experience.map((j, i) => (
                    <div key={i} className="mt-2">
                      <p className="font-medium text-neutral-600 dark:text-neutral-400">
                        {j.title} — {j.company}
                        {(j.location || j.start_date) && (
                          <span className="font-normal text-neutral-500 dark:text-neutral-500">
                            {' '}({[j.location, [j.start_date, j.end_date].filter(Boolean).join(' – ')].filter(Boolean).join(' | ')})
                          </span>
                        )}
                      </p>
                      <ul className="list-disc pl-5 text-neutral-600 dark:text-neutral-400">
                        {j.bullets.map((b, k) => (
                          <li key={k}>{b}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
                <div className="flex items-center gap-3">
                  <button
                    onClick={downloadTailored}
                    className="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-700 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
                  >
                    Download tailored .docx
                  </button>
                  <span className="text-xs text-neutral-500 dark:text-neutral-400">
                    Review it first — then apply manually from the job link.
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value, accent }) {
  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${accent}`} />
        <p className="text-sm text-neutral-500 dark:text-neutral-400">{label}</p>
      </div>
      <p className="mt-1 text-3xl font-semibold text-neutral-800 dark:text-neutral-100">{value}</p>
    </div>
  )
}