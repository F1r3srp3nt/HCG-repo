/**
 * STAGING ONLY. Runs HCG Store's own server code, unchanged, from a Store checkout (HCG_STORE_DIR), in its
 * TEST mode with its mock HCG Connect provider and TEST entitlement ledger, so HCG Repo can be tested end to
 * end against the real publishing, access and Sileo payment-provider code paths. Never a deployment.
 *
 *   HCG_STORE_DIR=<store>/web-hcg-store  <store>/web-hcg-store/node_modules/.bin/tsx store-staging-server.mts <info.json>
 *
 * The catalogue is a temporary copy of the Store's real catalogue/products.json in which the HCG Repo
 * products get central IDs prefixed "staging-only.": real products have none yet (HCG has not issued them),
 * and without one no entitlement, ownership or public approval is possible. Nothing is written back.
 *
 * Control API (127.0.0.1), standing in for HCG decisions and for people in a browser:
 *   POST /grant {sub, central, edition}   POST /revoke-all {sub}   POST /token {sub}  (desktop access token)
 *   POST /next-user {sub}                 the person who completes the next HCG Connect sign-in
 *   POST /approve {releaseId, eligibility, confirm, approver}   a different person approves in a browser
 */
import { copyFileSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";

const STORE = process.env.HCG_STORE_DIR;
if (!STORE) throw new Error("HCG_STORE_DIR must point at <store checkout>/web-hcg-store");
const src = (p: string) => import(join(STORE, p));
const { createStoreApp } = await src("server/src/app.ts");
const { parseStoreConfig } = await src("server/src/config.ts");
const { openDatabase } = await src("server/src/db.ts");
const { TestLedgerAuthority } = await src("server/src/entitlements.ts");
const { HcgConnectClient } = await src("server/src/hcg-connect.ts");
const { loadRegistry } = await src("server/src/registry.ts");
const { Client } = await src("server/test/harness.ts");
const { MockHcgConnect } = await src("server/test/mock-hcg-connect.ts");

export const STAGING_CENTRAL: Record<string, string> = { "hcg-nexus": "staging-only.hcg-nexus", "hcg-terminal": "staging-only.hcg-terminal" };
const infoFile = process.argv[2] ?? join(tmpdir(), "hcg-store-staging.json");
const MAKER_CLIENT = "99999999-8888-4777-8666-555555555555";
const idp = new MockHcgConnect();
idp.otherClients.add(MAKER_CLIENT);
await idp.start();

const cat = mkdtempSync(join(tmpdir(), "hcg-store-staging-catalogue-"));
const products = JSON.parse(readFileSync(join(STORE, "catalogue/products.json"), "utf8"));
for (const p of products.products) if (STAGING_CENTRAL[p.id]) p.registry.centralId = STAGING_CENTRAL[p.id];
writeFileSync(join(cat, "products.json"), JSON.stringify(products, null, 2));
copyFileSync(join(STORE, "catalogue/releases.json"), join(cat, "releases.json"));
const storage = mkdtempSync(join(tmpdir(), "hcg-store-staging-private-"));
writeFileSync(join(cat, "offers.json"), JSON.stringify({ offers: [] }));
writeFileSync(join(cat, "artifacts.json"), JSON.stringify({ artifacts: [] }));

const server = createServer();
await new Promise<void>((ok) => server.listen(0, "127.0.0.1", ok));
const origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
const cfg = parseStoreConfig({
  HCG_STORE_MODE: "test",
  HCG_STORE_PUBLIC_ORIGIN: origin,
  HCG_CONNECT_ISSUER: idp.issuer,
  HCG_CONNECT_AUTHORIZATION_ENDPOINT: `${idp.issuer}/oauth/authorize`,
  HCG_CONNECT_TOKEN_ENDPOINT: `${idp.issuer}/oauth/token`,
  HCG_CONNECT_JWKS_URI: `${idp.issuer}/.well-known/jwks.json`,
  HCG_CONNECT_CLIENT_ID: idp.clientId,
  HCG_CONNECT_REDIRECT_URI: `${origin}/auth/callback`,
  HCG_STORE_ENTITLEMENT_AUTHORITY: "test-ledger",
  HCG_STORE_PAYMENT_PROVIDER: "test",
  HCG_STORE_PROTECTED_DOWNLOADS: "enabled",
  HCG_STORE_ARTIFACT_STORAGE_DIR: storage,
  HCG_STORE_STATIC_DIR: "",
  HCG_CATALOGUE_DIR: cat,
  HCG_STORE_OFFERS_FILE: join(cat, "offers.json"),
  HCG_STORE_ARTIFACTS_FILE: join(cat, "artifacts.json"),
  HCG_STORE_PUBLISHING: "enabled",
  HCG_STORE_PUBLISHING_CLIENT_IDS: MAKER_CLIENT,
  HCG_STORE_PUBLISHER_CENTRAL_ID: "hcg.store.publish",
  HCG_STORE_RELEASE_APPROVER_CENTRAL_ID: "hcg.store.approve",
});
const registry = loadRegistry({ catalogueDir: cfg.catalogueDir, offersFile: cfg.offersFile, artifactsFile: cfg.artifactsFile, testMode: true });
const db = openDatabase(":memory:");
const now = () => Date.now();
const authority = new TestLedgerAuthority(db, now);
const app = createStoreApp({ cfg, db, registry, connect: new HcgConnectClient(cfg.hcgConnect!, fetch, now), authority, now });
server.on("request", (req, res) => void app.handle(req, res));

async function browserSignIn(sub: string) {
  const c = new Client(origin);
  idp.nextUser = { sub, email: `${sub}@example.test`, name: sub };
  const start = await c.get("/auth/login?return_to=/");
  const authz = await fetch(start.headers.get("location")!, { redirect: "manual" });
  await c.get(authz.headers.get("location")!.replace(origin, ""));
  return c;
}

const control = createServer(async (req, res) => {
  let raw = "";
  for await (const c of req) raw += c;
  const b = raw ? (JSON.parse(raw) as Record<string, string>) : {};
  const out = (status: number, body: unknown) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(body)); };
  try {
    if (req.url === "/grant") { authority.grant(b.sub!, b.central!, b.edition ?? "*", "staging", null); return out(200, { ok: true }); }
    if (req.url === "/revoke-all") { authority.revokeAll(b.sub!); return out(200, { ok: true }); }
    if (req.url === "/token") return out(200, { token: await idp.accessToken({ sub: b.sub!, email: `${b.sub}@example.test` }, MAKER_CLIENT) });
    if (req.url === "/next-user") { idp.nextUser = { sub: b.sub!, email: `${b.sub}@example.test`, name: b.sub! }; return out(200, { ok: true }); }
    if (req.url === "/approve") {
      const c = await browserSignIn(b.approver!);
      await c.get(`/publishing/releases/${b.releaseId}`);
      const r = await c.post(`/api/v1/publishing/releases/${b.releaseId}/approve`, { eligibility: b.eligibility, confirm: b.confirm });
      return out(r.status, await r.json());
    }
    out(404, {});
  } catch (e) {
    out(500, { error: String(e) });
  }
});
await new Promise<void>((ok) => control.listen(0, "127.0.0.1", ok));
writeFileSync(infoFile, JSON.stringify({
  storeOrigin: origin, issuer: idp.issuer, makerClientId: MAKER_CLIENT, staging: true, centralIds: STAGING_CENTRAL,
  control: `http://127.0.0.1:${(control.address() as { port: number }).port}`,
}, null, 2));
console.log(`STAGING HCG Store (TEST mode, real Store code from ${STORE}) on ${origin}`);
