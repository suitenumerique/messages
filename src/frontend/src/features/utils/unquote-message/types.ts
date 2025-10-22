/**
 * Email client handler configuration
 * Each handler defines how to detect and process client-specific quoted content
 * A handler can use either:
 * - A CSS selector (simple case)
 * - A custom detect function (complex case)
 * - Both: selector to find elements, then detect to validate/process them
 */
export interface CustomHandler {
  /** Client name for documentation */
  name: string;
  /** CSS selector to find potential quote elements */
  selector: string;
  /**
   * @param matchedElement - An element matched by the selector
   * @returns An array of elements to remove/wrap
   */
  detect?: (matchedElement: HTMLElement) => HTMLElement[];
}

export type UnquoteMode = "remove" | "wrap";

/**
 * Options for unquoting messages
 */
export interface UnquoteOptions {
  /**
   * Mode for handling quoted content with html content:
   * - 'remove': Remove quoted content entirely (default)
   * - 'wrap': Wrap quoted content in collapsible <details> element
   */
  mode?: UnquoteMode;
  /**
   * Whether to preserve the forwarded content
   * If true, the upper most forwarded content will not be wrapped or removed
   */
  ignoreFirstForward?: boolean;
  /**
   * Maximum depth to search for nested quotes (performance optimization)
   * - 0: Only find top-level quotes, skip all nested quotes
   * - 1: Find top-level and first level of nested quotes
   * - 2+: Continue searching deeper
   * - undefined (default): Search all levels (infinite depth)
   *
   * Example: depth=0 will skip searching within already-found quotes
   */
  depth?: number | undefined;
}

export type DetectionMethod = "handlers" | "pattern" | null;

/**
 * Result of unquoting operation
 */
export interface UnquoteResult {
  /** The unquoted content */
  content: string;
  /** Whether any quotes were detected and removed */
  hadQuotes: boolean;
  /** Method used to detect quotes: handlers or pattern otherwise null */
  detectionMethod: DetectionMethod;
}
