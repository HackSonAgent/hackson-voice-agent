import { createFileRoute } from '@tanstack/react-router'
import { ConversationsProvider } from '@/context/conversations-context'
import ChatHistoryPage from '@/features/history'

// Create a wrapper component that provides the conversations context
function HistoryPageWithProvider() {
  return (
    <ConversationsProvider>
      <ChatHistoryPage />
    </ConversationsProvider>
  )
}

// Use the wrapper component in your route definition
export const Route = createFileRoute('/_authenticated/history/')({
  component: HistoryPageWithProvider,
})
