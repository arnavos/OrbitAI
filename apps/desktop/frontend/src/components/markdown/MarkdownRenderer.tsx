import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CodeBlock } from './CodeBlock';

export interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({
  content,
  className = '',
}) => {
  return (
    <div className={`markdown-body ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre({ children }) {
            return <>{children}</>;
          },
          code({ className: codeClassName, children, ...props }) {
            const match = /language-(\w+)/.exec(codeClassName || '');
            const codeString = String(children).replace(/\n$/, '');
            const hasMultipleLines = codeString.includes('\n');

            // If language is declared or string has multiple lines, treat as code block
            if (match || hasMultipleLines) {
              return (
                <CodeBlock
                  language={match ? match[1] : 'text'}
                  code={codeString}
                />
              );
            }

            return (
              <code className={codeClassName} {...props}>
                {children}
              </code>
            );
          },
          a({ href, children, ...props }) {
            const handleLinkClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
              e.preventDefault();
              if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
                window.open(href, '_blank', 'noopener,noreferrer');
              }
            };

            return (
              <a
                href={href}
                onClick={handleLinkClick}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  color: 'var(--accent-primary)',
                  textDecoration: 'underline',
                }}
                {...props}
              >
                {children}
              </a>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};
