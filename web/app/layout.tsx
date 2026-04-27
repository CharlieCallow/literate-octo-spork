import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "Forte Research",
  description: "Multi-agent thematic research reports",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="layout">
          <aside className="sidebar">
            <h1>FORTE RESEARCH</h1>
            <div className="accent" />
            <nav>
              <a href="/">Home</a>
              <a href="/inbox">Inbox</a>
              <a href="/new">New report</a>
              <a href="/archive">Archive</a>
              <a href="/team">Team</a>
              <a href="/settings">Settings</a>
            </nav>
          </aside>
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
