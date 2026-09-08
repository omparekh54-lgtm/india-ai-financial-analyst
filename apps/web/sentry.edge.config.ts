// This file configures the initialization of Sentry for edge features (middleware, edge routes, and so on).
// The config you add here will be used whenever one of the edge features is loaded.
// Note that this config is unrelated to the Vercel Edge Runtime and is also required when running locally.
// https://docs.sentry.io/platforms/javascript/guides/nextjs/

import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: "https://c0d23a263d73957eb5ca713ca210a219@o4512048401612800.ingest.de.sentry.io/4512048497688656",

  sendDefaultPii: false,
  tracesSampleRate: 0.05,
  dataCollection: { userInfo: false, httpBodies: [] },
});
