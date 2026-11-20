import type { ComponentProps } from 'react';
import { Link } from 'react-router';

type Variant = 'primary' | 'secondary' | 'husband' | 'wife';
type Size = 'md' | 'xl';

const base =
  'inline-flex items-center justify-center gap-2 rounded-full font-semibold no-underline ' +
  'motion-safe:transition motion-safe:duration-150 motion-safe:active:scale-[0.98] ' +
  'disabled:opacity-60';
const variants: Record<Variant, string> = {
  primary: 'bg-accent text-on-accent shadow-md hover:brightness-110',
  secondary: 'bg-accent-soft text-accent border border-line hover:brightness-95',
  husband: 'bg-husband-soft text-husband border-2 border-husband hover:brightness-95',
  wife: 'bg-wife-soft text-wife border-2 border-wife hover:brightness-95',
};
const sizes: Record<Size, string> = {
  md: 'min-h-11 px-5 py-2 text-base',
  xl: 'min-h-16 w-full px-8 py-4 text-xl md:w-auto',
};

function classes(variant: Variant, size: Size, extra?: string) {
  return [base, variants[variant], sizes[size], extra].filter(Boolean).join(' ');
}

interface Common {
  variant?: Variant;
  size?: Size;
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  type = 'button',
  ...props
}: Common & ComponentProps<'button'>) {
  return <button type={type} className={classes(variant, size, className)} {...props} />;
}

export function ButtonLink({
  variant = 'primary',
  size = 'md',
  className,
  ...props
}: Common & ComponentProps<typeof Link>) {
  return <Link className={classes(variant, size, className)} {...props} />;
}
