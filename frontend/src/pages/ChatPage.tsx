import { Card, EmptyState } from '../components/EmptyState';
import { ChatIcon } from '../components/Icons';

export function ChatPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Chat</h1>
      <Card>
        <EmptyState icon={<ChatIcon />} title="No conversations yet">
          Chat with your partner about shared plans here. Your private planning stays separate.
        </EmptyState>
      </Card>
    </div>
  );
}
