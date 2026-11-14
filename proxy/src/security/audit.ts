/**
 * Allow-listed audit trail for security rejections.
 *
 * Only events in `AUDIT_EVENTS` are accepted, and the sink copies exactly the
 * five approved fields. Credentials, tokens, cookies, headers and exception
 * text can never be forwarded by construction.
 */
export const AUDIT_EVENTS = [
  "auth.login.rejected_origin",
  "auth.login.rejected_content_type",
  "auth.login.rejected_body",
  "auth.login.rejected_csrf",
  "auth.login.rejected_credentials",
  "auth.login.upstream_error",
  "auth.logout.rejected_origin",
  "auth.logout.rejected_content_type",
  "auth.logout.rejected_csrf",
] as const;

export type AuditEvent = (typeof AUDIT_EVENTS)[number];

export type AuditCode =
  | "FORBIDDEN"
  | "VALIDATION"
  | "UNSUPPORTED"
  | "UPSTREAM_UNAVAILABLE";

export interface AuditRecord {
  event: AuditEvent;
  ipHash: string;
  route: string;
  status: number;
  code: AuditCode;
}

export type AuditSink = (record: AuditRecord) => void;

const ALLOWED_EVENTS: ReadonlySet<string> = new Set(AUDIT_EVENTS);

export function createAuditSink(
  write: (record: AuditRecord) => void,
): AuditSink {
  return (record) => {
    if (!ALLOWED_EVENTS.has(record.event)) {
      return;
    }

    write({
      event: record.event,
      ipHash: record.ipHash,
      route: record.route,
      status: record.status,
      code: record.code,
    });
  };
}

/**
 * Default audit sink: one JSON line per rejection on stderr. This keeps working
 * when the Fastify logger is disabled (`logger: false`), while the record shape
 * stays restricted to the allow-listed, non-sensitive fields above.
 */
export function createStderrAuditSink(
  writeLine: (line: string) => void = (line) => {
    process.stderr.write(line);
  },
): AuditSink {
  return createAuditSink((record) => {
    writeLine(`${JSON.stringify(record)}\n`);
  });
}
