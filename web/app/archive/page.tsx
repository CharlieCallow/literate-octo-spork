import { AuthGate } from "@/components/Auth";
import { ArchiveTable } from "@/components/ArchiveTable";

export default function ArchivePage() {
  return (
    <AuthGate>
      <div className="byline">Forte Research · Archive</div>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>All reports</h1>
      <ArchiveTable />
    </AuthGate>
  );
}
