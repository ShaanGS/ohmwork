import { useEffect, useRef, useState } from 'react'

/*
 * The screen is arranged around one claim: the answer below was checked by an
 * outside tool, and here is what it was checked AGAINST.
 *
 * So the reading comes first and is the loudest thing on the page, the
 * rejected designs are shown rather than hidden, and the verified badge names
 * the evaluator. None of that is decoration -- each one is a place this
 * project can be wrong in a way that would otherwise look exactly like being
 * right.
 */

const EXAMPLES = [
  'Design a 2-to-4 decoder with an active-high enable.',
  'Design a 4-to-2 priority encoder with an enable input and a valid output, using basic gates only.',
  'Design a circuit that outputs 1 when exactly two of its three inputs are high.',
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

  if (authorised === null) return <div className="paper h-full" />
  return (
    <div className="paper flex h-full flex-col">
      {authorised ? <Solver onExpired={() => setAuthorised(false)} />
                  : <Login onIn={() => setAuthorised(true)} />}
    </div>
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
    else if (response.status === 429) setError('too many attempts. wait it out.')
    else setError('wrong password')
  }

  return (
    <main className="relative z-10 flex flex-1 items-center justify-center px-6">
      <form onSubmit={submit} className="rise w-full max-w-sm">
        <Wordmark />
        <p className="mt-3 text-sm text-ink-400">
          A lab question in. A circuit file and an answer a simulator checked, out.
        </p>
        <input
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="passphrase"
          className="mt-6 w-full rounded-lg border border-ink-800 bg-ink-900/70 px-4 py-3
                     font-mono text-sm text-ink-200 placeholder:text-ink-700
                     transition focus:border-signal-dim"
        />
        <button
          disabled={busy || !password}
          className="mt-3 w-full rounded-lg bg-signal px-4 py-3 text-sm font-medium
                     text-ink-950 transition hover:brightness-110
                     disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? 'checking…' : 'enter'}
        </button>
        {error && <p className="mt-3 text-center text-sm text-fault">{error}</p>}
      </form>
    </main>
  )
}

function Wordmark({ small = false }) {
  return (
    <div className="flex items-center gap-2.5">
      <svg width={small ? 20 : 26} height={small ? 20 : 26} viewBox="0 0 24 24" fill="none"
           className="text-signal" aria-hidden>
        <path d="M2 12h3.5l2-5 3 10 3-8 2 3H22" stroke="currentColor" strokeWidth="1.6"
              strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className={`font-mono tracking-tight text-ink-200 ${small ? 'text-base' : 'text-2xl'}`}>
        ohmwork
      </span>
    </div>
  )
}

function Solver({ onExpired }) {
  const [question, setQuestion] = useState('')
  const [running, setRunning] = useState(false)
  const [reading, setReading] = useState(null)
  const [attempts, setAttempts] = useState([])
  const [verified, setVerified] = useState(null)
  const [refused, setRefused] = useState(null)
  const [error, setError] = useState(null)
  const bottom = useRef(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [reading, attempts, verified, refused, error])

  async function run() {
    const text = question.trim()
    if (!text || running) return
    setRunning(true)
    setReading(null)
    setAttempts([])
    setVerified(null)
    setRefused(null)
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
      if (name === 'reading') setReading(data)
      else if (name === 'attempt') {
        setAttempts((previous) => {
          const rest = previous.filter((a) => a.index !== data.index)
          return [...rest, data].sort((a, b) => a.index - b.index)
        })
      } else if (name === 'verified') setVerified(data)
      else if (name === 'refused') setRefused(data)
      else if (name === 'error') setError(data)
    }
    setRunning(false)
  }

  const idle = !running && !reading && !verified && !error && !refused

  return (
    <>
      <header className="relative z-10 flex items-center justify-between border-b
                         border-ink-850/70 px-5 py-3.5">
        <Wordmark small />
        <span className="font-mono text-[11px] text-ink-700">
          digital · logisim evolution
        </span>
      </header>

      <main className="relative z-10 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-5 py-8">
          {idle && <Intro onPick={setQuestion} />}

          {refused && <Refused refused={refused} />}

          {(running || reading) && !refused && (
            <Step done={!!reading} label="reading the question">
              {reading && <Reading reading={reading} />}
            </Step>
          )}

          {attempts.map((attempt) => {
            const rejected = attempt.status === 'rejected'
            // An attempt that was never rejected is the one that WORKED.
            // Leaving it labelled "design attempt 1" with nothing underneath
            // reads as a step that never finished.
            const accepted = !rejected && !!verified
            return (
              <Step
                key={attempt.index}
                done={rejected || accepted}
                failed={rejected}
                label={`design attempt ${attempt.index}`}
              >
                <p className="font-mono text-xs leading-relaxed text-ink-400">
                  {rejected
                    ? `rejected — ${attempt.failure.split('\n')[0]}`
                    : accepted
                      ? 'emitted, and handed to the evaluator'
                      : 'designing…'}
                </p>
              </Step>
            )
          })}

          {verified && <Verified verified={verified} />}
          {error && <Failed error={error} />}
          <div ref={bottom} />
        </div>
      </main>

      <Composer
        question={question}
        setQuestion={setQuestion}
        onRun={run}
        running={running}
      />
    </>
  )
}

