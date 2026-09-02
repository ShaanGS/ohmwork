import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Download, KeyRound, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import LoadingState from '@/components/ohm/Loader'
import TaskRows from '@/components/ohm/TaskRows'
import Reveal from '@/components/ohm/Reveal'

/*
 * Chat-shaped, on a light page with hairline surfaces.
 *
 * The layout argues one claim: the answer below was checked by an outside
 * tool, and here is what it was checked AGAINST. So the reading comes first
 * and is the loudest thing on the page, every rejected design is shown
 * rather than hidden, and the verified badge names the evaluator. Each of
 * those is a place this project can be wrong in a way that would otherwise
 * look exactly like being right.
 */

const EXAMPLES = [
  { text: 'Design a 2-to-4 decoder with an active-high enable.',
    domain: 'digital' },
  { text: 'Design a 4-to-2 priority encoder with an enable input and a valid output, using basic gates only.',
    domain: 'digital' },
  { text: 'Design a series voltage regulator in LTspice that delivers 9 V to a 1 kOhm load from a 15 V unregulated supply. Report the output voltage and the zener current.',
    domain: 'analog' },
]

async function* sseStream(response) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop()
    for (const chunk of chunks) {
      let name = 'message'
      let data = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event: ')) name = line.slice(7)
        else if (line.startsWith('data: ')) data = line.slice(6)
      }
      if (data) yield [name, JSON.parse(data)]
    }
  }
}

export default function App() {
  const [authorised, setAuthorised] = useState(null)

  useEffect(() => {
    fetch('/api/session')
      .then((r) => r.json())
      .then((body) => setAuthorised(body.authorised))
      .catch(() => setAuthorised(false))
  }, [])

  if (authorised === null) return <div className="h-full bg-background" />
  return authorised
    ? <Solver onExpired={() => setAuthorised(false)} />
    : <Login onIn={() => setAuthorised(true)} />
}

function Logo({ className = '' }) {
  // The owner's mascot, used EXACTLY as supplied (web/public/logo.png) --
  // never redrawn.
  return (
    <img
      src="/logo.png"
      alt=""
      draggable={false}
      className={`select-none object-contain ${className}`}
    />
  )
}

/* ---------- small shared pieces ---------- */

function Kicker({ tone = 'grey', children }) {
  const cls = {
    grey: 'bg-inset text-ink-3',
    green: 'bg-green-tint text-green',
    amber: 'bg-amber-tint text-amber',
    red: 'bg-red-tint text-red',
  }[tone]
  return (
    <span className={`inline-flex h-[22px] items-center gap-1.5 rounded-full px-2.5 text-[11.5px] font-semibold ${cls}`}>
      {children}
    </span>
  )
}

function Dot({ tone = 'grey' }) {
  const cls = { grey: 'bg-ink-3', green: 'bg-green', amber: 'bg-amber', red: 'bg-red' }[tone]
  return <span className={`size-1.5 rounded-full ${cls}`} />
}

function Surface({ tone, className = '', children }) {
  const tint = {
    green: 'bg-[linear-gradient(160deg,#f6ffe0,#fff_55%)]',
    amber: 'bg-[linear-gradient(160deg,#fff7e6,#fff_55%)]',
    red: 'bg-[linear-gradient(160deg,#fff0ee,#fff_55%)]',
  }[tone] || 'bg-surface'
  return (
    <section
      className={`rounded-card shadow-card ${tint} ${className}`}
      style={{ animation: 'fade-up 450ms cubic-bezier(0.23,1,0.32,1) both' }}
    >
      {children}
    </section>
  )
}

function Fact({ label, children }) {
  return (
    <div className="flex gap-2 text-[12.5px]">
      <dt className="shrink-0 text-ink-3">{label}</dt>
      <dd className="min-w-0 break-words text-ink-2">{children}</dd>
    </div>
  )
}

function Mono({ children, className = '' }) {
  return <code className={`font-mono text-[12px] text-ink ${className}`}>{children}</code>
}

/* ---------- reading helpers ----------
 *
 * Numbers arrive as raw floats (2.2789902837896077) and boolean expressions
 * as one unbroken line. Both are TRUE and both are unreadable, so the page
 * formats them for a person -- without ever changing what they say. A value
 * is rounded to four significant figures with an SI prefix; an expression
 * gets a space around each operator so it can wrap. Nothing here computes
 * a new fact: every number still comes from the payload as measured.
 */

const SI = [[1e9, 'G'], [1e6, 'M'], [1e3, 'k'], [1, ''], [1e-3, 'm'], [1e-6, 'µ'], [1e-9, 'n'], [1e-12, 'p']]
const SI_UNITS = new Set(['V', 'A', 'W', 'Hz', 's', 'F', 'H', 'Ω', 'ohm', 'Vpp', 'Vrms'])

function sig(x, n = 4) {
  if (x === 0) return '0'
  return String(Number(x.toPrecision(n)))
}

function fmtValue(value, unit) {
  if (value === null || value === undefined) return '—'
  const v = Number(value)
  if (!Number.isFinite(v)) return String(value)
  const u = unit || ''
  if (SI_UNITS.has(u) && v !== 0) {
    const a = Math.abs(v)
    // Below a picovolt the number is solver noise on a node that sits at
    // zero (MEASURED: 7e-17 V on a clamper's output), and "0.00007316 pV"
    // reads as a measurement. Say what it is.
    if (a < 1e-12) return `≈ 0 ${u}`
    for (const [scale, prefix] of SI) {
      if (a >= scale * 0.9995) return `${sig(v / scale)} ${prefix}${u}`
    }
    return `${sig(v / 1e-12)} p${u}`
  }
  return u ? `${sig(v)} ${u}` : sig(v)
}

