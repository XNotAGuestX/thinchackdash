// SourceProvider interface. Shared code depends only on this file and src/target.ts.

export interface SourceAsset {
  sourceId: string;
  kind: "dashboard" | "monitor";
  title: string;
  raw: unknown;
  body: Record<string, unknown>;
}