function Intro({ onPick }) {
  return (
    <div className="rise">
      <h1 className="text-xl text-ink-200">Ask a digital logic question.</h1>
      <p className="mt-2 max-w-xl text-sm leading-relaxed text-ink-400">
        You get a Logisim circuit file and its truth table. The circuit is
        designed, emitted as a real <span className="font-mono">.circ</span>, and
        that file is handed to Logisim Evolution to evaluate. If Logisim
        disagrees with the specification, the design is thrown away and redone —
        so nothing reaches you unchecked.
      </p>
      <div className="mt-6 space-y-2">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            onClick={() => onPick(example)}
            className="block w-full rounded-lg border border-ink-850 bg-ink-900/40
                       px-4 py-3 text-left text-sm text-ink-400 transition
                       hover:border-ink-800 hover:bg-ink-900/80 hover:text-ink-200"
          >
            {example}
          </button>
        ))}
      </div>
      <p className="mt-6 text-xs text-ink-700">
        Analog questions (LTspice) are not served here: LTspice is a Windows
        application and this server cannot run it, so an analog answer from here
        could only ever be unverified. Those stay on the command line.
      </p>
    </div>
  )
}

function Step({ label, done, failed, children }) {
  return (
    <div className="rise mb-5 flex gap-3.5">
      <div className="mt-1.5 flex flex-col items-center">
        <span
          className={`h-2 w-2 rounded-full ${
            failed ? 'bg-warn' : done ? 'bg-signal' : 'bg-ink-700 working'
          }`}
        />
        <span className="mt-1 w-px flex-1 bg-ink-850" />
      </div>
      <div className="min-w-0 flex-1 pb-1">
        <p className="font-mono text-[11px] uppercase tracking-wider text-ink-700">
          {label}
        </p>
        <div className="mt-1.5">{children}</div>
      </div>
    </div>
  )
}

function Reading({ reading }) {
  return (
    <div className="rounded-lg border border-warn/25 bg-warn/[0.04] p-4">
      <pre className="overflow-x-auto font-mono text-[13px] leading-relaxed text-ink-200">
{reading.spec}
      </pre>
      {/* The notes are NOT listed again here: spec.render() already prints
          them, and the first rendered page showed every note twice on the
          one card a person is asked to read carefully. */}
      <p className="mt-3 border-t border-warn/15 pt-3 text-xs leading-relaxed text-warn/90">
        Read this. Everything below is checked against it, not against your
        question — a misreading here passes every check that follows.
      </p>
    </div>
  )
}

