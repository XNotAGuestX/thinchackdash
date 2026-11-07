// Datadog source provider. Skeleton: every widget and monitor is blocked until
// plans 04 (dashboards) and 06 (monitors) replace compile().
import { InputError } from "../../input.js";
import type { CompileResult, Diagnostic, ItemResult } from "../../target.js";
import type { SourceProvider } from "../provider.js";
import { recognizeAssets } from "./assets.js";
import { parseMapping } from "./mapping.js";

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export const datadogProvider: SourceProvider = {
  id: "datadog",

  load(files) {
    return recognizeAssets(files);
  },

  compile(assets, mapping, _options): CompileResult {
    const parsed = parseMapping(mapping);
    if (!parsed.ok) throw new InputError("invalid mapping: " + parsed.errors.join("; "));

    const items: ItemResult[] = [];
    const diagnostics: Diagnostic[] = [];

    for (const asset of assets) {
      if (asset.kind === "dashboard") {
        items.push({ sourceId: asset.sourceId, sourcePath: "", kind: "dashboard", title: asset.title, outcome: "blocked" });
        const widgets = Array.isArray(asset.body.widgets) ? asset.body.widgets : [];
        widgets.forEach((widget: unknown, i: number) => {
          const definition = (widget as { definition?: Record<string, unknown> } | null)?.definition ?? {};
          const sourcePath = `widgets[${i}]`;
          const type = str(definition.type);
          items.push({ sourceId: asset.sourceId, sourcePath, kind: "widget", title: str(definition.title), outcome: "blocked" });
          diagnostics.push({
            code: "UNSUPPORTED_WIDGET",
            severity: "error",
            sourceId: asset.sourceId,
            sourcePath,
            message: `Widget type '${type}' is not supported yet.`,
            suggestedAction: "Recreate this widget manually in the target dashboard.",
          });
        });
      } else {
        items.push({ sourceId: asset.sourceId, sourcePath: "", kind: "monitor", title: asset.title, outcome: "blocked" });
        diagnostics.push({
          code: "UNSUPPORTED_MONITOR_TYPE",
          severity: "error",
          sourceId: asset.sourceId,
          sourcePath: "",
          message: "Monitor conversion is not supported yet.",
          suggestedAction: "Recreate this monitor manually as a Dash0 check rule.",
        });
      }
    }

    return { dashboards: [], rules: [], items, diagnostics };
  },
};
