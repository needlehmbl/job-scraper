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
  // Mirrors backend dedupe.py normalize_company: strip "in the
  // philippines" anywhere, leading aggregator junk ("careers at ..."),
  // then trailing legal/region suffixes repeatedly ("Acme Corp PH" ->
  // "acme"), so UI groups match DB groups.
  let c = String(s || '').toLowerCase().trim()
  c = c.replace(/\s+in the philippines(?![a-z])/, ' ')
  c = c.replace(/^(careers|jobs)\s+at\s+/, '')
    .replace(/^hiring\s+(at|by)\s+/, '')
    .replace(/^jobs\s+by\s+/, '')
  let toks = c.replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim().split(' ').filter(Boolean)
  const suffix = new Set(['inc', 'incorporated', 'corp', 'corporation', 'co', 'company', 'ltd', 'limited', 'llc', 'plc', 'gmbh', 'pty', 'pvt', 'philippines', 'ph'])
  while (toks.length && suffix.has(toks[toks.length - 1])) toks.pop()
  return toks.join(' ')
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

// Fuzzy near-duplicate scan (review-only, never auto-marks): same
// normalized company with overlapping-but-not-identical titles, e.g.
// aggregator SEO suffixes ("Junior Full Stack Developer: Grow Frontend"
// vs "...: Learn"). Pairs already in an exact group are skipped; the
// rest are linked into connected components. The company gate is what
// keeps distinct roles apart — check thresholds empirically before
// changing FUZZY_OVERLAP.
export function titleTokens(job) {
  return new Set(normTitle(job.title).split(' ').filter(Boolean))
}

const FUZZY_OVERLAP = 0.6

function fuzzyTitleMatch(aTokens, bTokens) {
  if (!aTokens.size || !bTokens.size) return { match: false, subset: false }
  let inter = 0
  for (const t of aTokens) if (bTokens.has(t)) inter++
  // One title's tokens contained in the other's ("... Code Collaborate"
  // vs "... Code Collaborate Deliver") — the classic aggregator
  // SEO-suffix / repost pattern. Tracked separately so the UI can badge
  // subset-linked groups as likely duplicates.
  const subset = inter === Math.min(aTokens.size, bTokens.size)
  if (subset) return { match: true, subset: true }
  const match = inter / Math.max(aTokens.size, bTokens.size) >= FUZZY_OVERLAP
  return { match, subset: false }
}

export function findFuzzyGroups(jobs) {
  const live = (jobs || []).filter(
    (j) => j && !ARCHIVED.has(j.status) && normTitle(j.title) && normCompany(j.company)
  )
  const byCompany = new Map()
  for (const job of live) {
    const c = normCompany(job.company)
    if (!byCompany.has(c)) byCompany.set(c, [])
    byCompany.get(c).push(job)
  }
  const groups = []
  for (const [company, rows] of byCompany) {
    if (rows.length < 2) continue
    const parent = new Map(rows.map((r) => [r.id, r.id]))
    const find = (x) => {
      while (parent.get(x) !== x) {
        parent.set(x, parent.get(parent.get(x)))
        x = parent.get(x)
      }
      return x
    }
    const toks = new Map(rows.map((r) => [r.id, titleTokens(r)]))
    const edges = []
    for (let i = 0; i < rows.length; i++) {
      for (let j = i + 1; j < rows.length; j++) {
        const a = rows[i]
        const b = rows[j]
        if (groupKey(a) === groupKey(b)) continue // exact group owns this pair
        const { match, subset } = fuzzyTitleMatch(toks.get(a.id), toks.get(b.id))
        if (match) {
          parent.set(find(a.id), find(b.id))
          edges.push([a.id, b.id, subset])
        }
      }
    }
    const comps = new Map()
    for (const r of rows) {
      const root = find(r.id)
      if (!comps.has(root)) comps.set(root, [])
      comps.get(root).push(r)
    }
    for (const members of comps.values()) {
      if (members.length < 2) continue
      members.sort((a, b) => a.id - b.id)
      const ids = new Set(members.map((r) => r.id))
      // Likely = every link in the component is a subset relation (one
      // title contains the other), not just overlap ("Frontend" vs
      // "Backend" overlap but neither contains the other).
      const likely = edges
        .filter(([x, y]) => ids.has(x) && ids.has(y))
        .every(([, , sub]) => sub)
      groups.push({
        key: `fuzzy:${company}|${members.map((r) => r.id).join(',')}`,
        rows: members,
        fuzzy: true,
        likely,
        company,
      })
    }
  }
  return groups.sort(
    (a, b) => (b.likely - a.likely) || b.rows.length - a.rows.length || a.key.localeCompare(b.key)
  )
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
