/*
 * REVEAL -- text that resolves out of blur one line at a time. Used for the
 * reading, which is the one block a person is asked to actually read: the
 * stagger gives the eye an order without hiding anything for long.
 */
export default function Reveal({ text, className = '', perLine = 70 }) {
  const lines = String(text ?? '').split('\n')
  return (
    <pre className={`font-mono text-[13px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] ${className}`}>
      {lines.map((line, i) => (
        <span
          key={i}
          className="block"
          style={{ animation: `resolve 420ms cubic-bezier(0.23,1,0.32,1) ${i * perLine}ms both` }}
        >
          {line === '' ? ' ' : line}
        </span>
      ))}
    </pre>
  )
}
