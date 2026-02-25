export function LoadingSpinner({ size = 20 }: { size?: number }) {
  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', padding:32 }}>
      <div style={{
        width: size, height: size,
        border: '2px solid var(--border)',
        borderTopColor: 'var(--green)',
        borderRadius: '50%',
        animation: 'spin 0.8s linear infinite',
      }} />
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
