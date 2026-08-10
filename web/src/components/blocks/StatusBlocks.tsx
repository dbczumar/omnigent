// Inline status indicators for non-tool, non-text, non-reasoning blocks.
// Each is small enough to live in one file.
//
// - ErrorBanner: destructive Alert with `[source]` + code + message.
// - RetryIndicator: muted one-liner about an in-flight retry.
// - CompactionMarker: permanent marker shown after compaction completes.
//   The in-progress state renders as a Shimmer in ChatPage, mirroring
//   the "Working…" indicator.

import {
  AlertCircleIcon,
  BrainCircuitIcon,
  ChevronRightIcon,
  RotateCcwIcon,
  ShieldXIcon,
  ShrinkIcon,
} from "lucide-react";
import { useMemo } from "react";
import { CodeBlock, CodeBlockHeader, CodeBlockTitle } from "@/components/ai-elements/code-block";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { shortModelName } from "@/components/CostRoutingControl";
import { cn } from "@/lib/utils";
import { TOOL_SURFACE_WIDTH_CLASS } from "./toolSurface";

interface ErrorBannerProps {
  message: string;
  source: string;
  code: string;
  /** Friendly headline for a classified failure, e.g. "Claude Code can't run as root". */
  title?: string;
  /** One/two-sentence explanation of why it failed. Paired with `title`. */
  cause?: string;
  /** Concrete next step to fix it, e.g. a command to run. */
  remediation?: string;
}

/**
 * One-line English descriptions for known failure `code`s, so an
 * unclassified failure (no `title`/`cause` from the runner) still reads as a
 * sentence instead of a raw enum. Mirrors the server-side
 * `describe_failure_code` table (omnigent/runner/launch_failure.py); keep the
 * two in sync.
 */
const FAILURE_CODE_DESCRIPTIONS: Record<string, string> = {
  required_terminal_exited:
    "The agent's terminal exited unexpectedly, so the session can't continue.",
  terminal_launch_failed: "The agent's terminal couldn't be started on the host.",
  runner_error: "Something went wrong setting up the turn on the host.",
  runner_disconnected: "The connection to the host dropped unexpectedly.",
  connection_error: "The connection to the agent dropped mid-turn.",
  context_length_exceeded: "The conversation grew past the model's context window.",
  executor_error: "The agent runtime hit an error while running the turn.",
};

/**
 * Loud destructive banner for `error` blocks.
 *
 * When the runner classified the failure it passes `title` / `cause` /
 * `remediation`, and the banner renders a clear card: headline, plain-English
 * cause, a "Try this" remediation line, and the raw diagnostics folded into a
 * collapsible. Otherwise it falls back to a code→sentence map (so even an
 * unclassified failure reads as English) and finally to the raw message/code —
 * never a blank panel.
 */
