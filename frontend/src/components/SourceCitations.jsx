export default function SourceCitations({ sources }) {
  if (!sources || sources.length === 0) return null

  return (
    <div className="citations">
      <span className="citations-label">Sources:</span>
      {sources.map((source, i) => (
        <span key={`${source.source_file}-${i}`} className="citation-chip" title={source.snippet}>
          {source.title}
        </span>
      ))}
    </div>
  )
}
