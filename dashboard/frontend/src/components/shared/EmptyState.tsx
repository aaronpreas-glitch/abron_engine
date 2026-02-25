export function EmptyState({ message }: { message: string }) {
  return (
    <div style={{ textAlign:'center', padding:48, color:'var(--muted)' }}>
      <div style={{ fontSize:28, marginBottom:8 }}>—</div>
      <div>{message}</div>
    </div>
  )
}
