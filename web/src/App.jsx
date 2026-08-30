import { useEffect, useRef, useState } from 'react'
import {
  ArrowUp, Check, CircleAlert, Download, KeyRound, Loader2, TriangleAlert, Waves,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'

/*
 * Chat-shaped, on stock shadcn/ui components.
 *
 * The layout argues one claim: the answer below was checked by an outside
 * tool, and here is what it was checked AGAINST. So the reading comes first
 * and is the loudest thing on the page, every rejected design is shown
 * rather than hidden, and the verified badge names the evaluator. Each of
 * those is a place this project can be wrong in a way that would otherwise
 * look exactly like being right.
 */

const EXAMPLES = [
  'Design a 2-to-4 decoder with an active-high enable.',
  'Design a 4-to-2 priority encoder with an enable input and a valid output, using basic gates only.',
  'Design a series voltage regulator in LTspice that delivers 9 V to a 1 kOhm load from a 15 V unregulated supply. Report the output voltage and the zener current.',
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
    document.documentElement.classList.add('dark')
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

function Wordmark({ className = '' }) {
  return (
    <span className={`inline-flex items-center gap-2 font-medium ${className}`}>
      <Waves className="size-4 text-verified" strokeWidth={2.5} />
      ohmwork
    </span>
  )
}

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
    else if (response.status === 429) setError('too many attempts — wait it out')
    else setError('wrong password')
  }

  return (
    <div className="flex h-full items-center justify-center p-6">
      <Card className="w-full max-w-sm animate-in fade-in slide-in-from-bottom-2 duration-500">
        <CardContent className="space-y-4">
          <Wordmark className="text-lg" />
          <p className="text-sm text-muted-foreground">
            A lab question in. A circuit file and an answer a simulator
            checked, out.
          </p>
          <form onSubmit={submit} className="space-y-3">
            <Input
              type="password"
              autoFocus
              placeholder="passphrase"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button type="submit" className="w-full" disabled={busy || !password}>
              {busy && <Loader2 className="animate-spin" />}
              {busy ? 'checking' : 'enter'}
            </Button>
          </form>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </CardContent>
      </Card>
    </div>
  )
}

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
  const bottom = useRef(null)

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
      setError({ message: 'could not reach the server' })
      setRunning(false)
      return
    }

    if (response.status === 401) { onExpired(); return }
    if (!response.ok) {
      setError({ message: (await response.json()).detail || 'refused' })
      setRunning(false)
      return
    }

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
    setRunning(false)
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <Wordmark className="text-sm" />
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-muted-foreground">
            digital · logisim&ensp;|&ensp;analog · ltspice
          </Badge>
          {desktop && (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              title="Model key settings"
              aria-label="Model key settings"
              onClick={() => setShowSettings((open) => !open)}
            >
              <KeyRound />
            </Button>
          )}
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
          {showSettings && desktop && <DesktopSettings desktop={desktop} onDone={() => setShowSettings(false)} />}
          {!asked && <Intro onPick={setQuestion} />}

          {asked && <Asked text={asked} />}
          {routing && <Routing routing={routing} />}
          {refused && <Refused refused={refused} />}
          {unavailable && <Unavailable info={unavailable} />}
          {reading && <Reading reading={reading} />}

          {attempts.map((attempt) => (
            <Attempt key={attempt.index} attempt={attempt} settled={!!verified || !!measured} />
          ))}

          {verified && <Verified verified={verified} />}
          {measured && <Measured measured={measured} />}
          {error && <Failed error={error} />}
          {running && !verified && !measured && !error && !refused && !unavailable
            && <Working analog={routing?.domain === 'analog'} />}
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
      setError('secure credential storage is unavailable on this computer')
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
      setError(reason?.message || 'the key could not be stored')
    }
  }

  return (
    <Card className="border-caution/40 bg-caution/5 animate-in fade-in slide-in-from-bottom-2">
      <CardContent className="space-y-3">
        <div>
          <p className="text-sm font-medium">local model key</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            Stored encrypted by your operating system. It is sent to the local
            solver process, never to this page, and never uploaded by Ohmwork.
          </p>
        </div>
        <form onSubmit={save} className="space-y-2">
          {providers.map(([name, label]) => (
            <label key={name} className="grid gap-1 sm:grid-cols-[9rem_1fr] sm:items-center">
              <span className="text-sm">{label}</span>
              <Input
                type="password"
                autoComplete="off"
                placeholder={state?.configured.includes(name) ? 'saved - enter a replacement only' : 'API key (optional)'}
                value={keys[name]}
                onChange={(event) => setKeys((current) => ({ ...current, [name]: event.target.value }))}
                disabled={saving || state?.encryptionAvailable === false}
              />
            </label>
          ))}
          <Button type="submit" disabled={saving || !Object.values(keys).some((key) => key.trim().length >= 8) || state?.encryptionAvailable === false}>
            {saving && <Loader2 className="animate-spin" />}
            {saving ? 'restarting' : 'save keys'}
          </Button>
        </form>
        {state && (
          <p className="text-xs text-muted-foreground">
            configured locally: {state.configured.length ? state.configured.join(', ') : 'none'}
          </p>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
        <Button type="button" variant="ghost" size="xs" onClick={onDone}>close</Button>
      </CardContent>
    </Card>
  )
}

function Intro({ onPick }) {
  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-500">
      <h1 className="text-2xl font-medium tracking-tight">
        Ask an electronics lab question.
      </h1>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        A digital question gets a Logisim <code className="text-foreground">.circ</code>
        {' '}and its truth table, every row computed by Logisim Evolution from
        that exact file. An analog question gets an LTspice
        {' '}<code className="text-foreground">.asc</code> and the measured
        numbers, with what was checked and what was merely reported kept
        apart. Either way, a design the simulator disagrees with is thrown
        away and redone — nothing reaches you unchecked.
      </p>
      <div className="mt-6 space-y-2">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            onClick={() => onPick(example)}
            className="block w-full rounded-lg border bg-card p-3 text-left text-sm
                       text-muted-foreground transition-colors hover:bg-accent
                       hover:text-accent-foreground"
          >
            {example}
          </button>
        ))}
      </div>
      <p className="mt-6 text-xs leading-relaxed text-muted-foreground">
        Analog needs LTspice on this machine — without it, an analog question
        is refused with the download named, never answered unverified.
        Sequential circuits and parts whose pin geometry has never been
        measured are refused for their own reasons, and the refusal says
        which.
      </p>
    </div>
  )
}