export function ErrorBanner({
  message,
  source,
  code,
  title,
  cause,
  remediation,
}: ErrorBannerProps) {
  // Headline: the friendly title, else a plain-English sentence for a known
  // code, else a generic fallback. Never the raw enum.
  const headline = title || FAILURE_CODE_DESCRIPTIONS[code] || "Something went wrong";
  // Body under the headline: the classifier's cause, else the raw message.
  // Suppressed when it would merely echo the headline (e.g. a known code with
  // an empty message), so the panel never shows the same sentence twice or a
  // bare code string.
  const bodyText = cause || message || "";
  const explanation = bodyText && bodyText !== headline ? bodyText : null;
  // With a friendly title, the raw message is the detailed diagnostics blob —
  // keep it available but folded away. Without one, the message is already the
  // explanation, so there's nothing extra to fold.
  const details = title && message && message !== explanation ? message : null;

  return (
    <Alert
      variant="destructive"
      className="min-w-0 max-w-full overflow-hidden has-[>svg]:grid-cols-[auto_minmax(0,1fr)]"
    >
      <AlertCircleIcon />
      <AlertTitle className="min-w-0 break-words [overflow-wrap:anywhere]">
        {headline}
        {source ? ` · ${source}` : ""}
      </AlertTitle>
      <AlertDescription className="min-w-0 max-w-full space-y-2 overflow-hidden">
        {explanation ? (
          <span className="block max-w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere] [text-wrap:wrap]">
            {explanation}
          </span>
        ) : null}
        {remediation ? (
          <span className="block max-w-full break-words font-medium [overflow-wrap:anywhere]">
            Try this: {remediation}
          </span>
        ) : null}
        {details ? (
          <Collapsible>
            <CollapsibleTrigger className="group flex items-center gap-1 text-xs opacity-80 hover:opacity-100">
              <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]:rotate-90" />
              Details{code ? ` · ${code}` : ""}
            </CollapsibleTrigger>
            <CollapsibleContent>
              <span className="mt-1 block max-w-full whitespace-pre-wrap break-words font-mono text-xs opacity-80 [overflow-wrap:anywhere] [text-wrap:wrap]">
                {details}
              </span>
            </CollapsibleContent>
          </Collapsible>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

interface PolicyDeniedBannerProps {
  reason: string;
  phase: string;
}

/**
 * Warning banner for policy denials. Uses the `default` alert variant
 * (amber/warning tone) to distinguish from hard errors (destructive red).
 */
export function PolicyDeniedBanner({ reason, phase }: PolicyDeniedBannerProps) {
  return (
    <Alert>
      <ShieldXIcon />
      <AlertTitle>Blocked by policy{phase ? ` · ${phase}` : ""}</AlertTitle>
      <AlertDescription>{reason}</AlertDescription>
    </Alert>
  );
}

interface RetryIndicatorProps {
  source: string;
  attempt: number;
  maxAttempts: number;
  delaySeconds: number;
}

/**
 * Compact line that signals "we hit a transient failure and the server
 * is going to retry." No banner; reads more like a log line.
 */
export function RetryIndicator({
  source,
  attempt,
  maxAttempts,
  delaySeconds,
}: RetryIndicatorProps) {
  return (
    <div className="flex items-center gap-2 text-muted-foreground text-xs">
      <RotateCcwIcon className="size-3" />
      <span>
        Retrying {source} · attempt {attempt}/{maxAttempts}
        {delaySeconds > 0 ? ` · waiting ${delaySeconds.toFixed(1)}s` : ""}
      </span>
    </div>
  );
}

/**
 * Subtle inline marker that the conversation was compacted (older
 * history was summarized to fit context). The in-progress state is
 * rendered as a `Shimmer` in `ChatPage` to match the "Working…"
 * indicator.
 */
export function CompactionMarker() {
  return (
    <div className="flex items-center gap-2 text-muted-foreground text-xs italic">
      <ShrinkIcon className="size-3" />
      <span>Conversation compacted</span>
    </div>
  );
}

interface RoutingDecisionChipProps {
  model: string;
  applied: boolean;
  rationale: string;
}

/**
 * Muted inline chip announcing the intelligent model router's pick at
 * the start of a turn.
 */
export function RoutingDecisionChip({ model, applied, rationale }: RoutingDecisionChipProps) {
  const short = shortModelName(model);
  const lead = applied ? short : `would have picked ${short}`;
  const summary = `Intelligent model router · ${lead}`;
  return (
    <div
      className="my-1 flex flex-col items-center gap-0.5 text-muted-foreground text-xs"
      data-testid="routing-decision-chip"
      data-applied={applied ? "true" : "false"}
      title={rationale || summary}
    >
      <span className="flex items-center gap-1.5">
        <BrainCircuitIcon className="size-3 shrink-0" />
        <span>
          Intelligent model router{" · "}
          {!applied && <span>would have picked </span>}
          <span className="font-medium text-foreground">{short}</span>
        </span>
      </span>
      {rationale ? <span className="text-muted-foreground/70">{rationale}</span> : null}
    </div>
  );
}

interface RoutingDecisionCardProps {
  model: string;
  applied: boolean;
  rationale: string;
  /** Sub-agent name when this card is shown in the parent session. */
  agent?: string;
}

/**
 * Collapsible card announcing the intelligent model router's session-level
 * pick. Mirrors the SmartRoutingCard style: same container, model pill,
 * rationale, and expandable raw verdict JSON.
 *
 * Shown in place of the muted chip when auto-routing fires at turn start
 * because the agent spec has no explicit model. When `agent` is provided
 * the card is being shown in the parent (orchestrator) session for a child
 * session's routing decision — the agent name is shown as the row label.
 */
export function RoutingDecisionCard({
  model,
  applied,
  rationale,
  agent,
}: RoutingDecisionCardProps) {
  const short = shortModelName(model);
  const rowLabel = agent && agent.length > 0 ? agent : "Session";
  const prettyOutput = useMemo(
    () => JSON.stringify({ model, applied, rationale, ...(agent ? { agent } : {}) }, null, 2),
    [model, applied, rationale, agent],
  );
  return (
    <Collapsible
      defaultOpen={false}
      className={cn(
        "group not-prose my-1 flex flex-col gap-1.5 rounded-md border border-border bg-muted/30 px-3 py-2",
        TOOL_SURFACE_WIDTH_CLASS,
      )}
      data-testid="routing-decision-card"
      data-applied={applied ? "true" : "false"}
    >
      <div className="flex items-center gap-1.5 text-xs">
        <BrainCircuitIcon className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-medium">Intelligent routing</span>
        <span className="text-muted-foreground">{applied ? "· applied" : "· advisory"}</span>
        <CollapsibleTrigger
          className="ml-auto cursor-pointer rounded p-0.5 text-muted-foreground hover:text-foreground"
          aria-label="Show raw routing verdict"
          data-testid="routing-decision-raw-toggle"
        >
          <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]:rotate-90" />
        </CollapsibleTrigger>
      </div>
      <div className="flex items-center gap-2 text-xs">
        <span className="min-w-0 truncate font-mono text-foreground">{rowLabel}</span>
        <span className="ml-auto shrink-0 inline-flex items-center whitespace-nowrap rounded-full border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium leading-none text-foreground">
          {short}
        </span>
      </div>
      {rationale.length > 0 && (
        <p className="text-xs leading-snug text-muted-foreground">{rationale}</p>
      )}
      <CollapsibleContent className="data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=open]:animate-in">
        <CodeBlock code={prettyOutput} language="json">
          <CodeBlockHeader>
            <CodeBlockTitle className="min-w-0">
              <span className="truncate font-medium uppercase tracking-wide">Verdict</span>
            </CodeBlockTitle>
          </CodeBlockHeader>
        </CodeBlock>
      </CollapsibleContent>
    </Collapsible>
  );
}
