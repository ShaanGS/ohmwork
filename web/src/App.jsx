import { useEffect, useRef, useState } from 'react'
import {
  ArrowUp, Check, CircleAlert, Download, Loader2, TriangleAlert, Waves,
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
  const [question, setQuestion] = useState('')
  const [asked, setAsked] = useState(null)
  const [running, setRunning] = useState(false)
  const [reading, setReading] = useState(null)
  const [attempts, setAttempts] = useState([])
  const [verified, setVerified] = useState(null)
  const [refused, setRefused] = useState(null)
  const [error, setError] = useState(null)
  const bottom = useRef(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [asked, reading, attempts, verified, refused, error])

  async function run() {
    const text = question.trim()
    if (!text || running) return
    setRunning(true)
    setAsked(text)
    setQuestion('')
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
        setAttempts((previous) => [
          ...previous.filter((a) => a.index !== data.index), data,
        ].sort((a, b) => a.index - b.index))
      } else if (name === 'verified') setVerified(data)
      else if (name === 'refused') setRefused(data)
      else if (name === 'error') setError(data)
    }
    setRunning(false)
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <Wordmark className="text-sm" />
        <Badge variant="outline" className="text-muted-foreground">
          digital · logisim evolution
        </Badge>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
          {!asked && <Intro onPick={setQuestion} />}

          {asked && <Asked text={asked} />}
          {refused && <Refused refused={refused} />}
          {reading && <Reading reading={reading} />}

          {attempts.map((attempt) => (
            <Attempt key={attempt.index} attempt={attempt} settled={!!verified} />
          ))}

          {verified && <Verified verified={verified} />}
          {error && <Failed error={error} />}
          {running && !verified && !error && !refused && <Working />}
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

function Intro({ onPick }) {
  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-500">
      <h1 className="text-2xl font-medium tracking-tight">
        Ask a digital logic question.
      </h1>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        You get a Logisim circuit file and its truth table. The circuit is
        designed, emitted as a real <code className="text-foreground">.circ</code>,
        and that file is handed to Logisim Evolution to evaluate. If Logisim
        disagrees with the specification, the design is thrown away and redone —
        so nothing reaches you unchecked.
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
        Analog questions (LTspice) are answered on the command line, not here:
        LTspice is a Windows application and this server cannot run it, so an
        analog answer from here could only ever be unverified. Sequential
        circuits and parts whose pin geometry has never been measured are
        refused for their own reasons, and the refusal says which.
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

function Working() {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground
                    animate-in fade-in">
      <Loader2 className="size-3.5 animate-spin" />
      working — a solve takes a minute
    </div>
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
{reading.spec}
        </pre>
        <Separator className="bg-caution/20" />
        <p className="text-xs leading-relaxed text-caution/90">
          Read this. Everything below is checked against it, not against your
          question — a misreading here passes every check that follows.
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