function Asked({ text }) {
  return (
    <div className="flex justify-end animate-in fade-in slide-in-from-bottom-2">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5
                      text-sm text-primary-foreground">
        {text}
      </div>
    </div>
  )
}

function Working({ analog }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground
                    animate-in fade-in">
      <Loader2 className="size-3.5 animate-spin" />
      {analog
        ? 'working — an analog solve runs LTspice for every attempt, so this can take a few minutes'
        : 'working — a solve takes a minute'}
    </div>
  )
}

function Routing({ routing }) {
  // Which half answers is a guess made from the question's words, so it is
  // disclosed, exactly as the CLI prints it, before anything runs.
  return (
    <p className="text-xs text-muted-foreground animate-in fade-in">
      read as an <span className="font-medium uppercase">{routing.domain}</span>
      {' '}question: {routing.reason}
    </p>
  )
}

function Reading({ reading }) {
  return (
    <Card className="border-caution/40 bg-caution/5 animate-in fade-in
                     slide-in-from-bottom-2">
      <CardContent className="space-y-3">
        <p className="text-xs font-medium uppercase tracking-wider text-caution">
          the reading
        </p>
        <pre className="overflow-x-auto font-mono text-[13px] leading-relaxed">
{reading.spec || reading.intent}
        </pre>
        <Separator className="bg-caution/20" />
        <p className="text-xs leading-relaxed text-caution/90">
          {reading.basis === 'part'
            ? `Read this. Your question names a part, so the answer is checked
               against that chip's own measured behaviour and the wiring map
               printed with it — not against the words above, and not against
               your question.`
            : `Read this. Everything below is checked against it, not against
               your question — a misreading here passes every check that
               follows.`}
        </p>
      </CardContent>
    </Card>
  )
}

