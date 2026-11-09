import React, { useRef, useEffect } from 'react';
import type { MessageResponse } from '../../types/conversation';
import type { ActiveStreamingState } from '../../types/conversation';
import { MessageItem } from './MessageItem';
import { StreamingMessage } from './StreamingMessage';

export interface MessageListProps {
  messages: MessageResponse[];
  streamingState: ActiveStreamingState;
  activeConversationId: string | null;
}

export const MessageList: React.FC<MessageListProps> = ({
  messages,
  streamingState,
  activeConversationId,
}) => {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);

  const handleScroll = () => {
    if (!scrollContainerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current;
    // User is near bottom if within 120px
    isNearBottomRef.current = scrollHeight - scrollTop - clientHeight < 120;
  };

  // Auto-scroll when messages or streaming text change, but only if user was already at bottom
  useEffect(() => {
    if (isNearBottomRef.current && scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  }, [messages, streamingState.accumulatedText]);

  // Is the current streaming turn belonging to this conversation?
  const isStreamingHere =
    streamingState.isStreaming &&
    streamingState.conversationId === activeConversationId;

  // Filter out any temporary streaming placeholder that might already be rendered in messages
  const finalizedMessages = isStreamingHere && streamingState.messageId
    ? messages.filter((m) => m.id !== streamingState.messageId)
    : messages;

  return (
    <div
      ref={scrollContainerRef}
      onScroll={handleScroll}
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '16px 24px',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div style={{ maxWidth: '820px', width: '100%', margin: '0 auto', display: 'flex', flexDirection: 'column' }}>
        {finalizedMessages.map((msg) => (
          <MessageItem key={msg.id} message={msg} />
        ))}

        {isStreamingHere && (
          <StreamingMessage
            content={streamingState.accumulatedText}
            tokenCount={streamingState.tokenCount}
            modelId={null}
          />
        )}
      </div>
    </div>
  );
};
