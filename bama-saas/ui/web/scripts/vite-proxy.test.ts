import { createServer as createHttpServer } from "node:http";
import type { AddressInfo } from "node:net";
import { createServer } from "vite";
import { expect, it } from "vitest";
import config from "../vite.config";

it("preserves the browser Host and Origin on session POSTs", async () => {
  const upstream = createHttpServer((request, response) => {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ host: request.headers.host, origin: request.headers.origin }));
  });
  await new Promise<void>((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const target = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`;
  const proxy = await createServer({
    configFile: false,
    server: {
      host: "127.0.0.1", port: 0, strictPort: true,
      proxy: { "/api": { ...config.server!.proxy!["/api"] as object, target } },
    },
  });
  try {
    await proxy.listen();
    const host = `127.0.0.1:${(proxy.httpServer!.address() as AddressInfo).port}`;
    const origin = `http://${host}`;
    const response = await fetch(`${origin}/api/alerts/mark-read/`, {
      method: "POST", headers: { Origin: origin },
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ host, origin });
  } finally {
    await proxy.close();
    await new Promise<void>((resolve, reject) => upstream.close((error) => error ? reject(error) : resolve()));
  }
});
