/**
 * TEST ONLY: the HCG Repo commercial acceptance environment (docs/COMMERCIAL_TEST.md).
 *
 * Runs HCG Store's REAL, unmodified server code (imported from a local checkout of F1r3srp3nt/store) in test mode:
 *   - HTTPS on 127.0.0.1 (Sileo and Zebra refuse non-HTTPS payment endpoints and download URLs), with a throw-away
 *     certificate the harness creates;
 *   - the Store's own HCG Connect protocol mock (OAuth 2.1 authorization code + PKCE S256, ES256 tokens). This is a
 *     protocol-level stand-in, NOT the real HCG Connect: results from it are never reported as a real sign-in;
 *   - the test entitlement ledger and the test payment provider (signed test webhooks). No real payment provider.
 * Test controls (next mock user, clock offset, outages, revocation, free windows) are on a SEPARATE loopback control
 * port, never on the Store's port.
 *
 * Usage: <store>/node_modules/.bin/tsx tools/commercial-test/store-test-server.mts <config.json>
 */
import { readFileSync } from "node:fs";
import { createServer as createHttp, type IncomingMessage, type ServerResponse } from "node:http";
import { createServer as createHttps } from "node:https";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

interface TestConfig {
  storeDir: string;          // <checkout>/web-hcg-store
  port: number;              // HTTPS Store port
  idpPort: number;           // mock HCG Connect port
  controlPort: number;       // loopback test controls
  certFile: string;
  keyFile: string;
  catalogueDir: string;
  offersFile: string;
  artifactsFile: string;
  storageDir: string;
  staticDir: string;         // a minimal storefront page for TEST (the real one is a separate build)
  repoOrigins: string;       // HCG Repo origins allowed for credentialed CORS
  webhookSecret: string;
  downloadTtlSeconds: number;
}

const conf = JSON.parse(readFileSync(process.argv[2]!, "utf8")) as TestConfig;
const imp = (p: string) => import(pathToFileURL(join(conf.storeDir, p)).href);
const { createStoreApp } = await imp("server/src/app.ts");
const { parseStoreConfig } = await imp("server/src/config.ts");
const { openDatabase } = await imp("server/src/db.ts");
const { TestLedgerAuthority } = await imp("server/src/entitlements.ts");
const { HcgConnectClient } = await imp("server/src/hcg-connect.ts");
const { loadRegistry } = await imp("server/src/registry.ts");
const { openWindow, terminateWindow } = await imp("server/src/free-windows.ts");
const { MockHcgConnect } = await imp("server/test/mock-hcg-connect.ts");

let offsetMs = 0;
const now = () => Date.now() + offsetMs;

const idp = new MockHcgConnect();
idp.now = now;
await idp.start(conf.idpPort);

const origin = `https://127.0.0.1:${conf.port}`;
const cfg = parseStoreConfig({
  HCG_STORE_MODE: "test",
  HCG_STORE_PUBLIC_ORIGIN: origin,
  HCG_STORE_REPO_ORIGINS: conf.repoOrigins,
  HCG_CONNECT_ISSUER: `${idp.origin}/auth/v1`,
  HCG_CONNECT_AUTHORIZATION_ENDPOINT: `${idp.origin}/auth/v1/oauth/authorize`,
  HCG_CONNECT_TOKEN_ENDPOINT: `${idp.origin}/auth/v1/oauth/token`,
  HCG_CONNECT_JWKS_URI: `${idp.origin}/auth/v1/.well-known/jwks.json`,
  HCG_CONNECT_CLIENT_ID: idp.clientId,
  HCG_CONNECT_REDIRECT_URI: `${origin}/auth/callback`,
  HCG_STORE_ENTITLEMENT_AUTHORITY: "test-ledger",
  HCG_STORE_PAYMENT_PROVIDER: "test",
  HCG_STORE_TEST_PAYMENT_WEBHOOK_SECRET: conf.webhookSecret,
  HCG_STORE_PROTECTED_DOWNLOADS: "enabled",
  HCG_STORE_ARTIFACT_STORAGE_DIR: conf.storageDir,
  HCG_STORE_STATIC_DIR: conf.staticDir,
  HCG_CATALOGUE_DIR: conf.catalogueDir,
  HCG_STORE_OFFERS_FILE: conf.offersFile,
  HCG_STORE_ARTIFACTS_FILE: conf.artifactsFile,
  HCG_STORE_DOWNLOAD_URL_TTL_SECONDS: String(conf.downloadTtlSeconds),
});
const registry = loadRegistry({ catalogueDir: cfg.catalogueDir, offersFile: cfg.offersFile, artifactsFile: cfg.artifactsFile, testMode: true });
const db = openDatabase(":memory:");
const ledger = new TestLedgerAuthority(db, now);

