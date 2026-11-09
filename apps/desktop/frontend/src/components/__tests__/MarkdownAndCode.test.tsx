import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MarkdownRenderer } from '../markdown/MarkdownRenderer';
import { CodeBlock } from '../markdown/CodeBlock';

describe('Markdown & Code Rendering', () => {
  beforeEach(() => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  it('renders standard markdown headings, paragraphs and lists', () => {
    const md = `# Heading 1\n\nParagraph text with **bold** and *italic*.\n\n- Item 1\n- Item 2`;
    render(<MarkdownRenderer content={md} />);

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Heading 1');
    expect(screen.getByText(/Paragraph text with/)).toBeInTheDocument();
    expect(screen.getByText('bold')).toBeInTheDocument();
    expect(screen.getByText('Item 1')).toBeInTheDocument();
    expect(screen.getByText('Item 2')).toBeInTheDocument();
  });

  it('renders tables properly with GitHub-flavored markdown', () => {
    const tableMd = `| Command | Description |\n| :--- | :--- |\n| git status | Working tree state |\n| git log | Commit history |`;
    render(<MarkdownRenderer content={tableMd} />);

    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('Command')).toBeInTheDocument();
    expect(screen.getByText('Working tree state')).toBeInTheDocument();
  });

  it('renders code blocks with language indicator and copy action', async () => {
    const code = `def calculate_sum(a, b):\n    return a + b`;
    render(<CodeBlock language="python" code={code} />);

    expect(screen.getByText('python')).toBeInTheDocument();
    expect(screen.getByText(/calculate_sum/)).toBeInTheDocument();

    const copyBtn = screen.getByRole('button', { name: /Copy/i });
    expect(copyBtn).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(copyBtn);
    });

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(code);
    expect(screen.getByText('Copied!')).toBeInTheDocument();
  });

  it('renders multiline code blocks inside MarkdownRenderer using CodeBlock component', () => {
    const md = "Here is some code:\n```typescript\nconst x: number = 42;\n```";
    render(<MarkdownRenderer content={md} />);

    expect(screen.getByText('typescript')).toBeInTheDocument();
    expect(screen.getByText('const')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('sanitizes external links to open with noopener noreferrer', () => {
    const md = `Check [Orbit Documentation](https://orbit.local/docs) for details.`;
    render(<MarkdownRenderer content={md} />);

    const link = screen.getByRole('link', { name: 'Orbit Documentation' });
    expect(link).toHaveAttribute('href', 'https://orbit.local/docs');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('renders inline code without CodeBlock wrapper header', () => {
    const md = `Use the \`PRAGMA journal_mode=WAL;\` setting.`;
    render(<MarkdownRenderer content={md} />);

    const inlineCode = screen.getByText('PRAGMA journal_mode=WAL;');
    expect(inlineCode.tagName.toLowerCase()).toBe('code');
    // Inline code should not have the "Copy" button header
    expect(screen.queryByRole('button', { name: /Copy code/i })).not.toBeInTheDocument();
  });
});
