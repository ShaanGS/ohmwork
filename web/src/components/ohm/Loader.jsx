import { useEffect, useState } from 'react'

/*
 * LOADING STATE -- a 3x3 pixel grid with a chevron wavefront driving right,
 * a shimmering label, and a live elapsed timer in mono tabular figures. The
 * 650 ms cycle is shorter than the sweep, so two fronts are always in
 * flight. Reduced motion freezes the grid to its dim state via the global
 * rule; the timer still ticks, because the wait is real.
 */

const CHEVRON = Array.from({ length: 9 }, (_, i) => {
  const r = Math.floor(i / 3)
  const c = i % 3
  return (c + Math.abs(r - 1)) * 90
})

export function LoaderGrid({ className = '' }) {
  return (
    <span aria-hidden className={`grid shrink-0 grid-cols-[repeat(3,4px)] gap-[1.5px] ${className}`}>
      {CHEVRON.map((delay, index) => (
        <span
          key={index}
          className="size-[4px] rounded-[1px] bg-ink"
          style={{ opacity: 0.15, animation: `pixel-on 650ms ease-in-out ${delay}ms infinite` }}
        />
      ))}
    </span>
  )
}

function useElapsed() {
  const [ds, setDs] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const t = setInterval(() => setDs(Math.round((Date.now() - started) / 100)), 100)
    return () => clearInterval(t)
  }, [])
  const total = ds / 10
  if (total < 60) return `${total.toFixed(1)}s`
  return `${Math.floor(total / 60)}m ${(total % 60).toFixed(1)}s`
}

export function Shimmer({ children, className = '' }) {
  return (
    <span
      className={`bg-clip-text font-medium text-transparent ${className}`}
      style={{
        backgroundImage: 'linear-gradient(90deg, var(--ink-3) 35%, var(--ink) 50%, var(--ink-3) 65%)',
        backgroundSize: '200% 100%',
        animation: 'shimmer-text 1.6s linear infinite',
      }}
    >
      {children}
    </span>
  )
}

export default function LoadingState({ label, hint }) {
  const elapsed = useElapsed()
  return (
    <div role="status" className="flex flex-col gap-1" style={{ animation: 'fade-in 300ms ease-out both' }}>
      <div className="flex items-center gap-2.5">
        <LoaderGrid />
        <Shimmer className="text-[13px]">{label}</Shimmer>
        <span className="font-mono text-[12px] text-ink-3 tabular-nums">{elapsed}</span>
      </div>
      {hint && <p className="pl-[27px] text-[12.5px] text-ink-3">{hint}</p>}
    </div>
  )
}
