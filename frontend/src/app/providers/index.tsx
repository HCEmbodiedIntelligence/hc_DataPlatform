import { useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, type RouterProviderProps } from 'react-router-dom';
import { ErrorBoundary } from './ErrorBoundary';
import { ScopeProvider } from './ScopeProvider';
import { ToastProvider } from './ToastProvider';
import { UiProvider } from './UiProvider';

function ApplicationProviderStack({
  children,
  queryClient,
}: {
  children: ReactNode;
  queryClient: QueryClient;
}) {
  return (
    <UiProvider>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <ScopeProvider>{children}</ScopeProvider>
        </ToastProvider>
      </QueryClientProvider>
    </UiProvider>
  );
}

export function AppProviders({ router }: { router: RouterProviderProps['router'] }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
          mutations: { retry: false },
        },
      }),
  );
  return (
    <ErrorBoundary>
      <ApplicationProviderStack queryClient={queryClient}>
        <RouterProvider router={router} />
      </ApplicationProviderStack>
    </ErrorBoundary>
  );
}

export function ProviderHarness({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());
  return <ApplicationProviderStack queryClient={queryClient}>{children}</ApplicationProviderStack>;
}
