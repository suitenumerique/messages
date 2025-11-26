import { Component, PropsWithChildren } from 'react';
import { handle } from '@/features/utils/errors';

/**
 * Component in charge to catch error raised by its children.
 *
 * For more information : http://reactjs.org/docs/error-boundaries.html
 */
class ErrorBoundary extends Component<
  PropsWithChildren<void>,
  { hasError: boolean }
> {
  constructor(props: PropsWithChildren<void>) {
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
    return this.props.children;
  }
}

export default ErrorBoundary;
