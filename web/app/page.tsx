import { AuthGate } from "@/components/Auth";
import { ReportList } from "@/components/ReportList";

export default function Home() {
  return (
    <AuthGate>
      <div className="byline">Forte Research · Home</div>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Latest</h1>
      <ReportList />
    </AuthGate>
  );
}
