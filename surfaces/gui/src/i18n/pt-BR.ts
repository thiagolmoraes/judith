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

  // -- tela de ajustes ------------------------------------------------
  "settings.general.title": "Geral",
  "settings.general.sub": "Como o OpenWorker aparece e se comporta neste computador.",
  "settings.theme": "Tema",
  "settings.theme.light": "Claro",
  "settings.theme.dark": "Escuro",
  "settings.theme.auto": "Automático",
  "settings.theme.help": "O automático segue a aparência do seu Mac.",
  "settings.alwaysOn": "Sempre ativo",
  "settings.openAtLogin": "Abrir ao entrar",
  "settings.openAtLogin.help": "Abre o OpenWorker automaticamente quando você faz login.",
  "settings.keepAwake": "Manter este sistema acordado",
  "settings.keepAwake.help": "Impede a suspensão por inatividade para que as tarefas agendadas rodem na hora.",
  "settings.sidebar": "Barra lateral",
  "settings.sessionsPeek": "Conversas exibidas por coworker",
  "settings.trustedWorkspaces": "Pastas confiáveis",
  "settings.trustedWorkspaces.empty": "Nenhuma pasta confiável.",
  "settings.setupUpdates": "Configuração e atualizações",
  "settings.runSetupAgain.help": "Repete a configuração inicial: modelo, primeira automação, dicas.",
  "settings.personas.galleryHelp": "Coworkers selecionados pela equipe do OpenWorker — veja o que cada um faz antes de instalar.",
  "settings.runSetupAgain": "Refazer a configuração",
  "settings.trustedWorkspaces.help": "Projetos confiáveis podem definir seus comandos permitidos em .coworker/config.toml.",
  "settings.tokenSavings": "Economia de tokens",
  "settings.maxPages": "Máx. de páginas",
  "settings.maxSize": "Tamanho máx.",
  "settings.models.sub": "Provedores e os modelos oferecidos no seletor do compositor. As chaves ficam apenas neste computador.",
  "settings.voice.desktopOnly": "A configuração de entrada por voz está disponível no aplicativo desktop do OpenWorker.",
  "settings.voice.private": "Privado por padrão.",
  "settings.voice.privateDetail": "O áudio fica só na memória enquanto você grava e é transcrito localmente.",
  "settings.voice.thisDevice": "Este dispositivo",
  "settings.voice.memory": "Memória",
  "settings.voice.processor": "Processador",
  "settings.voice.verified": "Verificado",
  "settings.voice.repair": "Reparar",
  "settings.voice.verifying": "Verificando…",
  "settings.voice.downloadModel": "Baixar modelo",
  "settings.voice.micTest": "Teste de microfone",
  "settings.personas.browse": "Explorar a galeria de personas",
  "common.delete": "Excluir",
  "common.open": "Abrir →",
  "nav.settings": "Ajustes",
  "nav.models": "Modelos",
  "nav.voice": "Entrada por voz",
  "nav.personas": "Personas",

  // -- tool-call one-liners (humanize.ts) -----------------------------------------
  // Assembled as pre + object + post so the UI can bold the object. Verb-object order
  // holds in Portuguese, so the shape survives translation unchanged; only the
  // fragments move.
  "tool.readFile": "Leu ",
  "tool.writeFile": "Escreveu ",
  "tool.editFile": "Editou ",
  "tool.grep": "Procurou no código por ",
  "tool.gitLog": "Consultou o histórico recente do git",
  "tool.shellTaskOutput": "Verificou um comando em segundo plano",
  "tool.shellTaskKill": "Parou um comando em segundo plano",
  "tool.updatedPlan": "Atualizou o plano — ",
  "tool.updatedPlanItems": "Atualizou o plano — {count} itens",
  "tool.sentMessage": "Enviou uma mensagem",
  "tool.sentPlatformMessageTo": "Enviou uma mensagem no {platform} para ",
  "tool.readWebPage": "Leu uma página — ",
  "tool.searchedWeb": "Pesquisou na web — ",
  "tool.subagent": "Enviou um subagente para explorar — ",
  "tool.askedQuestion": "Fez uma pergunta a você",
  "tool.proposedPlan": "Propôs um plano",
  "tool.askedFolderAccess": "Pediu acesso a uma pasta — ",
  "tool.used": "Usou {name}",
  "tool.onPlatform": " no {platform}",
  // Pending (approval) forms — infinitive in Portuguese, matching the imperative English.
  "tool.pending.run": "Executar um comando",
  "tool.pending.edit": "Editar ",
  "tool.pending.write": "Escrever ",
  "tool.pending.sendMessage": "Enviar uma mensagem",
  "tool.pending.sendMessageTo": "Enviar uma mensagem para ",
  "tool.pending.sendFile": "Enviar um arquivo",
  "tool.pending.sendFileTo": "Enviar um arquivo para ",
  "tool.pending.createAutomation": "Criar uma automação",
  "tool.pending.createAutomationNamed": "Criar a automação ",
  "tool.pending.use": "Usar {name}",
  // Wanted (denied/expired) forms.
  "tool.wanted.run": "Queria executar ",
  "tool.wanted.edit": "Queria editar ",
  "tool.wanted.write": "Queria escrever ",
  "tool.wanted.sendMessage": "Queria enviar uma mensagem",
  "tool.wanted.message": "Queria enviar uma mensagem para ",
  "tool.wanted.use": "Queria usar {name}",
  "tool.ranCommand": "Executou ",
  "tool.startedBackground": "Iniciou em segundo plano: ",
  "todo.status.pending": "pendente",
  "todo.status.inProgress": "em andamento",
  "todo.status.completed": "concluído",
  "tool.aFile": "um arquivo",
  "tool.files": "arquivos",

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