function Attempt({ attempt, settled }) {
  const rejected = attempt.status === 'rejected'
  const accepted = !rejected && settled
  return (
    <div className="flex gap-2.5 text-sm animate-in fade-in slide-in-from-bottom-1">
      <span className="mt-0.5 shrink-0">
        {rejected
          ? <TriangleAlert className="size-4 text-caution" />
          : accepted
            ? <Check className="size-4 text-verified" />
            : <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      </span>
      <div className="min-w-0">
        <span className="text-muted-foreground">
          design attempt {attempt.index}
        </span>
        <p className="mt-0.5 font-mono text-xs leading-relaxed text-muted-foreground">
          {rejected
            ? `rejected — ${attempt.failure.split('\n')[0]}`
            : accepted
              ? 'emitted, and handed to the evaluator'
              : 'designing…'}
        </p>
      </div>
    </div>
  )
}

function Verified({ verified }) {
  const inputs = verified.input_count
  const external = verified.verification === 'external'
  return (
    <Card className="border-verified/40 bg-verified/5 animate-in fade-in
                     slide-in-from-bottom-2 duration-500">
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-sm text-verified">
            <Check className="size-4" strokeWidth={3} />
            verified in {verified.attempts} attempt
            {verified.attempts === 1 ? '' : 's'}
          </span>
          <Button asChild size="sm">
            <a href={`/api/circuit/${verified.download}`}>
              <Download /> download .circ
            </a>
          </Button>
        </div>

        <Separator className="bg-verified/20" />

        <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
          <Fact label="checked by">{verified.evaluator}</Fact>
          <Fact label="verification">
            {external
              ? 'external — an outside tool computed this'
              : 'INTERNAL — our own evaluator, unchecked by anything else'}
          </Fact>
          <Fact label="agreement">{verified.summary.split('\n')[0]}</Fact>
          <Fact label="designed by">{verified.designed_by}</Fact>
        </dl>

        {/* WHAT it was checked against. "Verified" alone is the same badge
            over two different claims: algebra read from the question, or a
            real part's own measured behaviour. A reader who cannot tell them
            apart has been shown the stronger one. */}
        {verified.basis && (
          <div className="space-y-2 rounded-lg border border-verified/20 p-3">
            <p className="text-xs font-medium uppercase tracking-wider
                          text-muted-foreground">
              checked against
            </p>
            <p className="text-xs leading-relaxed">{verified.basis.headline}</p>
            {verified.basis.kind === 'part' && (
              <pre className="overflow-x-auto font-mono text-[13px]
                              leading-relaxed">
{verified.basis.reading}
              </pre>
            )}
            <p className="text-xs leading-relaxed text-caution/90">
              Not established by any check here: {verified.basis.limit}
            </p>
          </div>
        )}

        <div className="overflow-x-auto rounded-lg border">
          <Table className="font-mono text-[13px]">
            <TableHeader>
              <TableRow>
                {verified.columns.map((column, index) => (
                  <TableHead
                    key={column}
                    className={`text-right ${index === inputs ? 'border-l' : ''}`}
                  >
                    {column}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {verified.rows.map((row, r) => (
                <TableRow key={r}>
                  {row.map((bit, index) => (
                    <TableCell
                      key={index}
                      className={`text-right tabular-nums ${
                        index === inputs ? 'border-l' : ''
                      } ${
                        index >= inputs
                          ? bit ? 'text-verified' : 'text-muted-foreground'
                          : ''
                      }`}
                    >
                      {bit}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        <p className="text-xs leading-relaxed text-muted-foreground">
          The layout inside the file is generated mechanically — inputs in a
          left column, gates in columns by logic depth. Correct, not pretty.
        </p>
      </CardContent>
    </Card>
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
  return (
    <Card className={`animate-in fade-in slide-in-from-bottom-2 duration-500 ${
      nothingChecked ? 'border-caution/40 bg-caution/5'
                     : 'border-verified/40 bg-verified/5'}`}>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className={`flex items-center gap-2 text-sm ${
            nothingChecked ? 'text-caution' : 'text-verified'}`}>
            {nothingChecked
              ? <CircleAlert className="size-4" />
              : <Check className="size-4" strokeWidth={3} />}
            {measured.headline}
          </span>
          <Button asChild size="sm">
            <a href={`/api/circuit/${measured.download}`}>
              <Download /> download .asc
            </a>
          </Button>
        </div>

        <Separator className={nothingChecked ? 'bg-caution/20' : 'bg-verified/20'} />

        <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
          <Fact label="measured by">{measured.evaluator}</Fact>
          <Fact label="verification">
            {external
              ? 'external — an outside simulator computed every number'
              : 'INTERNAL — our own evaluator, unchecked by anything else'}
          </Fact>
          <Fact label="checked / reported">
            {measured.checked} target{measured.checked === 1 ? '' : 's'} with a
            stated figure · {measured.observations} reported only
          </Fact>
          <Fact label="regimes">
            {measured.regimes_held} held
            {measured.regimes_failed.length
              ? ` · ${measured.regimes_failed.length} FAILED`
              : ''}
          </Fact>
          <Fact label="designed by">{measured.designed_by}</Fact>
        </dl>

        <div className="overflow-x-auto rounded-lg border">
          <Table className="font-mono text-[13px]">
            <TableHeader>
              <TableRow>
                <TableHead>quantity</TableHead>
                <TableHead>asked for</TableHead>
                <TableHead className="text-right">measured</TableHead>
                <TableHead>status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {measured.outcomes.map((outcome) => (
                <TableRow key={outcome.name}>
                  <TableCell>{outcome.quantity || outcome.name}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {outcome.wanted}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {outcome.measured === null || outcome.measured === undefined
                      ? '—'
                      : `${outcome.measured} ${outcome.unit}`}
                  </TableCell>
                  <TableCell className={
                    !outcome.checked ? 'text-muted-foreground'
                      : outcome.ok ? 'text-verified' : 'text-destructive'}>
                    {!outcome.checked ? 'reported only'
                      : outcome.ok ? 'within tolerance'
                        : (outcome.reason || 'missed')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        {measured.regimes_failed.length > 0 && (
          <p className="text-xs leading-relaxed text-destructive">
            Regime assertions that FAILED — the numbers above touching those
            runs are not reliable: {measured.regimes_failed.join('; ')}
          </p>
        )}
        {measured.warnings.length > 0 && (
          <p className="text-xs leading-relaxed text-caution/90">
            {measured.warnings.join(' · ')}
          </p>
        )}

        {measured.basis && (
          <div className="space-y-2 rounded-lg border border-verified/20 p-3">
            <p className="text-xs font-medium uppercase tracking-wider
                          text-muted-foreground">
              checked against
            </p>
            <p className="text-xs leading-relaxed">{measured.basis.headline}</p>
            <p className="text-xs leading-relaxed text-caution/90">
              Not established by any check here: {measured.basis.limit}
            </p>
          </div>
        )}

        <p className="text-xs leading-relaxed text-muted-foreground">
          {measured.file_note}
        </p>
      </CardContent>
    </Card>
  )
}

function Fact({ label, children }) {
  return (
    <div className="flex gap-2">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

function Refused({ refused }) {
  // The message is written as paragraphs: what was refused, the evidence, and
  // finally what to do instead. The last one is the advice.
  const paragraphs = refused.message.split('\n\n')
  const advice = paragraphs[paragraphs.length - 1]
  const rest = paragraphs.slice(0, -1)
  return (
    <Card className="border-caution/40 bg-caution/5 animate-in fade-in
                     slide-in-from-bottom-2">
      <CardContent className="space-y-3">
        <p className="flex items-center gap-2 text-sm text-caution">
          <CircleAlert className="size-4" />
          not a question this can answer
        </p>
        {rest.map((paragraph) => (
          <p key={paragraph} className="text-sm leading-relaxed">{paragraph}</p>
        ))}
        <Separator className="bg-caution/20" />
        <p className="text-xs leading-relaxed text-muted-foreground">{advice}</p>
      </CardContent>
    </Card>
  )
}

function Unavailable({ info }) {
  // A THIRD outcome, and it needs to look like neither of the others. "no
  // verified circuit" in red says the circuit failed; this says nobody could
  // be asked, which is not a fact about the question at all.
  return (
    <Card className="animate-in fade-in slide-in-from-bottom-2">
      <CardContent className="space-y-3">
        <p className="flex items-center gap-2 text-sm">
          <CircleAlert className="size-4 text-muted-foreground" />
          every model provider is busy
        </p>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Nothing was designed and nothing was wrong with your question —
          there was simply nobody to ask. These are free accounts, and free
          accounts run out.
        </p>
        <div className="rounded-lg border bg-background/50 p-3">
          <dl className="space-y-1 text-xs">
            {(info.members || []).map(([name, reason]) => (
              <div key={name} className="flex gap-2">
                <dt className="w-24 shrink-0 font-mono text-muted-foreground">
                  {name}
                </dt>
                <dd>{reason}</dd>
              </div>
            ))}
          </dl>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Try again in a few minutes, or add another provider key. Nothing
          about the question needs changing.
        </p>
      </CardContent>
    </Card>
  )
}


function Failed({ error }) {
  return (
    <Card className="border-destructive/40 bg-destructive/5 animate-in fade-in
                     slide-in-from-bottom-2">
      <CardContent className="space-y-3">
        <p className="flex items-center gap-2 text-sm text-destructive">
          <CircleAlert className="size-4" />
          no verified circuit
        </p>
        <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs
                        leading-relaxed text-muted-foreground">
{error.message}
        </pre>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Nothing is returned when the evaluator disagrees. A circuit that
          failed its own specification is worse than no circuit, because it
          looks like an answer.
        </p>
      </CardContent>
    </Card>
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
    <div className="border-t bg-background/80 backdrop-blur">
      <div className="mx-auto w-full max-w-3xl px-4 py-4">
        <div className="relative rounded-2xl border bg-card p-2
                        focus-within:ring-1 focus-within:ring-ring">
          <Textarea
            rows={2}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Design a 4-to-2 priority encoder with an enable input and a valid output…"
            className="min-h-16 resize-none border-0 bg-transparent px-2 py-1.5
                       shadow-none focus-visible:ring-0 dark:bg-transparent"
          />
          <div className="flex items-center justify-between px-2 pb-0.5">
            <span className="text-xs text-muted-foreground">
              {running ? 'working…' : 'enter to send · shift + enter for a new line'}
            </span>
            <Button
              size="icon"
              className="size-8 rounded-full"
              onClick={onRun}
              disabled={running || !question.trim()}
            >
              {running ? <Loader2 className="animate-spin" /> : <ArrowUp />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
