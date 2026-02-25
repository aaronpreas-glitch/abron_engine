interface Props { value: number | null; digits?: number }

export function PctChange({ value, digits = 1 }: Props) {
  if (value == null) return <span style={{ color: 'var(--muted)' }}>—</span>
  const color = value > 0 ? 'var(--green)' : value < 0 ? 'var(--red)' : 'var(--muted)'
  const sign = value > 0 ? '+' : ''
  return <span style={{ color }}>{sign}{value.toFixed(digits)}%</span>
}
