import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { useViewerId } from './useSession';

export type NotificationOut = Schemas['NotificationOut'];
export type NotificationSettings = Schemas['NotificationSettings'];
export type NotificationSettingsPatch = Schemas['NotificationSettingsPatch'];
export type PushConfigOut = Schemas['PushConfigOut'];
export type PushSubscriptionIn = Schemas['PushSubscriptionIn'];

export const notificationsKey = (userId: string | undefined) => ['notifications', userId] as const;
export const unreadCountKey = (userId: string | undefined) =>
  ['notifications-unread-count', userId] as const;
export const pushConfigKey = (userId: string | undefined) => ['push-config', userId] as const;
export const notificationSettingsKey = (userId: string | undefined) =>
  ['notification-settings', userId] as const;

export function useNotifications() {
  const userId = useViewerId();
  return useQuery({
    queryKey: notificationsKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/notifications')),
  });
}

export function useUnreadNotificationCount() {
  const userId = useViewerId();
  return useQuery({
    queryKey: unreadCountKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/notifications/unread-count')),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function useMarkNotificationRead() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await client.POST('/api/v1/notifications/{notification_id}/read', {
          params: { path: { notification_id: id } },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: notificationsKey(userId) });
      void queryClient.invalidateQueries({ queryKey: unreadCountKey(userId) });
    },
  });
}

export function useMarkAllNotificationsRead() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await client.POST('/api/v1/notifications/read-all')),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: notificationsKey(userId) });
      void queryClient.invalidateQueries({ queryKey: unreadCountKey(userId) });
    },
  });
}

export function usePushConfig() {
  const userId = useViewerId();
  return useQuery({
    queryKey: pushConfigKey(userId),
    enabled:
      !!userId &&
      typeof navigator !== 'undefined' &&
      'serviceWorker' in navigator &&
      typeof PushManager !== 'undefined',
    queryFn: async () => unwrap(await client.GET('/api/v1/notifications/push-config')),
  });
}

export function useAddPushSubscription() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: PushSubscriptionIn) =>
      unwrap(await client.POST('/api/v1/me/push-subscriptions', { body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: pushConfigKey(userId) });
    },
  });
}

export function useDeletePushSubscription() {
  return useMutation({
    mutationFn: async (endpoint: string) =>
      unwrap<undefined>(
        await client.DELETE('/api/v1/me/push-subscriptions', { params: { query: { endpoint } } }),
      ),
  });
}

export function useNotificationSettings(enabled = true) {
  const userId = useViewerId();
  return useQuery({
    queryKey: notificationSettingsKey(userId),
    enabled: !!userId && enabled,
    queryFn: async () => unwrap(await client.GET('/api/v1/me/notification-settings')),
  });
}

export function useUpdateNotificationSettings() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: NotificationSettingsPatch) =>
      unwrap(await client.PUT('/api/v1/me/notification-settings', { body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: notificationSettingsKey(userId) });
    },
  });
}
