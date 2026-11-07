// SourceProvider interface and the static provider table. Shared code depends
// only on this file and src/target.ts. This is not a plugin framework: no
// dynamic loading, discovery or config-driven registration.
import { InputError, type InputFile } from "../input.js";
import type { CompileResult, Diagnostic } from "../target.js";
import { datadogProvider } from "./datadog/index.js";

export interface SourceAsset {
  sourceId: string;
  kind: "dashboard" | "monitor";
  title: string;
  raw: unknown;
  body: Record<string, unknown>;
}

export interface CompileOptions {
  project: string;
}

export interface ProviderLoadResult {
  assets: SourceAsset[];
  diagnostics: Diagnostic[];
}

export interface SourceProvider {
  readonly id: string;
  load(files: InputFile[]): ProviderLoadResult;
  compile(assets: SourceAsset[], mapping: unknown, options: CompileOptions): CompileResult;
}

export const providers: Readonly<Record<string, SourceProvider>> = Object.freeze({
  datadog: datadogProvider,
});

export function getProvider(id: string): SourceProvider {
  const provider = Object.hasOwn(providers, id) ? providers[id] : undefined;
  if (!provider) {
    throw new InputError(`unknown --source '${id}'; known: ${Object.keys(providers).sort().join(", ")}`);
  }
  return provider;
}
