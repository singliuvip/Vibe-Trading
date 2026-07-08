// Type declarations for echarts v6 subpath exports.
// echarts v6 ships only echarts.d.cts (CJS) but its exports map
// omits "types" entries for subpath imports like "echarts/core".
// These supplemental declarations silence TS7016 and provide basic types.
// IMPORTANT: code uses `import * as echarts from "echarts/core"` (namespace import),
// so the module value itself must provide `init` / `use` / `connect`.

declare module "echarts/core" {
  export function init(
    dom: HTMLElement | null,
    theme?: unknown,
    opts?: unknown
  ): any;
  export function use(modules: readonly unknown[]): void;
  export function connect(group: string | unknown): void;
}

declare module "echarts/charts" {
  export const CandlestickChart: unknown;
  export const LineChart: unknown;
  export const BarChart: unknown;
  export const HeatmapChart: unknown;
}

declare module "echarts/components" {
  export const GridComponent: unknown;
  export const TooltipComponent: unknown;
  export const LegendComponent: unknown;
  export const DataZoomComponent: unknown;
  export const MarkPointComponent: unknown;
  export const ToolboxComponent: unknown;
  export const MarkLineComponent: unknown;
  export const MarkAreaComponent: unknown;
  export const VisualMapComponent: unknown;
}

declare module "echarts/renderers" {
  export const CanvasRenderer: unknown;
}
