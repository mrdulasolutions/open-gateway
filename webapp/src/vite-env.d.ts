/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_OPENGATEWAY_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
