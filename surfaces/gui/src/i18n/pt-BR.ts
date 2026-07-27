// Português (Brasil).
//
// Translated for the product, not word-for-word: "connector" stays "conector", but
// provider/vendor terms that appear in their own UIs (GitHub App, client ID, redirect URI,
// scopes) are left in English, because that is what the user will be reading on the
// provider's own screen while following these instructions. Translating them would make
// the walkthrough harder to follow, not easier.
//
// A key absent here falls back to English rather than rendering blank.

import type { Catalog } from "./index";

export const ptBR: Catalog = {
  // -- comum ----------------------------------------------------------------------
  "common.cancel": "Cancelar",
  "common.save": "Salvar",
  "common.remove": "Remover",
  "common.change": "Alterar",
  "common.retry": "Tentar de novo",
  "common.close": "Fechar",
  "common.connect": "Conectar",
  "common.disconnect": "Desconectar",
  "common.loading": "Carregando…",
  "common.checking": "Verificando…",
  "common.checkBrowser": "Confira o navegador…",
  "common.saving": "Salvando…",
  "common.optional": "opcional",

  // -- ajustes ▸ idioma -------------------------------------------------------------
  "settings.language.title": "Idioma",
  "settings.language.help":
    "Idioma da interface. As respostas do modelo seguem o idioma em que você escreve.",

  // -- conectores ▸ app OAuth próprio -------------------------------------------------
  "byo.tab": "Seu próprio app",
  "byo.intro":
    "Use seu próprio {kind} no lugar do da OpenWorker. Mesma autorização em um clique, sem login na nuvem, e o agente age como o seu app.",
  "byo.kind.githubApp": "GitHub App",
  "byo.kind.oauthApp": "app OAuth",
  "byo.register": "Registre um em {where}. Você vai receber {yields}.",
  "byo.redirect":
    "Defina o redirect URI como {uri} — o provedor recusa qualquer valor que não seja exatamente igual.",
  "byo.appId": "App ID",
  "byo.privateKey": "Chave privada (conteúdo do .pem)",
  "byo.clientId": "Client ID",
  "byo.clientSecret": "Client secret",
  "byo.clientSecretKeep": "•••••• (deixe em branco para manter)",
  "byo.scopes": "Scopes ({optional})",
  "byo.scopesPlaceholder": "deixe em branco para os padrões que este conector precisa",
  "byo.saveApp": "Salvar app",
  "byo.yourApp": "Seu app",
  "byo.connectTitle": "Conectar {title}",
  "byo.githubInstallHint":
    "Abre a página de instalação do seu App — escolha a conta e os repositórios.",
  "byo.loadFailed": "Não foi possível ler a configuração atual.",
  "byo.noPath": "{title} não tem caminho para app próprio.",
  "byo.errors.save": "não foi possível salvar essas credenciais",
  "byo.errors.connect": "não foi possível iniciar a conexão",
  "byo.errors.remove": "não foi possível remover esse app",

  // -- modos de conexão ----------------------------------------------------------------
  "connect.pane.one": "Um clique",
  "connect.pane.manual": "Manual",

  // -- contagens -------------------------------------------------------------------
  // `zero` é explícito de propósito: CLDR classifica 0 como `one` em pt-BR, então sem
  // isso sairia "0 repositório". A forma usada de fato no Brasil é o plural.
  "count.repositories": {
    zero: "nenhum repositório",
    one: "{count} repositório",
    other: "{count} repositórios",
  },
  "count.sessions": {
    zero: "nenhuma sessão",
    one: "{count} sessão",
    other: "{count} sessões",
  },
  "count.models": {
    zero: "nenhum modelo",
    one: "{count} modelo",
    other: "{count} modelos",
  },
};
