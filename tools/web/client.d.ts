// The types of the UI module's shared script, which every page imports by its absolute
// URL and every build leaves external. The UI module's build writes this file from its
// client.ts; until that build exists, this hand-written copy states the same surface.

declare module "/m/ui/client.js" {
  /** The component contract version this script speaks. */
  export const CONTRACT: number;

  /** One component a module declares, as the shell read it from the announcement. */
  export interface ComponentInfo {
    name: string;
    script: string;
    title: string;
    doc: string;
    contract: number;
    place: "page" | "block" | "inline";
    attributes?: Record<string, {
      type: "string" | "int" | "bool" | "enum";
      values?: string[];
      required?: boolean;
      doc?: string;
    }>;
    home?: boolean;
  }

  export function installed(module: string): boolean;
  export function components(module: string): ComponentInfo[];
  export function place(host: Element, module: string, name: string,
                        attrs?: Record<string, string>): Promise<Element | null>;
  export function api(module: string, path: string,
                      body?: object): Promise<{ status: number; data: any }>;
  export function subscribe(module: string, topic: string,
                            fn: (data: any) => void): () => void;
  export function navigate(module: string, route?: string): void;
}
