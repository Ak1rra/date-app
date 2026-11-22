import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Sheet } from './Sheet';

function Demo({ onClose = () => {} }: { onClose?: () => void }) {
  return (
    <Sheet labelledBy="demo-title" onClose={onClose}>
      <h2 id="demo-title">Demo</h2>
      <button type="button">First</button>
      <input aria-label="Middle" />
      <button type="button">Last</button>
    </Sheet>
  );
}

describe('Sheet', () => {
  it('is a labelled modal dialog and starts focus inside it', () => {
    render(<Demo />);
    expect(screen.getByRole('dialog', { name: 'Demo' })).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus();
  });

  it('focuses initialFocus when given', () => {
    function WithInitial() {
      const wanted = useRef<HTMLInputElement>(null);
      return (
        <Sheet labelledBy="t" onClose={() => {}} initialFocus={wanted}>
          <h2 id="t">T</h2>
          <button type="button">First</button>
          <input aria-label="Wanted" ref={wanted} />
        </Sheet>
      );
    }
    render(<WithInitial />);
    expect(screen.getByLabelText('Wanted')).toHaveFocus();
  });

  it('focuses the dialog itself when nothing inside is focusable', () => {
    render(
      <Sheet labelledBy="t" onClose={() => {}}>
        <h2 id="t">Nothing to press</h2>
      </Sheet>,
    );
    expect(screen.getByRole('dialog')).toHaveFocus();
  });

  it('wraps Tab from the last element to the first and Shift+Tab the other way', async () => {
    const user = userEvent.setup();
    render(<Demo />);
    const first = screen.getByRole('button', { name: 'First' });
    const last = screen.getByRole('button', { name: 'Last' });
    last.focus();
    await user.tab();
    expect(first).toHaveFocus();
    await user.tab({ shift: true });
    expect(last).toHaveFocus();
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Demo onClose={onClose} />);
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('gives focus back to the trigger when it closes', async () => {
    const user = userEvent.setup();
    function Host() {
      const [shown, setShown] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setShown(true)}>
            Open
          </button>
          {shown ? <Demo onClose={() => setShown(false)} /> : null}
        </>
      );
    }
    render(<Host />);
    const trigger = screen.getByRole('button', { name: 'Open' });
    await user.click(trigger);
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('makes the page inert while open, and keeps it so until the last nested sheet closes', async () => {
    const user = userEvent.setup();
    function Host() {
      const [outer, setOuter] = useState(false);
      const [inner, setInner] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOuter(true)}>
            Open outer
          </button>
          {outer ? (
            <Sheet labelledBy="outer-title" onClose={() => setOuter(false)}>
              <h2 id="outer-title">Outer</h2>
              <button type="button" onClick={() => setInner(true)}>
                Open inner
              </button>
              {inner ? (
                <Sheet labelledBy="inner-title" onClose={() => setInner(false)}>
                  <h2 id="inner-title">Inner</h2>
                  <button type="button" onClick={() => setInner(false)}>
                    Close inner
                  </button>
                </Sheet>
              ) : null}
            </Sheet>
          ) : null}
        </>
      );
    }
    const { container } = render(<Host />);
    expect(container).not.toHaveAttribute('inert');

    await user.click(screen.getByRole('button', { name: 'Open outer' }));
    expect(container).toHaveAttribute('inert');
    const outerDialog = screen.getByRole('dialog', { name: 'Outer' });
    expect(outerDialog.parentElement).not.toHaveAttribute('inert');

    await user.click(screen.getByRole('button', { name: 'Open inner' }));
    expect(container).toHaveAttribute('inert');
    expect(outerDialog.parentElement).toHaveAttribute('inert');
    expect(screen.getByRole('dialog', { name: 'Inner' }).parentElement).not.toHaveAttribute(
      'inert',
    );

    await user.click(screen.getByRole('button', { name: 'Close inner' }));
    expect(container).toHaveAttribute('inert');
    expect(outerDialog.parentElement).not.toHaveAttribute('inert');

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(container).not.toHaveAttribute('inert');
  });
});
