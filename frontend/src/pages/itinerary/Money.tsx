export function Money({ value, currency = 'USD' }: { value: number | null; currency?: string }) {
  if (value === null) return null;
  return (
    <span>{new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value)}</span>
  );
}
