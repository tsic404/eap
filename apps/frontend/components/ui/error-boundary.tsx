"use client";

import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

import { Button } from "./button";

export interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode | ((error: Error, reset: () => void) => ReactNode);
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Catches render/lifecycle errors in the subtree and renders a recoverable
 * fallback instead of unmounting the whole tree (white screen).
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error, info);
  }

  private reset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (typeof this.props.fallback === "function") {
      return this.props.fallback(error, this.reset);
    }
    if (this.props.fallback !== undefined) return this.props.fallback;

    return (
      <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-border bg-background p-8 text-center">
        <div className="flex flex-col gap-1">
          <p className="text-base font-semibold text-foreground">
            页面出错了
          </p>
          <p className="text-sm text-muted-foreground">
            {error.message || "发生了未知错误，请稍后重试。"}
          </p>
        </div>
        <Button variant="outline" onClick={this.reset}>
          重试
        </Button>
      </div>
    );
  }
}
