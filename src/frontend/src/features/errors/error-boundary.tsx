import { Component, PropsWithChildren, ReactNode } from 'react';
import { handle } from '@/features/utils/errors';

type ErrorBoundaryProps = PropsWithChildren<{
  fallback?: ReactNode;
}>;

/**
 * Component in charge to catch error raised by its children.
 *
 * For more information : http://reactjs.org/docs/error-boundaries.html
 */
class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  { hasError: boolean }
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  // Log the error to Sentry if available
  componentDidCatch(error: Error) {
    handle(error);
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError && this.props.fallback) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
