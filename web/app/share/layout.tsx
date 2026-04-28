import type { ReactNode } from "react";

export default function ShareLayout({ children }: { children: ReactNode }) {
  return (
    <div style={{ minHeight: "100vh", padding: "32px 40px", maxWidth: 1100, margin: "0 auto" }}>
      {children}
    </div>
  );
}