function prettyExpr(expr) {
  return String(expr).replace(/\s*([&|^])\s*/g, ' $1 ').replace(/~\s+/g, '~').trim()
}

// The plain-English name of the statistic a target's number is, taken from
// the payload's own `statistic` field. The page never infers this from the
// target's name: a page that asserts a convention the code moved on from is
// the viewer incident all over again.
const STATISTIC_WORDS = {
  mean: 'mean of the waveform',
  rms: 'RMS of the waveform',
  ripple_pp: 'peak-to-peak ripple',
  min: 'minimum',
  max: 'maximum',
}

function Chip({ tone = 'grey', children }) {
  const cls = {
    grey: 'bg-inset text-ink-2',
    green: 'bg-green-tint text-green',
    amber: 'bg-amber-tint text-amber',
  }[tone]
  return <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 font-mono text-[12px] ${cls}`}>{children}</span>
}

function Details({ summary, children, className = '' }) {
  // The facts a careful reader wants and a first-time reader does not:
  // present on the page, one click away, never lost.
  return (
    <details className={`group rounded-[10px] bg-page shadow-hairline ${className}`}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3.5 py-2.5 text-[12.5px] font-medium text-ink-2 select-none [&::-webkit-details-marker]:hidden">
        {summary}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-ink-3 transition-transform group-open:rotate-180"><path d="M6 9l6 6 6-6" /></svg>
      </summary>
      <div className="space-y-3 border-t border-line px-3.5 py-3">{children}</div>
    </details>
  )
}

/* ---------- login ---------- */

function Login({ onIn }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    })
    setBusy(false)
    if (response.ok) onIn()
    else if (response.status === 429) setError('Too many attempts. Wait a moment.')
    else setError('Wrong password.')
  }

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div
        className="w-full max-w-sm rounded-card bg-surface p-7 shadow-raised"
        style={{ animation: 'fade-up 500ms cubic-bezier(0.23,1,0.32,1) both' }}
      >
        <Logo className="mx-auto size-16" />
        <h1 className="mt-4 text-center font-serif text-3xl text-ink">Ohmwork</h1>
        <p className="mt-1.5 text-center text-[13.5px] text-ink-2">
          A lab question in. A simulator-checked circuit out.
        </p>
        <form onSubmit={submit} className="mt-6 space-y-2.5">
          <Input
            type="password"
            autoFocus
            placeholder="passphrase"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="h-10 rounded-[10px] bg-page"
          />
          <Button type="submit" className="h-10 w-full rounded-[10px]" disabled={busy || !password}>
            {busy && <Loader2 className="animate-spin" />}
            {busy ? 'Checking' : 'Enter'}
          </Button>
        </form>
        {error && <p className="mt-3 text-center text-[13px] text-red">{error}</p>}
      </div>
    </div>
  )
}

/* ---------- the solver ---------- */

function Solver({ onExpired }) {
  const desktop = window.ohmworkDesktop
  const [question, setQuestion] = useState('')
  const [asked, setAsked] = useState(null)
  const [running, setRunning] = useState(false)
  const [routing, setRouting] = useState(null)
  const [reading, setReading] = useState(null)
  const [attempts, setAttempts] = useState([])
  const [verified, setVerified] = useState(null)
  const [measured, setMeasured] = useState(null)
  const [refused, setRefused] = useState(null)
  const [unavailable, setUnavailable] = useState(null)
  const [error, setError] = useState(null)
  const [showSettings, setShowSettings] = useState(false)
  const [status, setStatus] = useState(null)
  const bottom = useRef(null)

  useEffect(() => {
    fetch('/api/status')
      .then((r) => (r.ok ? r.json() : null))
      .then(setStatus)
      .catch(() => setStatus(null))
  }, [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [asked, routing, reading, attempts, verified, measured, refused,
      unavailable, error])

  async function run() {
    const text = question.trim()
    if (!text || running) return
    setRunning(true)
    setAsked(text)
    setQuestion('')
    setRouting(null)
    setReading(null)
    setAttempts([])
    setVerified(null)
    setMeasured(null)
    setRefused(null)
    setUnavailable(null)
    setError(null)

    let response
    try {
      response = await fetch('/api/solve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text }),
      })
    } catch {
      // A dead server is NOT a failed design. `connection: true` gets its
      // own card: rendering this as "no verified circuit" told a person
      // their circuit failed when nothing was ever asked.
      setError({ message: 'The local solver could not be reached. It may '
        + 'have stopped. Restart Ohmwork and ask again.', connection: true })
      setRunning(false)
      return
    }

    if (response.status === 401) { onExpired(); return }
    if (!response.ok) {
      setError({ message: (await response.json()).detail || 'refused' })
      setRunning(false)
      return
    }

    try {
      for await (const [name, data] of sseStream(response)) {
        if (name === 'routing') setRouting(data)
        else if (name === 'reading') setReading(data)
        else if (name === 'attempt') {
          setAttempts((previous) => [
            ...previous.filter((a) => a.index !== data.index), data,
          ].sort((a, b) => a.index - b.index))
        } else if (name === 'verified') setVerified(data)
        else if (name === 'measured') setMeasured(data)
        else if (name === 'refused') setRefused(data)
        else if (name === 'unavailable') setUnavailable(data)
        else if (name === 'error') setError(data)
      }
    } catch {
      // The stream broke mid-solve. Same fact as above, at a later moment.
      setError({ message: 'The connection to the local solver was lost '
        + 'mid-solve. Restart Ohmwork and ask again.', connection: true })
    }
    setRunning(false)
  }

  const settled = !!verified || !!measured
  const working = running && !verified && !measured && !error && !refused && !unavailable

  return (
    <div className="flex h-full flex-col bg-background">
      <Header status={status} desktop={desktop} onSettings={() => setShowSettings((open) => !open)} />

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-4 px-5 py-8">
          {showSettings && desktop && <DesktopSettings desktop={desktop} onDone={() => setShowSettings(false)} />}
          {!asked && <Intro onPick={setQuestion} status={status}
                            desktop={desktop}
                            onOpenSettings={() => setShowSettings(true)} />}

          {asked && <Asked text={asked} />}
          {routing && <Routing routing={routing} />}
          {refused && <Refused refused={refused} />}
          {unavailable && <Unavailable info={unavailable} />}
          {reading && <Reading reading={reading} />}

          {attempts.length > 0 && <Attempts attempts={attempts} settled={settled} verified={verified} measured={measured} />}

          {verified && <Verified verified={verified} />}
          {measured && <Measured measured={measured} />}
          {error && <Failed error={error} />}
          {working && <Working analog={routing?.domain === 'analog'} stage={attempts.length ? 'design' : reading ? 'design' : routing ? 'reading' : 'routing'} />}
          <div ref={bottom} />
        </div>
      </main>

      <Composer
        question={question}
        setQuestion={setQuestion}
        onRun={run}
        running={running}
      />
    </div>
  )
}

function Header({ status, desktop, onSettings }) {
  const ok = status && status.providers.length > 0 && status.analog.available
    && status.digital.available && status.digital.verification !== 'internal'
  return (
    <header className="sticky top-0 z-10 border-b border-line bg-page/85 backdrop-blur">
      <div className="mx-auto flex h-14 w-full max-w-3xl items-center justify-between px-5">
        <Logo className="size-8" />
        <div className="flex items-center gap-2">
          {status && (
            <span className="hidden items-center gap-1.5 text-[12px] text-ink-3 sm:inline-flex" title={ok ? 'Both evaluators found, a key is set' : 'Something is missing; see below'}>
              <Dot tone={ok ? 'green' : 'amber'} />
              {ok ? 'ready' : 'setup needed'}
            </span>
          )}
          {desktop && (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              title="Model key settings"
              aria-label="Model key settings"
              onClick={onSettings}
              className="rounded-full text-ink-2"
            >
              <KeyRound />
            </Button>
          )}
        </div>
      </div>
    </header>
  )
}

function DesktopSettings({ desktop, onDone }) {
  const providers = [
    ['GROQ_API_KEY', 'Groq'],
    ['GEMINI_API_KEY', 'Google Gemini'],
    ['MISTRAL_API_KEY', 'Mistral'],
    ['OPENROUTER_API_KEY', 'OpenRouter'],
    ['CEREBRAS_API_KEY', 'Cerebras'],
  ]
  const [keys, setKeys] = useState(() => Object.fromEntries(providers.map(([name]) => [name, ''])))
  const [state, setState] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    desktop.providerState().then(setState).catch(() => {
      setError('Secure credential storage is unavailable on this computer.')
    })
  }, [desktop])

  async function save(event) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      await desktop.saveProviderKeys(keys)
      // Electron restarts immediately after accepting it. This text is useful
      // when a platform delays that restart by a moment.
      setKeys(Object.fromEntries(providers.map(([name]) => [name, ''])))
    } catch (reason) {
      setSaving(false)
      setError(reason?.message || 'The key could not be stored.')
    }
  }

  return (
    <Surface className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[15px] font-semibold text-ink">Model keys</p>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
            Stored encrypted by your operating system. Sent to the local solver
            process, never to this page, and never uploaded by Ohmwork.
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onDone} className="rounded-full text-ink-2">Close</Button>
      </div>
      <form onSubmit={save} className="mt-4 space-y-2">
        {providers.map(([name, label]) => (
          <label key={name} className="grid gap-1 sm:grid-cols-[9rem_1fr] sm:items-center">
            <span className="text-[13.5px] text-ink">{label}</span>
            <Input
              type="password"
              autoComplete="off"
              placeholder={state?.configured.includes(name) ? 'saved. Enter a replacement only' : 'API key (optional)'}
              value={keys[name]}
              onChange={(event) => setKeys((current) => ({ ...current, [name]: event.target.value }))}
              disabled={saving || state?.encryptionAvailable === false}
              className="h-9 rounded-[10px] bg-page"
            />
          </label>
        ))}
        <div className="flex items-center gap-3 pt-1">
          <Button type="submit" className="rounded-full" disabled={saving || !Object.values(keys).some((key) => key.trim().length >= 8) || state?.encryptionAvailable === false}>
            {saving && <Loader2 className="animate-spin" />}
            {saving ? 'Restarting' : 'Save keys'}
          </Button>
          {state && (
            <span className="text-[12px] text-ink-3">
              configured: {state.configured.length ? state.configured.join(', ') : 'none'}
            </span>
          )}
        </div>
      </form>
      {error && <p className="mt-2 text-[12.5px] text-red">{error}</p>}
    </Surface>
  )
}

function FirstRun({ status, desktop, onOpenSettings }) {
  // PRD gap 3: say what is missing PLAINLY, on the screen where it matters,
  // once -- before the first question, not as a confusing failure after it.
  // When nothing is missing, the header's green dot says what was found.
  if (!status) return null
  const noKey = status.providers.length === 0
  const noLtspice = !status.analog.available
  // No Logisim means digital questions are REFUSED, not answered by some
  // weaker evaluator: there is none. The status detail says so.
  const internalOnly = !status.digital.available
    || status.digital.verification === 'internal'
  if (!noKey && !noLtspice && !internalOnly) return null

  return (
    <div className="mb-6 space-y-3">
      {noKey && (
        <Surface tone="amber" className="p-4">
          <div className="flex items-center gap-2">
            <Kicker tone="amber"><Dot tone="amber" /> No model key</Kicker>
            <span className="text-[13.5px] text-ink">Nothing can be designed yet.</span>
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-ink-2">
            {desktop
              ? 'Click the key in the top-right corner and paste a free provider key. '
              : 'Set a provider key in the server’s environment. '}
            Free keys:{' '}
            {Object.entries(status.signup).map(([name, url], index) => (
              <span key={name}>
                {index > 0 && ' · '}
                <a href={url} target="_blank" rel="noreferrer" className="text-green underline-offset-2 hover:underline">{name}</a>
              </span>
            ))}
          </p>
          {desktop && (
            <Button size="sm" onClick={onOpenSettings} className="mt-3 rounded-full">
              <KeyRound /> Add a key
            </Button>
          )}
        </Surface>
      )}
      {noLtspice && (
        <p className="text-[12.5px] leading-relaxed text-ink-3">
          {status.analog.detail.split('https://')[0]}
          <a className="text-ink-2 underline underline-offset-2"
             href={`https://${status.analog.detail.split('https://')[1] || ''}`}
             target="_blank" rel="noreferrer">
            download LTspice
          </a>
        </p>
      )}
      {internalOnly && (
        <Surface tone="amber" className="p-4">
          <p className="text-[13px] leading-relaxed text-amber">{status.digital.detail}</p>
        </Surface>
      )}
    </div>
  )
}

