"use client";
import { useEffect, useState } from "react";
import { hasAuth, setAuth, clearAuth } from "@/lib/api";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState(false);
  const [user, setUser] = useState("admin");
  const [pw, setPw] = useState("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setAuthed(hasAuth());
  }, []);

  if (!mounted) return null;
  if (authed) {
    return (
      <>
        <div style={{ float: "right" }}>
          <button onClick={() => { clearAuth(); setAuthed(false); }}>Sign out</button>
        </div>
        {children}
      </>
    );
  }

  return (
    <div className="card" style={{ maxWidth: 380 }}>
      <h2>Sign in</h2>
      <p className="muted">Single password set in <code>.env</code> (DASHBOARD_PASSWORD_HASH).</p>
      <div style={{ marginBottom: 8 }}>
        <input value={user} onChange={(e) => setUser(e.target.value)} placeholder="admin" />
      </div>
      <div style={{ marginBottom: 12 }}>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="password" />
      </div>
      <button onClick={() => { setAuth(user, pw); setAuthed(true); }}>Sign in</button>
    </div>
  );
}
