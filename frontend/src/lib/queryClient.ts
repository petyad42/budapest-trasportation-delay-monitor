import { QueryClient } from "@tanstack/react-query";

/**
 * Shared React Query client. Server state (delays, rankings, weather, ...) is
 * fetched and cached here; individual queries set their own polling intervals
 * so the dashboard refreshes without a full page reload (Requirements 1.5,
 * 3.4). Retries are kept modest so a downed backend surfaces an error/degraded
 * state quickly (Requirement 10.3) rather than hanging.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
});
