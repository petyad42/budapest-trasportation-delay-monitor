/**
 * Root application shell.
 *
 * The full dashboard layout (header, left column, right column) and its
 * selection/polling state live in `Dashboard` (task 22). `App` simply mounts
 * it; the React Query provider is wired up in `main.tsx`.
 */
import Dashboard from "./components/Dashboard";

export default function App() {
  return <Dashboard />;
}
