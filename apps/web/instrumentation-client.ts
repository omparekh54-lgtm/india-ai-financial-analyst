// This file configures the initialization of Sentry on the client.
// The added config here will be used whenever a users loads a page in their browser.
// https://docs.sentry.io/platforms/javascript/guides/nextjs/

import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: "https://c0d23a263d73957eb5ca713ca210a219@o4512048401612800.ingest.de.sentry.io/4512048497688656",

  integrations: [Sentry.replayIntegration({ maskAllText: true, blockAllMedia: true })],
  sendDefaultPii: false,
  tracesSampleRate: 0.05,

  // Define how likely Replay events are sampled.
  // This sets the sample rate to be 10%. You may want this to be 100% while
  // in development and sample at a lower rate in production
  replaysSessionSampleRate: 0,

  // Define how likely Replay events are sampled when an error occurs.
  replaysOnErrorSampleRate: 0.1,

  dataCollection: { userInfo: false, httpBodies: [] },
});

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
