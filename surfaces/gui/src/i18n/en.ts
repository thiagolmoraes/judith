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

  // -- settings screen ------------------------------------------------
  "settings.general.title": "General",
  "settings.general.sub": "How OpenWorker looks and behaves on this machine.",
  "settings.theme": "Theme",
  "settings.theme.light": "Light",
  "settings.theme.dark": "Dark",
  "settings.theme.auto": "Auto",
  "settings.theme.help": "Auto follows your Mac’s appearance.",
  "settings.alwaysOn": "Always-on",
  "settings.openAtLogin": "Open at login",
  "settings.openAtLogin.help": "Launch OpenWorker automatically when you sign in.",
  "settings.keepAwake": "Keep this system awake",
  "settings.keepAwake.help": "Prevent idle sleep so scheduled tasks fire on time.",
  "settings.sidebar": "Sidebar",
  "settings.sessionsPeek": "Conversations shown per coworker",
  "settings.trustedWorkspaces": "Trusted workspaces",
  "settings.trustedWorkspaces.empty": "No workspaces are trusted.",
  "settings.setupUpdates": "Setup & updates",
  "settings.runSetupAgain.help": "Replays the first-run setup: model, first automation, tips.",
  "settings.tokenSavings": "Token savings",
  "settings.maxPages": "Max pages",
  "settings.maxSize": "Max size",
  "settings.models.sub":
    "Providers and the models offered in the composer's picker. Keys are stored only on this computer.",
  "settings.voice.desktopOnly": "Voice Input setup is available in the OpenWorker desktop app.",
  "settings.voice.private": "Private by design.",
  "settings.voice.privateDetail": "Audio is held in memory only while you record and is transcribed locally.",
  "settings.voice.thisDevice": "This device",
  "settings.voice.memory": "Memory",
  "settings.voice.processor": "Processor",
  "settings.voice.verified": "Verified",
  "settings.voice.repair": "Repair",
  "settings.voice.verifying": "Verifying…",
  "settings.voice.downloadModel": "Download model",
  "settings.voice.micTest": "Microphone test",
  "settings.personas.browse": "Browse the Persona Gallery",
  "common.delete": "Delete",
  "common.open": "Open →",
  "nav.settings": "Settings",
  "nav.models": "Models",
  "nav.voice": "Voice input",
  "nav.personas": "Personas",

  // -- tool-call one-liners (humanize.ts) -----------------------------------------
  // Assembled as pre + object + post so the UI can bold the object. Verb-object order
  // holds in Portuguese, so the shape survives translation unchanged; only the
  // fragments move.
  "tool.readFile": "Read ",
  "tool.writeFile": "Wrote ",
  "tool.editFile": "Edited ",
  "tool.grep": "Searched the code for ",
  "tool.gitLog": "Looked through recent git history",
  "tool.shellTaskOutput": "Checked on a background command",
  "tool.shellTaskKill": "Stopped a background command",
  "tool.updatedPlan": "Updated the plan — ",
  "tool.updatedPlanItems": "Updated the plan — {count} items",
  "tool.sentMessage": "Sent a message",
  "tool.sentPlatformMessageTo": "Sent a {platform} message to ",
  "tool.readWebPage": "Read a web page — ",
  "tool.searchedWeb": "Searched the web — ",
  "tool.subagent": "Sent a sub-agent to explore — ",
  "tool.askedQuestion": "Asked you a question",
  "tool.proposedPlan": "Proposed a plan",
  "tool.askedFolderAccess": "Asked for folder access — ",
  "tool.used": "Used {name}",
  "tool.onPlatform": " on {platform}",
  // Pending (approval) forms — infinitive in Portuguese, matching the imperative English.
  "tool.pending.run": "Run a command",
  "tool.pending.edit": "Edit ",
  "tool.pending.write": "Write ",
  "tool.pending.sendMessage": "Send a message",
  "tool.pending.sendMessageTo": "Send a message to ",
  "tool.pending.sendFile": "Send a file",
  "tool.pending.sendFileTo": "Send a file to ",
  "tool.pending.createAutomation": "Create an automation",
  "tool.pending.createAutomationNamed": "Create the automation ",
  "tool.pending.use": "Use {name}",
  // Wanted (denied/expired) forms.
  "tool.wanted.run": "Wanted to run ",
  "tool.wanted.edit": "Wanted to edit ",
  "tool.wanted.write": "Wanted to write ",
  "tool.wanted.sendMessage": "Wanted to send a message",
  "tool.wanted.message": "Wanted to message ",
  "tool.wanted.use": "Wanted to use {name}",
  "tool.aFile": "a file",
  "tool.files": "files",

  // -- counts ----------------------------------------------------------------------
  "count.repositories": { one: "{count} repository", other: "{count} repositories" },
  "count.sessions": { one: "{count} session", other: "{count} sessions" },
  "count.models": { one: "{count} model", other: "{count} models" },
};
