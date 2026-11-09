import React from 'react';
import { useStreamingChat } from '../../hooks/useStreamingChat';
import { useConversations } from '../../hooks/useConversations';
import { ChatToolbar } from './ChatToolbar';
import { MessageList } from './MessageList';
import { WelcomeScreen } from './WelcomeScreen';
import { MessageComposer } from './MessageComposer';

export const ChatWorkspace: React.FC = () => {
  const { messages, streamingState, updateDraft } = useStreamingChat();
  const { activeConversationId } = useConversations();

  const isStreamingHere =
    streamingState.isStreaming &&
    streamingState.conversationId === activeConversationId;

  const hasMessages = messages.length > 0 || isStreamingHere;

  const handleSelectStarterPrompt = (prompt: string) => {
    updateDraft(prompt);
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        backgroundColor: 'var(--bg-primary)',
        overflow: 'hidden',
        flex: 1,
      }}
    >
      <ChatToolbar />

      <main
        style={{
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {hasMessages ? (
          <MessageList
            messages={messages}
            streamingState={streamingState}
            activeConversationId={activeConversationId}
          />
        ) : (
          <WelcomeScreen onSelectPrompt={handleSelectStarterPrompt} />
        )}

        <MessageComposer />
      </main>
    </div>
  );
};
