type Tone = "success" | "danger" | "neutral" | "gold";

export function StatusDot({ tone }: { tone: Tone }) {
  const color =
    tone === "success" ? "bg-success" : tone === "danger" ? "bg-danger" : tone === "gold" ? "bg-gold" : "bg-muted";
  return <span className={`inline-block h-2 w-2 rounded-full ${color}`} aria-hidden />;
}