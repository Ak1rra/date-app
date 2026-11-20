import { useRef, type ComponentType, type MouseEvent, type SVGProps } from 'react';
import { NavLink, Outlet } from 'react-router';
import { CalendarIcon, ChatIcon, HomeIcon, IdeasIcon, UsIcon } from './Icons';

interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  end?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Home', icon: HomeIcon, end: true },
  { to: '/ideas', label: 'Ideas', icon: IdeasIcon },
  { to: '/calendar', label: 'Calendar', icon: CalendarIcon },
  { to: '/chat', label: 'Chat', icon: ChatIcon },
  { to: '/us', label: 'Us', icon: UsIcon },
];

const focusRing = 'rounded-2xl no-underline';

function TopNav() {
  return (
    <header className="hidden border-b border-line bg-surface md:block">
      <div className="mx-auto flex max-w-5xl items-center gap-6 px-6 py-3">
        <span className="text-lg font-bold text-accent">Date Planner</span>
        <nav aria-label="Main menu" className="ml-auto">
          <ul className="flex gap-1">
            {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `${focusRing} flex items-center gap-2 px-4 py-2 font-medium motion-safe:transition ${
                      isActive ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-surface-2'
                    }`
                  }
                >
                  <Icon width={20} height={20} />
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}

function BottomTabBar() {
  return (
    <nav
      aria-label="Main tabs"
      className="fixed inset-x-0 bottom-0 z-10 border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <ul className="grid grid-cols-5">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex min-h-14 flex-col items-center justify-center gap-0.5 text-xs font-medium no-underline ${
                  isActive ? 'text-accent' : 'text-muted'
                }`
              }
            >
              <Icon />
              <span>{label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function AppShell() {
  const mainRef = useRef<HTMLElement>(null);

  // A bare #main anchor does not reliably move keyboard focus, so do it explicitly.
  const skipToContent = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    mainRef.current?.focus();
  };

  return (
    <div className="min-h-dvh bg-canvas text-ink">
      <a
        href="#main"
        onClick={skipToContent}
        className="sr-only z-20 rounded-full bg-accent px-4 py-2 text-on-accent no-underline focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <TopNav />
      <main
        id="main"
        ref={mainRef}
        tabIndex={-1}
        className="mx-auto outline-none max-w-3xl px-4 pt-6 pb-28 md:px-6 md:pb-12"
      >
        <Outlet />
      </main>
      <BottomTabBar />
    </div>
  );
}
