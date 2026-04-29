import type { ReactNode } from "react";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>FORTE RESEARCH</h1>
        <div className="accent" />
        <nav>
          <a href="/">Home</a>
          <a href="/inbox">Inbox</a>
          <a href="/new">New report</a>
          <a href="/archive">Archive</a>
          <a href="/positions">Positions</a>
          <a href="/team">Team</a>
          <a href="/workers">Workers</a>
          <a href="/settings">Settings</a>
        </nav>
      </aside>
      <main>{children}</main>
    </div>
  );
}
