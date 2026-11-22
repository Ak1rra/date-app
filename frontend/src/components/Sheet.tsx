import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

const OVERLAY = 'fixed inset-0 z-10 flex items-end bg-ink/40 p-3 sm:items-center sm:justify-center';
const PANEL = 'max-h-full w-full max-w-lg overflow-y-auto rounded-3xl bg-surface p-5 shadow-xl';

/** Open sheets, oldest first. Only the last one is reachable; everything else on the body is inert. */
const open: HTMLElement[] = [];
const madeInert = new Set<Element>();

function syncInert() {
  const top = open[open.length - 1];
  for (const child of Array.from(document.body.children)) {
    if (top && child !== top) {
      child.setAttribute('inert', '');
      madeInert.add(child);
    } else if (madeInert.has(child)) {
      child.removeAttribute('inert');
      madeInert.delete(child);
    }
  }
  if (!top) madeInert.clear();
}

function focusables(root: HTMLElement) {
  // Controls inside a disabled fieldset match the selector but can't take focus.
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.closest('fieldset[disabled]'),
  );
}

/**
 * The one modal sheet. It traps Tab inside itself, closes on Escape, hands focus back to whatever
 * opened it, and makes the rest of the page inert while it is open (nested sheets stack).
 */
export function Sheet({
  labelledBy,
  onClose,
  children,
  className = PANEL,
  overlayClassName = OVERLAY,
  initialFocus,
}: {
  labelledBy: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  overlayClassName?: string;
  initialFocus?: RefObject<HTMLElement | null>;
}) {
  const overlay = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  // Read during the first render, before any child can autofocus and move it.
  const [trigger] = useState(() => document.activeElement);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    const overlayEl = overlay.current;
    const panelEl = panel.current;
    if (!overlayEl || !panelEl) return;
    open.push(overlayEl);
    syncInert();

    if (!panelEl.contains(document.activeElement)) {
      (initialFocus?.current ?? focusables(panelEl)[0] ?? panelEl).focus();
    }

    const onKey = (event: KeyboardEvent) => {
      if (open[open.length - 1] !== overlayEl) return;
      if (event.key === 'Escape') {
        close.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusables(panelEl);
      if (items.length === 0) {
        event.preventDefault();
        panelEl.focus();
        return;
      }
      const first = items[0]!;
      const last = items[items.length - 1]!;
      const active = document.activeElement;
      if (!panelEl.contains(active) || active === panelEl) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);

    return () => {
      document.removeEventListener('keydown', onKey);
      open.splice(open.indexOf(overlayEl), 1);
      syncInert();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
    // Mount-only: the trap must not restart (and steal focus) when a parent re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return createPortal(
    <div ref={overlay} className={overlayClassName} role="presentation">
      <section
        ref={panel}
        tabIndex={-1}
        className={className}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
      >
        {children}
      </section>
    </div>,
    document.body,
  );
}
