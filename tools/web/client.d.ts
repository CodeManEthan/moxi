// The types of the UI module's shared script, /m/ui/client.js, which every page imports
// by that absolute URL and every build leaves external. tsconfig.base.json maps the URL
// to this file, since TypeScript reads a specifier that starts with "/" as a path and
// takes no ambient `declare module` for it. The UI module's build writes this file from
// its client.ts; until that build exists, this hand-written copy states the same surface.

/** The component contract version this script speaks. */
export declare const CONTRACT: number;

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

export declare function installed(module: string): boolean;
export declare function components(module: string): ComponentInfo[];
export declare function place(host: Element, module: string, name: string,
                              attrs?: Record<string, string>): Promise<Element | null>;
export declare function api(module: string, path: string,
                            body?: object): Promise<{ status: number; data: any }>;
export declare function subscribe(module: string, topic: string,
                                  fn: (data: any) => void): () => void;
export declare function navigate(module: string, route?: string): void;
