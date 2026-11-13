# xmllint-wasm 5.3.0

MIT; see COPYING (including libxml2 notices). Unmodified engine and WASM from the official npm artifact:
https://registry.npmjs.org/xmllint-wasm/-/xmllint-wasm-5.3.0.tgz
npm SHA-512 integrity verified before extracting: sEgRXdf2t13eYCFCURmCE6h2hwleEOIS9VL/jOh9JJJnez2YXIooi2Ghm05/JZEPXyPawN8sMASPVZlmh8Sd6A==

Runtime uses only the Module factory preceding the upstream initWorker adapter. The original file remains byte-identical; our validation-worker.js supplies in-memory WASM/files and replaces the upstream network-loading adapter. No CDN or runtime npm installation.

xmllint-browser.mjs SHA-256: 5f5a2c17be12d398c640f3b494e7ded8cd8c9b0175d2c65e6f197a28b77fd01e
xmllint.wasm SHA-256: 4e3cc21a67e8dd40ccb59822e4193fd1923b4cda08b40fe8ff8973de5eed9515
