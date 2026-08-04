import type { MemberRow } from "@/shared/types";
import { Button } from "@/shared/ui/Button";
import { StatusDot } from "@/shared/ui/StatusDot";

interface MemberCardProps {
  member: MemberRow;
  onCatch: (membershipId: string) => void;
  busy: boolean;
}

export function MemberCard({ member, onCatch, busy }: MemberCardProps) {
  const tone = member.status === "done" ? "success" : member.status === "missed" ? "danger" : "neutral";
  return (
    <div className="flex items-center justify-between gap-3 rounded-card bg-surface p-4">
      <div className="flex min-w-0 items-center gap-3">
        <StatusDot tone={tone} />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{member.first_name}</div>
          <div className="text-xs text-muted">
            {member.checkin_count > 0
              ? `📅 ${member.checkin_count} чек`
              : "ещё не отмечался"}
          </div>
        </div>
      </div>
      {member.can_catch ? (
        <Button variant="danger" loading={busy} onClick={() => onCatch(member.membership_id)}>
          Спалить
        </Button>
      ) : (
        <span className="text-xs text-muted">—</span>
      )}
    </div>
  );
}