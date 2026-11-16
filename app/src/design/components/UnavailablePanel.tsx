import type { ReactNode } from "react";
import { Card } from "./Card";

/**
 * Explicit "nothing is wired here" panel. Used where a designed surface has no
 * verified connection: it names the state instead of rendering invented data.
 */
export function UnavailablePanel({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <Card>
      <div className="pc-empty">
        <h2 className="pc-empty__title">{title}</h2>
        {detail && <p>{detail}</p>}
        {action}
      </div>
    </Card>
  );
}
