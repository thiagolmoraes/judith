// English — the source language. Keys are `area.thing`, and the values here must match
// the strings as they read in the JSX, since this is also the fallback for every other
// catalogue.
//
// This file grows per translated area rather than all at once; the settings surface and
// the connector panes come first because they're where a new user spends their setup.

import type { Catalog } from "./index";

export const en: Catalog = {
  // -- common -------------------------------------------------------------------
  "common.cancel": "Cancel",
  "common.save": "Save",
  "common.remove": "Remove",
  "common.change": "Change",
  "common.retry": "Retry",
  "common.close": "Close",
  "common.connect": "Connect",
  "common.disconnect": "Disconnect",
  "common.loading": "Loading…",
  "common.checking": "Checking…",
  "common.checkBrowser": "Check your browser…",
  "common.saving": "Saving…",
  "common.optional": "optional",

  // -- settings ▸ language --------------------------------------------------------
  "settings.language.title": "Language",
  "settings.language.help":
    "Interface language. Model replies follow the language you write in.",

  // -- connectors ▸ bring-your-own OAuth ------------------------------------------
  "byo.tab": "Your own app",
  "byo.intro":
    "Use your own {kind} instead of OpenWorker's. Same click-and-approve, no cloud sign-in, and the agent acts as your app.",
  "byo.kind.githubApp": "GitHub App",
  "byo.kind.oauthApp": "OAuth app",
  "byo.register": "Register one at {where}. You'll get {yields}.",
  "byo.redirect":
    "Set the redirect URI to {uri} — the provider rejects anything that doesn't match exactly.",
  "byo.appId": "App ID",
  "byo.privateKey": "Private key (.pem contents)",
  "byo.clientId": "Client ID",
  "byo.clientSecret": "Client secret",
  "byo.clientSecretKeep": "•••••• (leave blank to keep)",
  "byo.scopes": "Scopes ({optional})",
  "byo.scopesPlaceholder": "leave blank for the defaults this connector needs",
  "byo.saveApp": "Save app",
  "byo.yourApp": "Your app",
  "byo.connectTitle": "Connect {title}",
  "byo.githubInstallHint":
    "Opens your App's install page — pick the account and repositories.",
  "byo.loadFailed": "Couldn't read the current setup.",
  "byo.noPath": "{title} has no bring-your-own app path.",
  "byo.errors.save": "could not save those credentials",
  "byo.errors.connect": "could not start the connect",
  "byo.errors.remove": "could not remove that app",

  // -- connect modes ---------------------------------------------------------------
  "connect.pane.one": "One click",
  "connect.pane.manual": "Manual",

  // -- counts ----------------------------------------------------------------------
  "count.repositories": { one: "{count} repository", other: "{count} repositories" },
  "count.sessions": { one: "{count} session", other: "{count} sessions" },
  "count.models": { one: "{count} model", other: "{count} models" },
};
