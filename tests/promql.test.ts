import { describe, expect, it } from "vitest";
import { escapeLabelValue, renderSelector } from "../src/promql.js";

describe("promql", () => {
  it("escapes label values", () => {
    expect(escapeLabelValue('a"b\\c\nd')).toBe('a\\"b\\\\c\\nd');
  });
  it("renders the Dash0 selector", () => {
    expect(renderSelector({ name: "postgresql.backends", type: "sum" }, [{ label: "postgresql_database_name", value: "astronomy_db" }]))
      .toBe('{otel_metric_name="postgresql.backends", otel_metric_type="sum", postgresql_database_name="astronomy_db"}');
  });
});
