// Possible-duplicate grouping for the Jobs tab.
//
// Pure functions (no React) so the rules stay unit-testable from node.
// Grouping is deliberately conservative: same normalized title at the same
// normalized company. "Accenture" and "Accenture in the Philippines" merge;
// "Engineer (Taguig)" and "Engineer (Makati)" do NOT (locations may differ).

export function normTitle(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

export function normCompany(s) {
  let c = String(s || '').toLowerCase().trim()
  // LinkedIn appends "in the Philippines" to local company names.
  c = c.replace(/\s+in the philippines\s*$/, '').trim()
  c = c.replace(/\s+philippines\s*$/, '').trim()
  // Trailing corporate suffixes ("Acme, Inc." vs "Acme").
  c = c.replace(/[\s,.]+(inc|corp|corporation|llc|ltd|co|company|pvt|plc|gmbh)\.?$/, '').trim()
  return c
    .replace(/[^a-z0-9 ]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

export function groupKey(job) {
  return `${normTitle(job.title)} @ ${normCompany(job.company)}`
}

// Rows the user already archived never seed a group.
const ARCHIVED = new Set(['DUPLICATE', 'EXPIRED'])

export function findDuplicateGroups(jobs) {
  const byKey = new Map()
  for (const job of jobs || []) {
    if (!job || ARCHIVED.has(job.status)) continue
    if (!normTitle(job.title)) continue
    const key = groupKey(job)
    if (!byKey.has(key)) byKey.set(key, [])
    byKey.get(key).push(job)
  }
  return [...byKey.entries()]
    .filter(([, rows]) => rows.length > 1)
    .map(([key, rows]) => ({ key, rows: [...rows].sort((a, b) => a.id - b.id) }))
    .sort((a, b) => b.rows.length - a.rows.length || a.key.localeCompare(b.key))
}

// Keeper default: a row you already acted on wins (APPLIED > REVIEWED >
// NEW > anything else), then earliest scraped, then lowest id.
const KEEPER_RANK = { APPLIED: 0, REVIEWED: 1, NEW: 2 }

export function defaultKeeper(group) {
  const rows = [...group.rows].sort((a, b) => {
    const ra = KEEPER_RANK[a.status] ?? 3
    const rb = KEEPER_RANK[b.status] ?? 3
    if (ra !== rb) return ra - rb
    const ta = new Date(a.scraped_at || 0).getTime()
    const tb = new Date(b.scraped_at || 0).getTime()
    if (ta !== tb) return ta - tb
    return a.id - b.id
  })
  return rows[0].id
}
