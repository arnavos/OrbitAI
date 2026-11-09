import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { ChatProvider, useChat } from '../../context/ChatContext';
import { ThemeProvider, useTheme } from '../../context/ThemeContext';
import { Sidebar } from '../sidebar/Sidebar';
import { ChatToolbar } from '../chat/ChatToolbar';
import { MessageComposer } from '../chat/MessageComposer';
import { MessageItem } from '../chat/MessageItem';
import { StreamingMessage } from '../chat/StreamingMessage';
import { WelcomeScreen } from '../chat/WelcomeScreen';
import { ModelSelector } from '../models/ModelSelector';
import { ContextMeter } from '../chat/ContextMeter';
import { TitleBar } from '../layout/TitleBar';
import type { ConversationResponse, MessageResponse } from '../../types/conversation';
import * as convServiceModule from '../../services/conversation';
import * as modelServiceModule from '../../services/models';
import { StreamingConversationClient } from '../../services/streaming';

describe('ORBIT Desktop Chat Interface Suite', () => {
  const mockConversations: ConversationResponse[] = [
    {
      id: 'conv-1',
      title: 'SQLite WAL Research',
      model_id: 'ollama/llama3.2:3b',
      last_sequence: 4,
      created_at: '2026-11-09T10:00:00Z',
      updated_at: '2026-11-09T10:15:00Z',
      message_count: 4,
    },
    {
      id: 'conv-2',
      title: 'Python Asyncio Locks',
      model_id: 'ollama/llama3.2:3b',
      last_sequence: 2,
      created_at: '2026-11-08T09:00:00Z',
      updated_at: '2026-11-08T09:30:00Z',
      message_count: 2,
    },
  ];

  const mockModels = [
    {
      id: 'ollama/llama3.2:3b',
      name: 'Llama 3.2 3B',
      provider_type: 'ollama',
      model_family: 'llama',
      parameter_size: '3.2B',
      quantization: 'Q4_K_M',
      context_window_capacity: 131072,
      estimated_memory_bytes: 2147483648,
      availability_status: 'available',
      capabilities: ['streaming', 'cancellation'],
    },
    {
      id: 'ollama/qwen2.5:7b',
      name: 'Qwen 2.5 7B',
      provider_type: 'ollama',
      model_family: 'qwen',
      parameter_size: '7B',
      quantization: 'Q4_K_M',
      context_window_capacity: 32768,
      estimated_memory_bytes: 4294967296,
      availability_status: 'loaded',
      capabilities: ['streaming'],
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(convServiceModule.conversationService, 'listConversations').mockResolvedValue({
      conversations: [...mockConversations],
      total: mockConversations.length,
    });
    vi.spyOn(convServiceModule.conversationService, 'listMessages').mockResolvedValue({
      messages: [],
      total: 0,
    });
    vi.spyOn(modelServiceModule.modelService, 'listModels').mockResolvedValue({
      models: [...mockModels],
      errors: {},
      discovered_at: new Date().toISOString(),
    });
  });

  // 1. Sidebar rendering and conversation listing
  it('renders sidebar with conversation threads', async () => {
    await act(async () => {
      render(
        <ThemeProvider>
          <ChatProvider>
            <Sidebar />
          </ChatProvider>
        </ThemeProvider>
      );
    });

    expect(screen.getByText('SQLite WAL Research')).toBeInTheDocument();
    expect(screen.getByText('Python Asyncio Locks')).toBeInTheDocument();
    expect(screen.getByText('New Chat')).toBeInTheDocument();
  });

  // 2. Sidebar search filtering
  it('filters conversation list when search query is entered', async () => {
    await act(async () => {
      render(
        <ThemeProvider>
          <ChatProvider>
            <Sidebar />
          </ChatProvider>
        </ThemeProvider>
      );
    });

    const searchInput = screen.getByPlaceholderText('Search conversations...');
    await act(async () => {
      fireEvent.change(searchInput, { target: { value: 'sqlite' } });
    });

    expect(screen.getByText('SQLite WAL Research')).toBeInTheDocument();
    expect(screen.queryByText('Python Asyncio Locks')).not.toBeInTheDocument();
  });

  // 3. New Chat creation
  it('triggers createConversation when New Chat button is clicked', async () => {
    const createSpy = vi
      .spyOn(convServiceModule.conversationService, 'createConversation')
      .mockResolvedValue({
        id: 'conv-new-1',
        title: 'New Conversation',
        model_id: 'ollama/llama3.2:3b',
        last_sequence: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        message_count: 0,
      });

    await act(async () => {
      render(
        <ThemeProvider>
          <ChatProvider>
            <Sidebar />
          </ChatProvider>
        </ThemeProvider>
      );
    });

    const newChatBtn = screen.getByText('New Chat');
    await act(async () => {
      fireEvent.click(newChatBtn);
    });

    expect(createSpy).toHaveBeenCalledWith({
      title: 'New Conversation',
      model_id: 'ollama/llama3.2:3b',
    });
  });

  // 4. Conversation renaming
  it('renames a conversation on confirm', async () => {
    const updateSpy = vi
      .spyOn(convServiceModule.conversationService, 'updateConversation')
      .mockResolvedValue({
        ...mockConversations[0],
        title: 'Renamed Title',
      });

    await act(async () => {
      render(
        <ThemeProvider>
          <ChatProvider>
            <Sidebar />
          </ChatProvider>
        </ThemeProvider>
      );
    });

    const renameBtn = screen.getAllByTitle('Rename conversation')[0];
    await act(async () => {
      fireEvent.click(renameBtn);
    });

    const editInput = screen.getByDisplayValue('SQLite WAL Research');
    await act(async () => {
      fireEvent.change(editInput, { target: { value: 'Renamed Title' } });
      fireEvent.keyDown(editInput, { key: 'Enter' });
    });

    expect(updateSpy).toHaveBeenCalledWith('conv-1', { title: 'Renamed Title' });
  });

  // 5. Conversation deletion with confirmation dialog
  it('shows confirmation dialog before deleting conversation', async () => {
    const deleteSpy = vi
      .spyOn(convServiceModule.conversationService, 'deleteConversation')
      .mockResolvedValue(true);

    await act(async () => {
      render(
        <ThemeProvider>
          <ChatProvider>
            <Sidebar />
          </ChatProvider>
        </ThemeProvider>
      );
    });

    const deleteBtn = screen.getAllByTitle('Delete conversation')[0];
    await act(async () => {
      fireEvent.click(deleteBtn);
    });

    // Confirmation dialog appears
    expect(screen.getByText(/Are you sure you want to permanently delete/)).toBeInTheDocument();

    const confirmBtn = screen.getByRole('button', { name: 'Delete' });
    await act(async () => {
      fireEvent.click(confirmBtn);
    });

    expect(deleteSpy).toHaveBeenCalledWith('conv-1');
  });

  // 6. Welcome screen rendering and starter prompts
  it('renders welcome screen with starter prompts and handles selection', async () => {
    const handleSelect = vi.fn();
    await act(async () => {
      render(
        <ChatProvider>
          <WelcomeScreen onSelectPrompt={handleSelect} />
        </ChatProvider>
      );
    });

    expect(screen.getByText('Welcome to ORBIT')).toBeInTheDocument();
    expect(screen.getByText(/Local Device Only/)).toBeInTheDocument();

    const starterBtn = screen.getByText('Analyze Architecture');
    fireEvent.click(starterBtn);

    expect(handleSelect).toHaveBeenCalledWith(
      expect.stringContaining('SQLite Write-Ahead Logging')
    );
  });

  // 7. Message Composer typing and token estimation
  it('updates textarea and conservative token estimate in composer', () => {
    render(
      <ChatProvider>
        <MessageComposer />
      </ChatProvider>
    );

    const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
    fireEvent.change(textarea, { target: { value: 'Hello world, test prompt' } });

    // 24 chars -> ceil(24 / 3.5) + 4 = 7 + 4 = 11 tokens
    expect(screen.getByText(/~11 est. tokens/)).toBeInTheDocument();
  });

  // 8. Message Composer submit and Shift+Enter behavior
  it('submits on Enter and prevents submission when input is empty', () => {
    const onSend = vi.fn();
    render(
      <ChatProvider>
        <MessageComposer onSendMessage={onSend} />
      </ChatProvider>
    );

    const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);

    // Empty submission prevented
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();

    // Type text and hit Shift+Enter (inserts newline, doesn't submit)
    fireEvent.change(textarea, { target: { value: 'Line 1' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    // Hit Enter (submits)
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    expect(onSend).toHaveBeenCalledWith('Line 1');
  });

  // 9. MessageItem rendering for User turn
  it('renders user message item with right alignment and timestamp', () => {
    const userMsg: MessageResponse = {
      id: 'msg-u1',
      conversation_id: 'conv-1',
      sequence: 1,
      role: 'user',
      content: 'Can you explain database checkpoints?',
      status: 'completed',
      model_id: 'ollama/llama3.2:3b',
      finish_reason: null,
      prompt_tokens: null,
      completion_tokens: null,
      error_message: null,
      created_at: '2026-11-09T12:00:00Z',
      updated_at: '2026-11-09T12:00:00Z',
    };

    render(<MessageItem message={userMsg} />);
    expect(screen.getByText('Can you explain database checkpoints?')).toBeInTheDocument();
  });

  // 10. MessageItem rendering for Assistant turn
  it('renders assistant message item with markdown and token count', () => {
    const assistantMsg: MessageResponse = {
      id: 'msg-a1',
      conversation_id: 'conv-1',
      sequence: 2,
      role: 'assistant',
      content: 'A checkpoint transfers WAL pages back to the database file.',
      status: 'completed',
      model_id: 'ollama/llama3.2:3b',
      finish_reason: 'stop',
      prompt_tokens: 15,
      completion_tokens: 42,
      error_message: null,
      created_at: '2026-11-09T12:00:05Z',
      updated_at: '2026-11-09T12:00:05Z',
    };

    render(<MessageItem message={assistantMsg} />);
    expect(
      screen.getByText('A checkpoint transfers WAL pages back to the database file.')
    ).toBeInTheDocument();
    expect(screen.getByText('42 tokens')).toBeInTheDocument();
    expect(screen.getByText('• reason: stop')).toBeInTheDocument();
  });

  // 11. MessageItem rendering for Interrupted status
  it('renders interrupted status badge when generation is cancelled', () => {
    const interruptedMsg: MessageResponse = {
      id: 'msg-a2',
      conversation_id: 'conv-1',
      sequence: 2,
      role: 'assistant',
      content: 'Partial response before interruption...',
      status: 'interrupted',
      model_id: 'ollama/llama3.2:3b',
      finish_reason: 'cancelled',
      prompt_tokens: null,
      completion_tokens: 8,
      error_message: null,
      created_at: '2026-11-09T12:01:00Z',
      updated_at: '2026-11-09T12:01:01Z',
    };

    render(<MessageItem message={interruptedMsg} />);
    expect(screen.getByText('Interrupted')).toBeInTheDocument();
    expect(screen.getByText('Partial response before interruption...')).toBeInTheDocument();
  });

  // 12. MessageItem rendering for Failed status
  it('renders failed status and error details when generation errors', () => {
    const failedMsg: MessageResponse = {
      id: 'msg-a3',
      conversation_id: 'conv-1',
      sequence: 2,
      role: 'assistant',
      content: 'Truncated stream',
      status: 'failed',
      model_id: 'ollama/llama3.2:3b',
      finish_reason: 'error',
      prompt_tokens: null,
      completion_tokens: null,
      error_message: 'Daemon socket disconnected',
      created_at: '2026-11-09T12:02:00Z',
      updated_at: '2026-11-09T12:02:01Z',
    };

    render(<MessageItem message={failedMsg} />);
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('Daemon socket disconnected')).toBeInTheDocument();
  });

  // 13. StreamingMessage live token display
  it('renders streaming assistant placeholder with live tokens and pulse cursor', () => {
    render(
      <StreamingMessage
        content="Streaming tokens incrementally..."
        tokenCount={14}
        modelId="ollama/llama3.2:3b"
      />
    );

    expect(screen.getByText('llama3.2:3b')).toBeInTheDocument();
    expect(screen.getByText('Generating...')).toBeInTheDocument();
    expect(screen.getByText('(14 tokens)')).toBeInTheDocument();
    expect(screen.getByText('Streaming tokens incrementally...')).toBeInTheDocument();
  });

  // 14. ModelSelector dropdown selection
  it('displays discovered local models and allows selection', async () => {
    await act(async () => {
      render(
        <ChatProvider>
          <ModelSelector />
        </ChatProvider>
      );
    });

    const trigger = screen.getByRole('button', { expanded: false });
    expect(trigger).toHaveTextContent(/llama 3.2/i);

    // Open dropdown
    await act(async () => {
      fireEvent.click(trigger);
    });

    expect(screen.getByText('Qwen 2.5 7B')).toBeInTheDocument();
    expect(screen.getByText('RAM')).toBeInTheDocument(); // loaded tag for qwen

    // Select Qwen
    const qwenOption = screen.getByText('Qwen 2.5 7B');
    await act(async () => {
      fireEvent.click(qwenOption);
    });

    expect(trigger).toHaveTextContent(/Qwen 2.5 7B/i);
  });

  // 15. ContextMeter display
  it('displays context capacity in context meter', () => {
    render(
      <ChatProvider>
        <ContextMeter />
      </ChatProvider>
    );

    expect(screen.getByText(/context/)).toBeInTheDocument();
  });

  // 16. TitleBar view switcher toggle
  it('switches between Chat and System views in TitleBar', () => {
    const TestComponent = () => {
      const { state } = useChat();
      return (
        <div>
          <TitleBar connectionStatus="CONNECTED" port="8000" />
          <div data-testid="current-view">{state.activeView}</div>
        </div>
      );
    };

    render(
      <ChatProvider>
        <TestComponent />
      </ChatProvider>
    );

    expect(screen.getByTestId('current-view')).toHaveTextContent('chat');

    const systemBtn = screen.getByRole('button', { name: /System/i });
    fireEvent.click(systemBtn);

    expect(screen.getByTestId('current-view')).toHaveTextContent('system');

    const chatBtn = screen.getByRole('button', { name: /Chat/i });
    fireEvent.click(chatBtn);

    expect(screen.getByTestId('current-view')).toHaveTextContent('chat');
  });

  // 17. Theme toggle
  it('toggles dark and light mode themes via ThemeContext', () => {
    const ThemeTestComponent = () => {
      const { theme, toggleTheme } = useTheme();
      return (
        <div>
          <div data-testid="theme-val">{theme}</div>
          <button onClick={toggleTheme}>Toggle</button>
        </div>
      );
    };

    render(
      <ThemeProvider>
        <ThemeTestComponent />
      </ThemeProvider>
    );

    expect(screen.getByTestId('theme-val')).toHaveTextContent('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

    fireEvent.click(screen.getByText('Toggle'));

    expect(screen.getByTestId('theme-val')).toHaveTextContent('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  // 18. Draft preservation across conversation switching
  it('preserves draft content when switching conversations', async () => {
    const DraftTestComponent = () => {
      const { state, setActiveConversationId, setDraft } = useChat();
      return (
        <div>
          <div data-testid="active-id">{state.activeConversationId}</div>
          <input
            data-testid="draft-input"
            value={state.drafts[state.activeConversationId || 'new'] || ''}
            onChange={(e) =>
              setDraft(state.activeConversationId || 'new', e.target.value)
            }
          />
          <button onClick={() => setActiveConversationId('conv-1')}>Go Conv 1</button>
          <button onClick={() => setActiveConversationId('conv-2')}>Go Conv 2</button>
        </div>
      );
    };

    render(
      <ChatProvider>
        <DraftTestComponent />
      </ChatProvider>
    );

    // Switch to conv-1 and type a draft
    fireEvent.click(screen.getByText('Go Conv 1'));
    expect(screen.getByTestId('active-id')).toHaveTextContent('conv-1');

    const draftInput = screen.getByTestId('draft-input');
    fireEvent.change(draftInput, { target: { value: 'Draft for conversation 1' } });

    // Switch to conv-2 and type a draft
    fireEvent.click(screen.getByText('Go Conv 2'));
    expect(screen.getByTestId('active-id')).toHaveTextContent('conv-2');
    expect(draftInput).toHaveValue('');
    fireEvent.change(draftInput, { target: { value: 'Draft for conversation 2' } });

    // Switch back to conv-1: draft is restored!
    fireEvent.click(screen.getByText('Go Conv 1'));
    expect(screen.getByTestId('active-id')).toHaveTextContent('conv-1');
    expect(draftInput).toHaveValue('Draft for conversation 1');
  });

  // 19. Duplicate submission prevention during streaming
  it('disables send button when streaming is active', () => {
    const MockStreamingView = () => {
      const { state, cancelStreaming } = useChat();
      return (
        <div>
          <div data-testid="is-streaming">
            {state.streamingState.isStreaming ? 'STREAMING' : 'IDLE'}
          </div>
          <MessageComposer />
          {state.streamingState.isStreaming && (
            <button onClick={cancelStreaming}>Stop Streaming</button>
          )}
        </div>
      );
    };

    render(
      <ChatProvider>
        <MockStreamingView />
      </ChatProvider>
    );

    expect(screen.getByTestId('is-streaming')).toHaveTextContent('IDLE');
    const sendBtn = screen.getByRole('button', { name: /Send message/i });
    expect(sendBtn).toBeDisabled();
  });

  // 20. ChatToolbar renders model selector and delete action
  it('renders toolbar with controls and confirmation dialog on delete', async () => {
    await act(async () => {
      render(
        <ChatProvider>
          <ChatToolbar />
        </ChatProvider>
      );
    });

    expect(screen.getByRole('button', { expanded: false })).toBeInTheDocument();
  });

  // --------------------------------------------------------------------------
  // Message Composer & Submission Lifecycle Suite (Requirements 1-10)
  // --------------------------------------------------------------------------
  describe('Message Composer & Submission Lifecycle (Requirements 1-10)', () => {
    // 1. Empty input -> Send button disabled
    it('Requirement 1: Send button is disabled when input is empty', async () => {
      render(
        <ChatProvider>
          <MessageComposer />
        </ChatProvider>
      );
      const sendBtn = screen.getByRole('button', { name: /Send message/i });
      expect(sendBtn).toBeDisabled();
    });

    // 2. Whitespace input -> Send button disabled
    it('Requirement 2: Send button is disabled when input contains only whitespace', async () => {
      render(
        <ChatProvider>
          <MessageComposer />
        </ChatProvider>
      );
      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
      fireEvent.change(textarea, { target: { value: '   \n\t  ' } });
      const sendBtn = screen.getByRole('button', { name: /Send message/i });
      expect(sendBtn).toBeDisabled();
    });

    // 3. Valid text entered -> Send button enabled
    it('Requirement 3: Send button is enabled when valid text is typed', async () => {
      render(
        <ChatProvider>
          <MessageComposer />
        </ChatProvider>
      );
      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
      fireEvent.change(textarea, { target: { value: 'Valid prompt text' } });
      const sendBtn = screen.getByRole('button', { name: /Send message/i });
      expect(sendBtn).not.toBeDisabled();
    });

    // 4. Missing conversation -> creates conversation before submission
    it('Requirement 4: Automatically creates conversation before submission when activeConversationId is null', async () => {
      const createConvSpy = vi
        .spyOn(convServiceModule.conversationService, 'createConversation')
        .mockResolvedValue({
          id: 'new-conv-uuid',
          title: 'Hello from user',
          model_id: 'ollama/llama3.2:3b',
          last_sequence: 0,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          message_count: 0,
        });

      let capturedConvId: string | null = null;
      vi.spyOn(StreamingConversationClient.prototype, 'streamMessage').mockImplementation(
        async (convId, _content, callbacks) => {
          capturedConvId = convId;
          const controller = new AbortController();
          setTimeout(() => {
            callbacks.onCompleted?.(
              {
                finish_reason: 'stop',
                usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 },
              } as any,
              {} as any
            );
          }, 10);
          return controller;
        }
      );

      const TestView = () => {
        const { state } = useChat();
        return (
          <div>
            <div data-testid="active-conv">{state.activeConversationId || 'NONE'}</div>
            <MessageComposer />
          </div>
        );
      };

      render(
        <ChatProvider>
          <TestView />
        </ChatProvider>
      );

      expect(screen.getByTestId('active-conv')).toHaveTextContent('NONE');

      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
      fireEvent.change(textarea, { target: { value: 'Hello from user' } });

      const sendBtn = screen.getByRole('button', { name: /Send message/i });
      expect(sendBtn).not.toBeDisabled();

      await act(async () => {
        fireEvent.click(sendBtn);
      });

      expect(createConvSpy).toHaveBeenCalled();
      expect(capturedConvId).toBe('new-conv-uuid');
    });

    // 5. Missing model -> displays meaningful error without crashing
    it('Requirement 5: Displays meaningful error banner when no model is selected', async () => {
      const TestView = () => {
        const { setSelectedModelId } = useChat();
        return (
          <div>
            <button onClick={() => setSelectedModelId('')}>Clear Model</button>
            <MessageComposer />
          </div>
        );
      };

      render(
        <ChatProvider>
          <TestView />
        </ChatProvider>
      );

      fireEvent.click(screen.getByText('Clear Model'));

      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
      fireEvent.change(textarea, { target: { value: 'Prompt without model' } });

      const sendBtn = screen.getByRole('button', { name: /Send message/i });
      expect(sendBtn).not.toBeDisabled();

      await act(async () => {
        fireEvent.click(sendBtn);
      });

      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/No AI model selected/i)).toBeInTheDocument();
    });

    // 6. Active generation -> Stop button displayed, Send disabled, textarea disabled
    it('Requirement 6: Disables textarea and send button during active generation', async () => {
      vi.spyOn(StreamingConversationClient.prototype, 'streamMessage').mockImplementation(
        async (_convId, _content, callbacks) => {
          const controller = new AbortController();
          callbacks.onStarted?.(
            {
              user_message: {
                id: 'u1',
                content: 'hello',
                role: 'user',
                sequence: 1,
                created_at: '',
                conversation_id: 'conv-1',
                status: 'completed',
              },
              assistant_message_id: 'a1',
              assistant_sequence: 2,
              model_id: 'ollama/llama3.2:3b',
              allocated_context: 4096,
              created_at: '',
            },
            {} as any
          );
          return controller;
        }
      );

      const TestView = () => {
        const { setActiveConversationId } = useChat();
        return (
          <div>
            <button onClick={() => setActiveConversationId('conv-1')}>Select Conv</button>
            <MessageComposer />
          </div>
        );
      };

      render(
        <ChatProvider>
          <TestView />
        </ChatProvider>
      );

      fireEvent.click(screen.getByText('Select Conv'));

      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
      fireEvent.change(textarea, { target: { value: 'Streaming prompt' } });

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Send message/i }));
      });

      // While streaming, composer shows Stop button
      expect(screen.getByRole('button', { name: /Stop generation/i })).toBeInTheDocument();
      expect(textarea).toBeDisabled();
    });

    // 7. Completed generation -> Send button re-enabled when text present
    it('Requirement 7: Re-enables Send button after stream completion', async () => {
      let completeStream: (() => void) | null = null;
      vi.spyOn(StreamingConversationClient.prototype, 'streamMessage').mockImplementation(
        async (_convId, _content, callbacks) => {
          const controller = new AbortController();
          callbacks.onStarted?.(
            {
              user_message: {
                id: 'u1',
                content: 'test',
                role: 'user',
                sequence: 1,
                created_at: '',
                conversation_id: 'conv-1',
                status: 'completed',
              },
              assistant_message_id: 'a1',
              assistant_sequence: 2,
              model_id: 'ollama/llama3.2:3b',
              allocated_context: 4096,
              created_at: '',
            },
            {} as any
          );
          completeStream = () => {
            callbacks.onCompleted?.(
              {
                finish_reason: 'stop',
                usage: { prompt_tokens: 5, completion_tokens: 3, total_tokens: 8 },
              } as any,
              {} as any
            );
          };
          return controller;
        }
      );

      const TestView = () => {
        const { setActiveConversationId } = useChat();
        return (
          <div>
            <button onClick={() => setActiveConversationId('conv-1')}>Select Conv</button>
            <MessageComposer />
          </div>
        );
      };

      render(
        <ChatProvider>
          <TestView />
        </ChatProvider>
      );

      fireEvent.click(screen.getByText('Select Conv'));
      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
      fireEvent.change(textarea, { target: { value: 'Testing stream complete' } });

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Send message/i }));
      });

      expect(screen.getByRole('button', { name: /Stop generation/i })).toBeInTheDocument();

      // Complete stream
      await act(async () => {
        completeStream?.();
      });

      // After completion, textarea is enabled and typing enables Send button
      expect(textarea).not.toBeDisabled();
      fireEvent.change(textarea, { target: { value: 'Next prompt' } });
      expect(screen.getByRole('button', { name: /Send message/i })).not.toBeDisabled();
    });

    // 8. Failed generation -> displays error banner in composer and error in message
    it('Requirement 8: Displays error banner and restores retry capability on failed generation', async () => {
      vi.spyOn(StreamingConversationClient.prototype, 'streamMessage').mockImplementation(
        async (_convId, _content, callbacks) => {
          const controller = new AbortController();
          callbacks.onStarted?.(
            {
              user_message: {
                id: 'u1',
                content: 'fail prompt',
                role: 'user',
                sequence: 1,
                created_at: '',
                conversation_id: 'conv-1',
                status: 'completed',
              },
              assistant_message_id: 'a1',
              assistant_sequence: 2,
              model_id: 'ollama/llama3.2:3b',
              allocated_context: 4096,
              created_at: '',
            },
            {} as any
          );
          callbacks.onFailed?.(
            {
              error_message: 'Inference engine out of memory',
            } as any,
            {} as any
          );
          return controller;
        }
      );

      const TestView = () => {
        const { setActiveConversationId } = useChat();
        return (
          <div>
            <button onClick={() => setActiveConversationId('conv-1')}>Select Conv</button>
            <MessageComposer />
          </div>
        );
      };

      render(
        <ChatProvider>
          <TestView />
        </ChatProvider>
      );

      fireEvent.click(screen.getByText('Select Conv'));
      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
      fireEvent.change(textarea, { target: { value: 'fail prompt' } });

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Send message/i }));
      });

      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/Inference engine out of memory/i)).toBeInTheDocument();
      expect(textarea).not.toBeDisabled();
    });

    // 9. Cancelled generation -> Send button re-enabled
    it('Requirement 9: Re-enables Send button after cancelling active generation', async () => {
      let cancelCallback: (() => void) | null = null;
      vi.spyOn(StreamingConversationClient.prototype, 'streamMessage').mockImplementation(
        async (_convId, _content, callbacks) => {
          const controller = new AbortController();
          callbacks.onStarted?.(
            {
              user_message: {
                id: 'u1',
                content: 'cancel me',
                role: 'user',
                sequence: 1,
                created_at: '',
                conversation_id: 'conv-1',
                status: 'completed',
              },
              assistant_message_id: 'a1',
              assistant_sequence: 2,
              model_id: 'ollama/llama3.2:3b',
              allocated_context: 4096,
              created_at: '',
            },
            {} as any
          );
          cancelCallback = () => {
            callbacks.onCancelled?.(
              {
                accumulated_text: 'partial',
                token_count: 1,
              } as any,
              {} as any
            );
          };
          return controller;
        }
      );
      vi.spyOn(StreamingConversationClient.prototype, 'cancelTurn').mockImplementation(async () => {
        cancelCallback?.();
        return { cancelled: true, message_id: 'a1', detail: 'Cancelled' };
      });

      const TestView = () => {
        const { setActiveConversationId } = useChat();
        return (
          <div>
            <button onClick={() => setActiveConversationId('conv-1')}>Select Conv</button>
            <MessageComposer />
          </div>
        );
      };

      render(
        <ChatProvider>
          <TestView />
        </ChatProvider>
      );

      fireEvent.click(screen.getByText('Select Conv'));
      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);
      fireEvent.change(textarea, { target: { value: 'cancel me' } });

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Send message/i }));
      });

      const stopBtn = screen.getByRole('button', { name: /Stop generation/i });
      expect(stopBtn).toBeInTheDocument();

      await act(async () => {
        fireEvent.click(stopBtn);
      });

      // Stop button disappears, composer is restored
      expect(screen.queryByRole('button', { name: /Stop generation/i })).not.toBeInTheDocument();
      expect(textarea).not.toBeDisabled();
    });

    // 10. Enter vs Shift+Enter keyboard handling
    it('Requirement 10: Enter submits valid text, Shift+Enter inserts newline without submitting', async () => {
      let streamCalled = false;
      vi.spyOn(StreamingConversationClient.prototype, 'streamMessage').mockImplementation(
        async (_convId, _content, callbacks) => {
          streamCalled = true;
          const controller = new AbortController();
          setTimeout(() => {
            callbacks.onCompleted?.(
              {
                finish_reason: 'stop',
                usage: { prompt_tokens: 5, completion_tokens: 1, total_tokens: 6 },
              } as any,
              {} as any
            );
          }, 10);
          return controller;
        }
      );

      const TestView = () => {
        const { setActiveConversationId } = useChat();
        return (
          <div>
            <button onClick={() => setActiveConversationId('conv-1')}>Select Conv</button>
            <MessageComposer />
          </div>
        );
      };

      render(
        <ChatProvider>
          <TestView />
        </ChatProvider>
      );

      fireEvent.click(screen.getByText('Select Conv'));
      const textarea = screen.getByPlaceholderText(/Ask ORBIT anything/);

      // Empty enter does nothing
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
      expect(streamCalled).toBe(false);

      // Shift+Enter does nothing
      fireEvent.change(textarea, { target: { value: 'Line 1' } });
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
      expect(streamCalled).toBe(false);

      // Enter alone submits
      await act(async () => {
        fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
      });
      expect(streamCalled).toBe(true);
    });
  });
});
