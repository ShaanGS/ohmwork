import { useState } from 'react'

/*
 * TASK ROWS -- one row per design attempt.
 *
 *   pending   spinner ring carrying the attempt number
 *   rejected  red X badge + "Rejected" pill; the row opens to the reason
 *   accepted  green check badge + pill naming what happened
 *
 * Rows enter staggered; details drop down on click with the same
 * grid-template-rows trick a chain-of-thought disclosure uses.
 */

function SpinnerRing({ active, children }) {
  const size = 24
  const stroke = 2
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  return (
    <span className="relative inline-flex shrink-0 items-center justify-center" style={{ width: size, height: size }}>
      <svg
        width={size}
        height={size}
        className="absolute inset-0"
        style={active ? { animation: 'spin 1.1s linear infinite' } : undefined}
      >
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line-strong)" strokeWidth={stroke} />
        {active && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke="var(--ink-3)"
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${c * 0.28} ${c * 0.72}`}
          />
        )}
      </svg>
      <span className="relative text-[10.5px] font-semibold text-ink tabular-nums">{children}</span>
    </span>
  )
}

function Badge({ tone, children }) {
  return (
    <span
      className={`flex size-[22px] shrink-0 items-center justify-center rounded-full text-white ${
        tone === 'red' ? 'bg-red' : 'bg-green'
      }`}
      style={{ animation: 'pop-in 300ms cubic-bezier(0.23,1,0.32,1) both' }}
    >
      {children}
    </span>
  )
}

const XIcon = (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round">
    <path d="M18 6L6 18M6 6l12 12" />
  </svg>
)
const CheckIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20 6L9 17l-5-5" />
  </svg>
)

function Pill({ tone, children }) {
  const cls = tone === 'red'
    ? 'bg-red-tint text-red'
    : tone === 'green'
      ? 'bg-green-tint text-green'
      : 'bg-inset text-ink-3'
  return (
    <span
      className={`inline-flex h-[22px] shrink-0 items-center gap-1.5 rounded-full px-2 text-[11.5px] font-medium ${cls}`}
      style={{ animation: 'fade-in 200ms ease-out both' }}
    >
      {children}
    </span>
  )
}

/* rows: [{ key, index, label, meta, status: 'pending'|'rejected'|'accepted', pill, detail }] */
export default function TaskRows({ rows }) {
  const [open, setOpen] = useState({})
  return (
    <div className="flex w-full flex-col overflow-hidden rounded-card bg-surface shadow-card">
      {rows.map((row, i) => {
        const isOpen = open[row.key] ?? false
        const hasDetail = !!row.detail
        return (
          <div
            key={row.key}
            className="border-b border-line transition-colors duration-300 last:border-0 hover:bg-inset/60"
            style={{ animation: `fade-up 450ms cubic-bezier(0.23,1,0.32,1) ${i * 80}ms both` }}
          >
            <button
              type="button"
              aria-expanded={isOpen}
              disabled={!hasDetail}
              onClick={() => setOpen((cur) => ({ ...cur, [row.key]: !isOpen }))}
              className="flex h-11 w-full items-center gap-2.5 px-3 text-left disabled:cursor-default"
            >
              <span className="flex size-6 shrink-0 items-center justify-center">
                {row.status === 'accepted'
                  ? <Badge tone="green">{CheckIcon}</Badge>
                  : row.status === 'rejected'
                    ? <Badge tone="red">{XIcon}</Badge>
                    : <SpinnerRing active>{row.index}</SpinnerRing>}
              </span>
              <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{row.label}</span>
              {row.meta && <span className="hidden text-[12.5px] text-ink-2 tabular-nums sm:inline">{row.meta}</span>}
              {row.pill && <Pill tone={row.status === 'rejected' ? 'red' : row.status === 'accepted' ? 'green' : 'grey'}>{row.pill}</Pill>}
              <span
                aria-hidden="true"
                className={`-ml-1 flex size-7 shrink-0 items-center justify-center rounded-full text-ink-3 ${hasDetail ? '' : 'invisible'}`}
              >
                <svg
                  width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
                  className="transition-transform duration-300"
                  style={{ transform: isOpen ? 'rotate(180deg)' : 'rotate(0)' }}
                >
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </span>
            </button>

            <div
              className="grid transition-[grid-template-rows,opacity] duration-300"
              style={{
                gridTemplateRows: isOpen ? '1fr' : '0fr',
                opacity: isOpen ? 1 : 0,
                transitionTimingFunction: 'cubic-bezier(0.23, 1, 0.32, 1)',
              }}
            >
              <div className="overflow-hidden">
                <div className="mb-3 grid grid-cols-[24px_1fr] gap-2.5 px-3">
                  <span aria-hidden className="mx-auto h-full w-px bg-line" />
                  <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-ink-2">
{row.detail}
                  </pre>
                </div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