function Intro({ onPick, status, desktop, onOpenSettings }) {
  return (
    <div style={{ animation: 'fade-up 500ms cubic-bezier(0.23,1,0.32,1) both' }}>
      <FirstRun status={status} desktop={desktop} onOpenSettings={onOpenSettings} />
      <h1 className="font-serif text-[44px] leading-[1.05] tracking-[-0.01em] text-ink">
        Ask a lab question.
      </h1>
      <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-ink-2">
        Digital gets a Logisim <Mono>.circ</Mono> and its truth table.
        Analog gets an LTspice <Mono>.asc</Mono> and the measured numbers.
        Nothing reaches you that the simulator did not check.
      </p>

      <div className="mt-7 overflow-hidden rounded-card bg-surface shadow-card">
        {EXAMPLES.map(({ text, domain }, i) => (
          <button
            key={text}
            onClick={() => onPick(text)}
            className="flex w-full items-center gap-3 border-b border-line px-3.5 py-3 text-left transition-colors last:border-0 hover:bg-inset/70"
            style={{ animation: `fade-up 450ms cubic-bezier(0.23,1,0.32,1) ${120 + i * 80}ms both` }}
          >
            <span className="w-[62px] shrink-0 font-mono text-[10.5px] tracking-[0.08em] text-ink-3 uppercase">{domain}</span>
            <span className="min-w-0 flex-1 text-[13.5px] text-ink">{text}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-ink-3"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
          </button>
        ))}
      </div>
      <p className="mt-4 text-[12.5px] leading-relaxed text-ink-3">
        Analog needs LTspice on this machine. Without it, an analog question is refused with the download named, never answered unverified.
      </p>
    </div>
  )
}

