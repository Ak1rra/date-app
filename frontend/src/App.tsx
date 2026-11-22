import { createBrowserRouter, type RouteObject } from 'react-router';
import { AppShell } from './components/AppShell';
import { SessionBootstrap } from './components/SessionBootstrap';
import { RequireAuth } from './components/RequireAuth';
import { CalendarPage } from './pages/CalendarPage';
import { ChatPage } from './pages/ChatPage';
import { HomePage } from './pages/HomePage';
import { IdeasPage } from './pages/IdeasPage';
import { InstallPage } from './pages/InstallPage';
import { ItinerariesPage } from './pages/ItinerariesPage';
import { ItineraryPage } from './pages/ItineraryPage';
import { JoinPage } from './pages/JoinPage';
import { SurprisePage } from './pages/SurprisePage';
import { NotificationsPage } from './pages/NotificationsPage';
import { PlanPage } from './pages/PlanPage';
import { PrivacyPage } from './pages/PrivacyPage';
import { ProfilePage } from './pages/ProfilePage';
import { SetupPage } from './pages/SetupPage';
import { SignInPage } from './pages/SignInPage';
import { TemplatesPage } from './pages/TemplatesPage';
import { UsPage } from './pages/UsPage';
import { VerifyPage } from './pages/VerifyPage';

export const routes: RouteObject[] = [
  {
    element: <SessionBootstrap />,
    children: [
      { path: '/signin', element: <SignInPage /> },
      { path: '/auth/verify', element: <VerifyPage /> },
      { path: '/privacy', element: <PrivacyPage /> },
      { path: '/join', element: <JoinPage /> },
      {
        path: '/setup',
        element: (
          <RequireAuth allowIncompleteProfile>
            <SetupPage />
          </RequireAuth>
        ),
      },
      {
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          { path: '/', element: <HomePage /> },
          { path: '/ideas', element: <IdeasPage /> },
          { path: '/calendar', element: <CalendarPage /> },
          { path: '/chat', element: <ChatPage /> },
          { path: '/us', element: <UsPage /> },
          { path: '/profile', element: <ProfilePage /> },
          { path: '/notifications', element: <NotificationsPage /> },
          { path: '/plan', element: <PlanPage /> },
          { path: '/install', element: <InstallPage /> },
          { path: '/itineraries', element: <ItinerariesPage /> },
          { path: '/itineraries/:id', element: <ItineraryPage /> },
          { path: '/surprise/:id', element: <SurprisePage /> },
          { path: '/templates', element: <TemplatesPage /> },
        ],
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