// Authority outage switch: while set, every entitlement question is "unavailable" (never a grant).
let authorityDown = false;
const authority = new Proxy(ledger, {
  get(target, prop, receiver) {
    if (authorityDown && (prop === "check" || prop === "checkFeature" || prop === "list")) {
      return async () => ({ status: "unavailable", reason: "TEST: entitlement authority unreachable" });
    }
    const v = Reflect.get(target, prop, receiver);
    return typeof v === "function" ? v.bind(target) : v;
  },
});

const app = createStoreApp({ cfg, db, registry, connect: new HcgConnectClient(cfg.hcgConnect!), authority, now });
createHttps({ key: readFileSync(conf.keyFile), cert: readFileSync(conf.certFile) }, (req, res) => void app.handle(req, res))
  .listen(conf.port, "127.0.0.1");

async function body(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
}

function reply(res: ServerResponse, code: number, data: unknown) {
  res.writeHead(code, { "content-type": "application/json" }).end(JSON.stringify(data));
}

createHttp(async (req, res) => {
  try {
    const b = req.method === "POST" ? await body(req) : {};
    switch (req.url) {
      case "/next-user":
        idp.nextUser = { sub: String(b.sub), email: String(b.email), name: String(b.name ?? b.sub) };
        return reply(res, 200, { ok: true });
      case "/clock":
        offsetMs += Number(b.advanceSeconds ?? 0) * 1000;
        return reply(res, 200, { offsetMs });
      case "/idp":
        idp.down = !!b.down;
        return reply(res, 200, { down: idp.down });
      case "/authority":
        authorityDown = !!b.down;
        return reply(res, 200, { down: authorityDown });
      case "/revoke-all":
        ledger.revokeAll(String(b.sub));
        return reply(res, 200, { ok: true });
      case "/grant": {
        // Time-limited entitlement in the TEST ledger (temporary free testing / private beta / trial).
        const exp = b.seconds ? now() + Number(b.seconds) * 1000 : null;
        ledger.grant(String(b.sub), String(b.centralProductId), String(b.edition), String(b.source ?? "temporary-test"), exp);
        return reply(res, 200, { ok: true, expiresAt: exp });
      }
      case "/free-window": {
        const start = now();
        const w = openWindow(db, { productId: String(b.productId), editionId: (b.editionId as string) ?? null, releaseId: null,
          period: { anchor: "fixed", startsAt: start, endsAt: start + Number(b.durationSeconds) * 1000 },
          authorisedBy: "TEST owner", performedBy: "TEST harness" }, start);
        return reply(res, 200, w);
      }
      case "/free-window/end":
        return reply(res, 200, terminateWindow(db, String(b.id), "TEST ended", { authorisedBy: "TEST owner", performedBy: "TEST harness" }, now()));
      case "/status":
        return reply(res, 200, { origin, idp: idp.origin, offsetMs, authorityDown, idpDown: idp.down, mode: cfg.mode,
          payments: cfg.paymentProvider, downloads: cfg.protectedDownloads });
      default:
        return reply(res, 404, { error: "unknown control" });
    }
  } catch (e) {
    return reply(res, 500, { error: String(e) });
  }
}).listen(conf.controlPort, "127.0.0.1", () => {
  console.log(`READY ${origin} idp=${idp.origin} control=http://127.0.0.1:${conf.controlPort}`);
});
