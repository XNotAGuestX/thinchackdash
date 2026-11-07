// Recognize, unwrap and identify Datadog assets. No translation here.
import { InputError, type InputFile } from "../../input.js";
import type { Diagnostic } from "../../target.js";
import type { SourceAsset } from "../provider.js";

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function isMonitorBody(v: unknown): v is Obj {
  return isObj(v) && typeof v.query === "string" && typeof v.type === "string";
}

export function recognizeAssets(files: InputFile[]): { assets: SourceAsset[]; diagnostics: Diagnostic[] } {
  const assets: SourceAsset[] = [];
  const diagnostics: Diagnostic[] = [];

  for (const f of files) {
    let raw: unknown;
    try {
      raw = JSON.parse(f.text);
    } catch (err) {
      throw new InputError(`INVALID_JSON: ${f.sourceId}: ${(err as Error).message}`);
    }

    if (isObj(raw) && Array.isArray(raw.widgets) && typeof raw.layout_type === "string") {
      assets.push({ sourceId: f.sourceId, kind: "dashboard", title: str(raw.title), raw, body: raw });
    } else if (isObj(raw) && isMonitorBody(raw.definition)) {
      const body = raw.definition;
      assets.push({ sourceId: f.sourceId, kind: "monitor", title: str(body.name), raw, body });
    } else if (isMonitorBody(raw) && isObj(raw.options)) {
      assets.push({ sourceId: f.sourceId, kind: "monitor", title: str(raw.name), raw, body: raw });
    } else {
      diagnostics.push({
        code: "UNSUPPORTED_ASSET_KIND",
        severity: "warning",
        sourceId: f.sourceId,
        sourcePath: "",
        message: "File is not a recognized Datadog dashboard or monitor; skipped.",
        suggestedAction: "Remove the file from the input, or export it as a Datadog dashboard or monitor JSON.",
      });
    }
  }

  return { assets, diagnostics };
}
