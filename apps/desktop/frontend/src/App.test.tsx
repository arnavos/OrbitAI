import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import App from './App';

describe('App Scaffolding', () => {
  it('renders application header and title', () => {
    render(<App />);
    expect(screen.getByText('ORBIT')).toBeInTheDocument();
    expect(screen.getByText('M0 Foundation')).toBeInTheDocument();
  });

  it('displays the core architectural components', () => {
    render(<App />);
    expect(screen.getByText('Tauri 2 Native Host')).toBeInTheDocument();
    expect(screen.getByText('Python 3.13 Backend')).toBeInTheDocument();
    expect(screen.getByText('SQLite WAL Persistence')).toBeInTheDocument();
    expect(screen.getByText('Security & Sandboxing')).toBeInTheDocument();
  });

  it('renders status bar with memory budget indicator', () => {
    render(<App />);
    expect(screen.getByText('MEMORY BUDGET: 16 GB')).toBeInTheDocument();
    expect(screen.getByText('COST: ₹0/MO')).toBeInTheDocument();
  });
});
