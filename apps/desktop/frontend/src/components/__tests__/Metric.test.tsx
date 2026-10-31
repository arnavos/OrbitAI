import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Metric } from '../ui/Metric';

describe('Metric Component', () => {
  it('renders label and formatted value with unit', () => {
    render(<Metric label="CPU Usage" value={14.5} unit="%" />);
    expect(screen.getByText('CPU Usage')).toBeInTheDocument();
    expect(screen.getByText('14.5')).toBeInTheDocument();
    expect(screen.getByText('%')).toBeInTheDocument();
  });

  it('renders string values correctly', () => {
    render(<Metric label="Architecture" value="aarch64" />);
    expect(screen.getByText('Architecture')).toBeInTheDocument();
    expect(screen.getByText('aarch64')).toBeInTheDocument();
  });

  it('renders explicit UNAVAILABLE state when value is null', () => {
    render(<Metric label="Memory RSS" value={null} unit="MB" />);
    expect(screen.getByText('Memory RSS')).toBeInTheDocument();
    expect(screen.getByText('UNAVAILABLE')).toBeInTheDocument();
    // Unit should not be rendered when value is UNAVAILABLE
    expect(screen.queryByText('MB')).not.toBeInTheDocument();
  });

  it('renders explicit UNAVAILABLE state when value is undefined', () => {
    render(<Metric label="Process ID" value={undefined} />);
    expect(screen.getByText('Process ID')).toBeInTheDocument();
    expect(screen.getByText('UNAVAILABLE')).toBeInTheDocument();
  });
});