function Asked({ text }) {
  return (
    <div className="flex justify-end" style={{ animation: 'fade-up 350ms cubic-bezier(0.23,1,0.32,1) both' }}>
      <div className="max-w-[85%] rounded-[18px] rounded-br-[6px] bg-ink px-4 py-2.5 text-[14px] leading-relaxed text-white">
        {text}
      </div>
    </div>
  )
}

function Agent({ children }) {
  // The mascot is the agent's face, and only ever appears where the agent
  // is speaking: once per turn, not in every card.
  return (
    <div className="flex items-start gap-2.5" style={{ animation: 'fade-up 350ms cubic-bezier(0.23,1,0.32,1) both' }}>
      <Logo className="mt-0.5 size-6 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

function Working({ analog, stage }) {
  const label = stage === 'routing' ? 'Reading the question'
    : stage === 'reading' ? 'Writing the reading'
      : analog ? 'Designing, then running LTspice' : 'Designing, then asking Logisim'
  const hint = analog
    ? 'An analog solve runs LTspice for every attempt, so this can take a few minutes.'
    : 'A digital solve takes about a minute.'
  return <LoadingState label={label} hint={hint} />
}

function Routing({ routing }) {
  // Which half answers is a guess made from the question's words, so it is
  // disclosed, exactly as the CLI prints it, before anything runs.
  return (
    <Agent>
      <p className="pt-0.5 text-[13px] text-ink-2">
        Read as {routing.domain === 'analog' ? 'an' : 'a'}{' '}
        <span className="font-semibold text-ink uppercase">{routing.domain}</span>{' '}
        question: {routing.reason}
      </p>
    </Agent>
  )
}

function Reading({ reading }) {
  // The one block on the page a person is asked to actually READ, so it is
  // laid out rather than dumped: signals as chips, one expression per line
  // that wraps, targets as a list with what each is measured on. The text
  // form is kept behind a disclosure for anyone who wants it verbatim. The
  // facts are the payload's; only the arrangement is the page's.
  const structured = reading.expressions && Object.keys(reading.expressions).length > 0
    ? <DigitalReading reading={reading} />
    : reading.targets
      ? <AnalogReading reading={reading} />
      : <Reveal text={reading.spec || reading.intent} className="mt-4 text-ink" />
  return (
    <Surface tone="amber" className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Kicker tone="amber"><Dot tone="amber" /> Step 1 · check the reading</Kicker>
        <span className="text-[12px] text-amber">the one check only you can do</span>
      </div>
      <p className="mt-3 text-[15px] font-semibold text-ink">
        {reading.basis === 'part'
          ? 'Here is how your question was understood. The answer is checked against the real chip.'
          : 'Here is how your question was understood.'}
      </p>
      {structured}
      <p className="mt-4 border-t border-amber/20 pt-3 text-[12.5px] leading-relaxed text-ink-2">
        {reading.basis === 'part'
          ? 'Your question names a part, so the answer below is checked against that chip’s own measured behaviour and the wiring map printed with it. Make sure the signal names and choices above are what you meant.'
          : 'If this is not what you asked, stop here. Everything below proves the circuit does THIS. Nothing below can tell whether THIS is your question.'}
      </p>
    </Surface>
  )
}

function DigitalReading({ reading }) {
  const notes = reading.notes || []
  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-[12.5px]">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-ink-3">inputs</span>
          {(reading.inputs || []).map((name) => <Chip key={name}>{name}</Chip>)}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-ink-3">outputs</span>
          {(reading.outputs || []).map((name) => <Chip key={name} tone="amber">{name}</Chip>)}
        </div>
      </div>
      <div className="space-y-1.5">
        {Object.entries(reading.expressions).map(([name, expr], i) => (
          <div
            key={name}
            className="rounded-[10px] bg-page/80 px-3 py-2 font-mono text-[12.5px] leading-relaxed shadow-hairline"
            style={{ animation: `resolve 420ms cubic-bezier(0.23,1,0.32,1) ${i * 70}ms both` }}
          >
            <span className="font-semibold text-ink">{name}</span>
            <span className="text-ink-3"> = </span>
            <span className="text-ink-2 [overflow-wrap:anywhere]">{prettyExpr(expr)}</span>
          </div>
        ))}
      </div>
      {notes.length > 0 && (
        <ul className="space-y-1 text-[13px] leading-relaxed text-ink-2">
          {notes.map((note) => (
            <li key={note} className="flex gap-2"><span className="mt-[7px] size-1 shrink-0 rounded-full bg-amber" /><span>{note}</span></li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AnalogReading({ reading }) {
  const targets = reading.targets || []
  const stated = reading.stated || []
  const notes = reading.notes || []
  return (
    <div className="mt-3 space-y-3">
      <p className="text-[13.5px] leading-relaxed text-ink">
        <span className="text-ink-3">Circuit: </span>{reading.topology}
        {reading.frequency ? <><span className="text-ink-3"> · source: </span>{fmtValue(reading.frequency, 'Hz')}</> : null}
      </p>
      <div className="overflow-hidden rounded-[10px] bg-page/80 shadow-hairline">
        {targets.map((target, i) => (
          <div
            key={target.name}
            className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line/70 px-3 py-2 text-[13px] last:border-0"
            style={{ animation: `resolve 420ms cubic-bezier(0.23,1,0.32,1) ${i * 70}ms both` }}
          >
            <div className="min-w-0">
              <span className="text-ink">{target.quantity}</span>
              <span className="ml-2 font-mono text-[11.5px] text-ink-3">{target.where}</span>
            </div>
            {target.checked
              ? <span className="font-medium text-green">checked: {target.wanted}</span>
              : <span className="text-ink-3">reported, not checked</span>}
          </div>
        ))}
      </div>
      {stated.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
          <span className="mr-0.5 text-ink-3">stated in the question</span>
          {stated.map((s) => (
            <Chip key={s.what}>{s.what} = {s.value}{s.unit ? ` ${s.unit}` : ''}</Chip>
          ))}
        </div>
      )}
      {notes.length > 0 && (
        <div>
          <p className="text-[12px] text-ink-3">Chosen here, because the question left it open</p>
          <ul className="mt-1 space-y-1 text-[13px] leading-relaxed text-ink-2">
            {notes.map((note) => (
              <li key={note} className="flex gap-2"><span className="mt-[7px] size-1 shrink-0 rounded-full bg-amber" /><span>{note}</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Attempts({ attempts, settled, verified, measured }) {
  const rows = attempts.map((attempt) => {
    const rejected = attempt.status === 'rejected'
    const accepted = !rejected && settled
    const acceptedPill = verified ? 'Verified' : measured ? (measured.checked ? 'Met the intent' : 'Ran in regime') : 'Accepted'
    return {
      key: attempt.index,
      index: attempt.index,
      label: `Design attempt ${attempt.index}`,
      meta: rejected ? attempt.failure.split('\n')[0].slice(0, 56) + (attempt.failure.split('\n')[0].length > 56 ? '…' : '') : accepted ? 'handed to the evaluator' : 'designing',
      status: rejected ? 'rejected' : accepted ? 'accepted' : 'pending',
      pill: rejected ? 'Rejected' : accepted ? acceptedPill : null,
      detail: rejected ? attempt.failure : null,
    }
  })
  return <TaskRows rows={rows} />
}

function BitTable({ columns, rows, inputs }) {
  return (
    <div className="overflow-x-auto rounded-[10px] shadow-hairline">
      <table className="w-full border-collapse font-mono text-[12.5px]">
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th key={column} className={`border-b border-line px-2.5 py-1.5 text-center font-medium text-ink-3 ${index === inputs ? 'border-l' : ''}`}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} style={{ animation: `fade-in 300ms ease-out ${Math.min(r, 32) * 25}ms both` }}>
              {row.map((bit, index) => {
                const out = index >= inputs
                return (
                  <td key={index} className={`border-b border-line/60 px-2.5 py-1 text-center tabular-nums ${index === inputs ? 'border-l border-l-line' : ''} ${out && bit ? 'bg-green-tint font-semibold text-ink' : out ? 'text-ink-3' : 'text-ink-2'}`}>{bit}</td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DownloadButton({ href, children }) {
  return (
    <Button asChild size="sm" className="rounded-full bg-lime text-ink hover:bg-[#d6ff66]">
      <a href={href}><Download /> {children}</a>
    </Button>
  )
}

function Basis({ basis }) {
  // WHAT it was checked against, and what it does NOT prove. "Verified" alone
  // is the same badge over two different claims: algebra read from the
  // question, or a real part's own measured behaviour. Rendered inside the
  // details disclosure: present on every answer, one click from the number.
  if (!basis) return null
  return (
    <div className="space-y-2">
      <p className="font-mono text-[10.5px] tracking-[0.08em] text-ink-3 uppercase">Checked against</p>
      <p className="text-[13px] leading-relaxed text-ink">{basis.headline}</p>
      <p className="font-mono text-[10.5px] tracking-[0.08em] text-ink-3 uppercase">Not proven by any check here</p>
      <p className="text-[13px] leading-relaxed text-amber">{basis.limit}</p>
    </div>
  )
}

function WiringMap({ basis }) {
  // For a part question this IS the thing a person must check: which of the
  // question's signals landed on which pin of the real chip. It stays in
  // plain view, never behind a disclosure.
  if (!basis || basis.kind !== 'part' || !basis.reading) return null
  return (
    <div className="rounded-[10px] bg-amber-tint/60 p-3.5">
      <p className="font-mono text-[10.5px] tracking-[0.08em] text-amber uppercase">Wiring map · check this against your question</p>
      <pre className="mt-2 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] text-ink-2">{basis.reading}</pre>
    </div>
  )
}

function Meta({ items }) {
  // One quiet line of provenance: who checked it, how, who designed it.
  return (
    <p className="text-[12px] leading-relaxed text-ink-3">
      {items.filter(Boolean).map((item, i) => (
        <span key={i}>{i > 0 && <span className="mx-1.5">·</span>}{item}</span>
      ))}
    </p>
  )
}

function Verified({ verified }) {
  const external = verified.verification === 'external'
  const rows = verified.rows.length
  return (
    <Surface tone="green" className="space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span
            className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-green text-white"
            style={{ animation: 'stamp 500ms cubic-bezier(0.2,0.9,0.3,1.4) both' }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
          </span>
          <div>
            <p className="text-[16px] font-semibold text-ink">
              Verified: all {rows} rows match
            </p>
            <p className="mt-0.5 text-[13px] text-ink-2">
              {external
                ? <>{verified.evaluator} evaluated the file below and every row agreed with the reading. </>
                : <>Computed by Ohmwork’s own evaluator, not an outside tool, so this is a weaker claim. </>}
              {verified.attempts > 1 && <>{verified.attempts - 1} earlier design{verified.attempts === 2 ? ' was' : 's were'} rejected first.</>}
            </p>
          </div>
        </div>
        <DownloadButton href={`/api/circuit/${verified.download}`}>Download .circ</DownloadButton>
      </div>

      <WiringMap basis={verified.basis} />

      <BitTable columns={verified.columns} rows={verified.rows} inputs={verified.input_count} />

      <Meta items={[
        `checked by ${verified.evaluator}`,
        external ? 'an outside tool' : 'INTERNAL evaluator',
        `designed by ${verified.designed_by}`,
      ]} />

      <Details summary="What this proves, and what it does not">
        <Basis basis={verified.basis} />
        <p className="text-[12.5px] leading-relaxed text-ink-3">
          The layout inside the file is generated mechanically: inputs in a
          left column, gates in columns by logic depth. Correct, not pretty.
          Open it in Logisim and it will evaluate exactly as the table above.
        </p>
      </Details>
    </Surface>
  )
}

function Measured({ measured }) {
  // The analog answer. DELIBERATELY not the Verified card and not the word
  // "verified": numbers checked against the question's own figures are a
  // weaker claim than rows checked against an exhaustive table, and the two
  // must not look alike. When the question stated no figure, the headline
  // says nothing numeric was checked instead of reading as a pass.
  const external = measured.verification === 'external'
  const nothingChecked = !measured.checked
  const checkedOutcomes = measured.outcomes.filter((o) => o.checked)
  const passed = checkedOutcomes.filter((o) => o.ok).length
  const allOk = passed === checkedOutcomes.length
  const failed = measured.regimes_failed.length > 0
  const tone = nothingChecked || failed || !allOk ? 'amber' : 'green'
  // The headline counts PASSES over CHECKS. The server never returns a
  // measured result with a missed target (the loop retries instead), but a
  // pass-shaped sentence over a miss is the one thing this card must never
  // print, so the count is computed here rather than assumed.
  const headline = nothingChecked
    ? 'Simulated. No number could be checked.'
    : allOk
      ? `Meets the question’s figures: ${passed} of ${checkedOutcomes.length} checked`
      : `Missed ${checkedOutcomes.length - passed} of ${checkedOutcomes.length} checked figures`
  const sub = nothingChecked
    ? `The question stated no figure to hit, so ${measured.evaluator} ran the circuit and reported these values. None of them could pass or fail.`
    : allOk
      ? `${measured.evaluator} ran the circuit and every figure the question stated came out inside tolerance.`
      : `${measured.evaluator} ran the circuit; the rows marked red are outside the tolerance the reading set.`
  const showWanted = measured.outcomes.some((o) => o.checked)
  return (
    <Surface tone={tone} className="space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <Kicker tone={tone}><Dot tone={tone} /> Measured</Kicker>
          </div>
          <p className="mt-2 text-[16px] font-semibold text-ink">{headline}</p>
          <p className="mt-0.5 text-[13px] leading-relaxed text-ink-2">
            {sub}
            {measured.attempts > 1 && <> {measured.attempts - 1} earlier design{measured.attempts === 2 ? ' was' : 's were'} rejected first.</>}
          </p>
        </div>
        <DownloadButton href={`/api/circuit/${measured.download}`}>Download .asc</DownloadButton>
      </div>

      <div className="overflow-x-auto rounded-[10px] shadow-hairline">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="font-mono text-[11px] text-ink-3">
              <th className="border-b border-line px-3 py-1.5 text-left font-medium">quantity</th>
              <th className="border-b border-line px-3 py-1.5 text-right font-medium">measured</th>
              {showWanted && <th className="border-b border-line px-3 py-1.5 text-left font-medium">target</th>}
              <th className="border-b border-line px-3 py-1.5 text-left font-medium">result</th>
            </tr>
          </thead>
          <tbody>
            {measured.outcomes.map((outcome, i) => (
              <tr key={outcome.name} style={{ animation: `fade-in 300ms ease-out ${i * 60}ms both` }}>
                <td className="border-b border-line/60 px-3 py-2 text-ink">
                  {outcome.quantity || outcome.name}
                  {outcome.statistic && STATISTIC_WORDS[outcome.statistic] && (
                    <span className="block text-[11.5px] text-ink-3">{STATISTIC_WORDS[outcome.statistic]}</span>
                  )}
                </td>
                <td className="border-b border-line/60 px-3 py-2 text-right font-mono text-[13px] font-medium text-ink tabular-nums whitespace-nowrap">
                  {fmtValue(outcome.measured, outcome.unit)}
                </td>
                {showWanted && (
                  <td className="border-b border-line/60 px-3 py-2 text-ink-2">{outcome.checked ? outcome.wanted : '—'}</td>
                )}
                <td className="border-b border-line/60 px-3 py-2 whitespace-nowrap">
                  {!outcome.checked
                    ? <span className="text-ink-3">reported only</span>
                    : outcome.ok
                      ? <span className="font-medium text-green">within tolerance</span>
                      : <span className="font-medium text-red">{outcome.reason || 'missed'}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {failed && (
        <p className="text-[12.5px] leading-relaxed text-red">
          Regime assertions that FAILED, so the numbers above touching those runs are not reliable: {measured.regimes_failed.join('; ')}
        </p>
      )}
      {measured.warnings.length > 0 && (
        <p className="text-[12.5px] leading-relaxed text-amber">{measured.warnings.join(' · ')}</p>
      )}

      <p className="text-[12.5px] leading-relaxed text-amber">
        Not proven: that this is a <em>good</em> design, or that the reading above is what your question meant.
      </p>

      <Meta items={[
        `measured by ${measured.evaluator}`,
        external ? 'an outside simulator' : 'INTERNAL evaluator',
        `${measured.regimes_held} regime check${measured.regimes_held === 1 ? '' : 's'} held`,
        `designed by ${measured.designed_by}`,
      ]} />

      <Details summary="What this proves, what it does not, and what the file is">
        <Basis basis={measured.basis} />
        <p className="text-[12.5px] leading-relaxed text-ink-3">{measured.file_note}</p>
      </Details>
    </Surface>
  )
}

function Refused({ refused }) {
  // The message is written as paragraphs: what was refused, the evidence, and
  // finally what to do instead. The last one is the advice.
  const paragraphs = refused.message.split('\n\n')
  const advice = paragraphs[paragraphs.length - 1]
  const rest = paragraphs.slice(0, -1)
  return (
    <Surface tone="amber" className="space-y-3 p-5">
      <div className="flex items-center gap-2.5">
        <Kicker tone="amber"><Dot tone="amber" /> Refused</Kicker>
        <p className="text-[15px] font-semibold text-ink">Not a question this can answer</p>
      </div>
      {rest.map((paragraph) => (
        <p key={paragraph} className="text-[13.5px] leading-relaxed text-ink-2">{paragraph}</p>
      ))}
      <p className="border-t border-amber/20 pt-3 text-[12.5px] leading-relaxed text-ink-3">{advice}</p>
    </Surface>
  )
}

function Unavailable({ info }) {
  // A THIRD outcome, and it needs to look like neither of the others. "no
  // verified circuit" in red says the circuit failed; this says nobody could
  // be asked, which is not a fact about the question at all.
  return (
    <Surface className="space-y-3 p-5">
      <div className="flex items-center gap-2.5">
        <Kicker><Dot /> Unavailable</Kicker>
        <p className="text-[15px] font-semibold text-ink">Every model provider is busy</p>
      </div>
      <p className="text-[13.5px] leading-relaxed text-ink-2">
        Nothing was designed and nothing was wrong with your question. There
        was simply nobody to ask. These are free accounts, and free accounts
        run out.
      </p>
      <dl className="space-y-1 rounded-[10px] bg-page p-3 text-[12.5px] shadow-hairline">
        {(info.members || []).map(([name, reason]) => (
          <div key={name} className="flex gap-2">
            <dt className="w-24 shrink-0 font-mono text-ink-3">{name}</dt>
            <dd className="text-ink-2">{reason}</dd>
          </div>
        ))}
      </dl>
      <p className="text-[12.5px] leading-relaxed text-ink-3">
        Try again in a few minutes, or add another provider key. Nothing about the question needs changing.
      </p>
    </Surface>
  )
}

function Failed({ error }) {
  // Two different facts share this card's shape and must not share its
  // words: a design the evaluator rejected, and a solver that could not be
  // reached at all. The second says nothing about the circuit or the
  // question, so it gets neither the "no verified circuit" headline nor the
  // evaluator boilerplate.
  if (error.connection) {
    return (
      <Surface className="space-y-3 p-5">
        <div className="flex items-center gap-2.5">
          <Kicker><Dot /> Offline</Kicker>
          <p className="text-[15px] font-semibold text-ink">The local solver is not reachable</p>
        </div>
        <p className="text-[13.5px] leading-relaxed text-ink-2">{error.message}</p>
        <p className="text-[12.5px] leading-relaxed text-ink-3">Nothing was designed and nothing is wrong with your question.</p>
      </Surface>
    )
  }
  // A provider that could not be reached is "nobody to ask", the same fact
  // the Unavailable card renders. The server now raises it as its own event;
  // this branch catches the wording in case an older backend still wraps it,
  // because the red card's evaluator boilerplate over a rate limit told the
  // owner their circuit failed when nothing was ever asked.
  if (/could not be reached/.test(error.message)) {
    const members = [...error.message.matchAll(/^\s+(\w+):\s+(.+)$/gm)].map((m) => [m[1], m[2]])
    return <Unavailable info={{ message: error.message, members }} />
  }
  // A failure at the READING stage is a different fact from a failed
  // circuit: no circuit was ever designed, and the evaluator was never
  // asked. The owner hit this live with the evaluator boilerplate under
  // it, which told them their circuit failed when none existed.
  const atReading = /^(spec|intent):/.test(error.message)
    || error.message.startsWith('the model kept producing')
  if (atReading) {
    return (
      <Surface tone="amber" className="space-y-3 p-5">
        <div className="flex items-center gap-2.5">
          <Kicker tone="amber"><Dot tone="amber" /> Reading failed</Kicker>
          <p className="text-[15px] font-semibold text-ink">The question could not be read into a checkable form</p>
        </div>
        <pre className="rounded-[10px] bg-page p-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] text-ink-2 shadow-hairline">{error.message}</pre>
        <p className="text-[12.5px] leading-relaxed text-ink-3">
          No circuit was designed and the evaluator was never asked. This failed at the reading step. Asking again often works, since models vary run to run. Rephrasing helps when it does not.
        </p>
      </Surface>
    )
  }
  return (
    <Surface tone="red" className="space-y-3 p-5">
      <div className="flex items-center gap-2.5">
        <Kicker tone="red"><Dot tone="red" /> Failed</Kicker>
        <p className="text-[15px] font-semibold text-ink">No verified circuit</p>
      </div>
      <pre className="rounded-[10px] bg-page p-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] text-ink-2 shadow-hairline">{error.message}</pre>
      <p className="text-[12.5px] leading-relaxed text-ink-3">
        Nothing is returned when the evaluator disagrees. A circuit that failed its own specification is worse than no circuit, because it looks like an answer.
      </p>
    </Surface>
  )
}

function Composer({ question, setQuestion, onRun, running }) {
  function onKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      onRun()
    }
  }

  return (
    <div className="bg-page/85 backdrop-blur">
      <div className="mx-auto w-full max-w-3xl px-5 pt-2 pb-5">
        <div className="rounded-[18px] bg-surface p-2 shadow-raised transition-shadow focus-within:shadow-[0_1px_2px_rgba(18,20,16,0.04),0_12px_40px_-12px_rgba(18,20,16,0.16),0_0_0_1.5px_var(--green)]">
          <Textarea
            rows={2}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Design a 4-to-2 priority encoder with an enable input and a valid output…"
            className="min-h-14 resize-none border-0 bg-transparent px-2.5 py-2 text-[14px] text-ink shadow-none placeholder:text-ink-3 focus-visible:ring-0"
          />
          <div className="flex items-center justify-between px-2.5 pb-1">
            <span className="text-[12px] text-ink-3">
              {running ? 'working' : 'Enter to send · Shift + Enter for a new line'}
            </span>
            <Button
              size="icon"
              className="size-8 rounded-full bg-lime text-ink hover:bg-[#d6ff66] disabled:bg-inset disabled:text-ink-3"
              onClick={onRun}
              disabled={running || !question.trim()}
              aria-label="Ask"
            >
              {running ? <Loader2 className="animate-spin" /> : <ArrowUp />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
