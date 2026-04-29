import { AuthGate } from "@/components/Auth";
import { CostRollup } from "@/components/CostRollup";
import { LatestReport } from "@/components/LatestReport";

export default function Home() {
  return (
    <AuthGate>
      <div className="byline">Forte Research · Home</div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Latest</h1>
        <a href="/archive" style={{ fontSize: 13 }}>View archive →</a>
      </div>
      <CostRollup />
      <LatestReport />
    </AuthGate>
  );
}