function Verified({ verified }) {
  const inputs = verified.input_count
  return (
    <div className="rise mb-6 rounded-xl border border-signal/25 bg-signal/[0.04] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" className="text-signal">
            <path d="M20 6L9 17l-5-5" stroke="currentColor" strokeWidth="2.4"
                  strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="text-sm text-signal">
            verified in {verified.attempts} attempt{verified.attempts === 1 ? '' : 's'}
          </span>
        </div>
        <a
          href={`/api/circuit/${verified.download}`}
          className="rounded-lg bg-signal px-3.5 py-2 text-xs font-medium text-ink-950
                     transition hover:brightness-110"
        >
          download .circ
        </a>
      </div>

      <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 border-t border-signal/15
                     pt-4 text-xs sm:grid-cols-2">
        <Fact label="checked by">{verified.evaluator}</Fact>
        <Fact label="verification">
          {verified.verification === 'external'
            ? 'external — an outside tool computed this'
            : 'INTERNAL — our own evaluator, unchecked by anything else'}
        </Fact>
        <Fact label="agreement">{verified.summary.split('\n')[0]}</Fact>
        <Fact label="designed by">{verified.designed_by}</Fact>
      </dl>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full border-collapse font-mono text-[13px]">
          <thead>
            <tr>
              {verified.columns.map((column, index) => (
                <th
                  key={column}
                  className={`px-3 py-1.5 text-right font-normal text-ink-700 ${
                    index === inputs ? 'border-l border-ink-800' : ''
                  }`}
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {verified.rows.map((row, r) => (
              <tr key={r} className="border-t border-ink-850/60">
                {row.map((bit, index) => (
                  <td
                    key={index}
                    className={`px-3 py-1 text-right tabular-nums ${
                      index === inputs ? 'border-l border-ink-800' : ''
                    } ${
                      index >= inputs
                        ? bit ? 'text-signal' : 'text-ink-700'
                        : 'text-ink-400'
                    }`}
                  >
                    {bit}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-4 text-xs leading-relaxed text-ink-700">
        The layout inside the file is generated mechanically — inputs in a left
        column, gates in columns by logic depth. Correct, not pretty.
      </p>
    </div>
  )
}

function Fact({ label, children }) {
  return (
    <div className="flex gap-2">
      <dt className="shrink-0 text-ink-700">{label}</dt>
      <dd className="min-w-0 break-words text-ink-400">{children}</dd>
    </div>
  )
}

function Refused({ refused }) {
  // The message is written as paragraphs: what was refused, the evidence,
  // and finally what to do instead. The last one is the advice.
  const paragraphs = refused.message.split('\n\n')
  const advice = paragraphs[paragraphs.length - 1]
  const rest = paragraphs.slice(0, -1)
  return (
    <div className="rise mb-6 rounded-xl border border-warn/30 bg-warn/[0.05] p-5">
      <p className="text-sm text-warn">not a question this can answer</p>
      {rest.map((paragraph) => (
        <p key={paragraph} className="mt-2 text-sm leading-relaxed text-ink-200">
          {paragraph}
        </p>
      ))}
      <p className="mt-3 border-t border-warn/15 pt-3 text-xs leading-relaxed text-ink-400">
        {advice}
      </p>
    </div>
  )
}


function Failed({ error }) {
  return (
    <div className="rise mb-6 rounded-xl border border-fault/30 bg-fault/[0.05] p-5">
      <p className="text-sm text-fault">no verified circuit</p>
      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-xs
                      leading-relaxed text-ink-400">
{error.message}
      </pre>
      <p className="mt-3 text-xs leading-relaxed text-ink-700">
        Nothing is returned when the evaluator disagrees. A circuit that failed
        its own specification is worse than no circuit, because it looks like an
        answer.
      </p>
    </div>
  )
}

function Composer({ question, setQuestion, onRun, running }) {
  function onKeyDown(event) {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) onRun()
  }

  return (
    <div className="relative z-10 border-t border-ink-850/70 bg-ink-950/80 backdrop-blur">
      <div className="mx-auto w-full max-w-3xl px-5 py-4">
        <div className="rounded-xl border border-ink-800 bg-ink-900/70 p-2
                        transition focus-within:border-signal-dim">
          <textarea
            rows={2}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Design a 4-to-2 priority encoder with an enable input and a valid output…"
            className="w-full resize-none bg-transparent px-3 py-2 text-sm
                       text-ink-200 placeholder:text-ink-700 focus:outline-none"
          />
          <div className="flex items-center justify-between px-3 pb-1">
            <span className="font-mono text-[11px] text-ink-700">
              {running ? 'working — this takes a minute' : 'ctrl + enter'}
            </span>
            <button
              onClick={onRun}
              disabled={running || !question.trim()}
              className="rounded-lg bg-signal px-4 py-2 text-xs font-medium text-ink-950
                         transition hover:brightness-110 disabled:cursor-not-allowed
                         disabled:opacity-30"
            >
              {running ? 'solving…' : 'solve'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
