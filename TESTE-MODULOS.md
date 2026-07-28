# Relatório de teste — módulo a módulo

**Data:** madrugada de 2026-07-28 · **Base:** `deploy/hml` @ `6689abf` (stack i18n #13–#17 mergeada)
**Como:** sidecar rodando do código-fonte em porta isolada (8991), state dir limpo, locale `pt-BR`, modelo real `qwen3-14b` no seu vLLM (`192.168.15.101:9001`). Nada foi testado contra seus dados reais.

## Resumo executivo

| | |
|---|---|
| Suites | Python **1047 ✓** · GUI **153 ✓** (`tsc` e build limpos) |
| Módulos exercitados ao vivo | 16 — todos os fluxos principais **funcionam** |
| Quebrado de verdade | **0** — o único item (testes do Sidebar) foi corrigido no PR #20 |
| Consertado durante a noite | 3 bugs reais achados pelos testes (seção "Consertado") |
| Pendências | itens 1–4 corrigidos (PRs #20 e #21); restam observações opcionais |

## O que funciona (testado ao vivo, não só suite)

| Módulo | Teste feito | Resultado |
|---|---|---|
| Autenticação da API | requisição sem token | ✅ 401; com token, 200 |
| Settings / locale | roundtrip `pt-BR`, locale inválido | ✅ persiste; `xx-XX` rejeitado |
| Providers | verify + save do vLLM, adicionar modelo, trocar default | ✅ tudo persiste (os fixes dos PRs #2–4 seguram) |
| **Chat E2E** | WebSocket → qwen3-14b → resposta | ✅ streaming de raciocínio + resposta `FUNCIONA` persistida |
| Sessões | listar, renomear, roots, unattended on/off, connections | ✅ |
| Artefatos | listar, ler, **path traversal** (`../../etc/passwd` e absoluto) | ✅ leitura ok; traversal bloqueado: `path escapes workspace` |
| **Automações E2E** | agendada (disparou sozinha, `status: ok`) **e** manual run dirigido por WS | ✅ ciclo completo |
| **Fluxo de aprovação** | `permission_required` → aprovar → `write_file` executa | ✅ inclusive: aprovação pendente sobrevive à desconexão, e input novo é rejeitado (`input_rejected`) enquanto há pendência — correto |
| Conectores | catálogo (40, blurbs em pt-BR), connect sem campos, BYO sem caminho, managed com cloud OFF | ✅ validações certas; cloud OFF recusa na rota |
| MCP | add/list/patch/tools/delete com servidor stdio falso | ✅ CRUD ok; servidor morto não derruba nada |
| Inbox / roteamento | binding (recusado sem Slack — correto), dm-route set/get, resolve de item fantasma | ✅ |
| Personas | 4 padrão, install com dir/git inválidos, enable | ✅ erros claros, nada quebra |
| Cloud (desligado) | login, gallery | ✅ recusados sem 500; login já responde em pt-BR |
| Audit | eventos após o run | ✅ registrou as ações do agente |
| Anexos | `inspect-pdf` com PDF corrompido | ✅ erro limpo, sem 500 |
| Browser | state/close sem browser aberto | ✅ |
| `/v1/chat/completions` | compat OpenAI com o vLLM | ✅ `choices[0] = "OK"` |
| Páginas loopback | `/oauth/callback?error=` | ✅ em pt-BR ("Falha na conexão"), HTTP 400 correto |

## 🔴 Para corrigir (amanhã)

1. ~~**`Sidebar.test.tsx`: 7 testes falham**~~ **Corrigido (PR #20).** O diagnóstico original ("mock do teste") estava errado: a causa era o `localStorage` global do próprio Node — ligado por padrão no Node 25, sem métodos sem `--localstorage-file` — sombreando o Storage do jsdom nos workers do vitest. Duas linhas de config (`--no-experimental-webstorage` no execArgv + URL não-opaca no jsdom) e a suíte fechou **153/153** pela primeira vez.

2. ~~**Strings de backend em inglês que a GUI pode exibir**~~ **Corrigido (PR #21)** — as cinco abaixo, mais três descobertas durante o fix (`workspace not connected`, a variante "cloud inacessível" da galeria e a mensagem de clone), passaram pelo catálogo:
   - `"connector not connected"` — `POST /v1/connectors/{name}/allow`
   - `"Slack is not connected."` — `POST /v1/inbox/routing/binding`
   - `"gallery requires cloud sign-in"` — `GET /v1/cloud/gallery` (a GalleryModal mostra copy própria, então prioridade baixa)
   - `"not a directory: …"` — install de persona
   - `"path escapes workspace"` — leitura de artefato

3. ~~**Erro cru de MCP vaza para o usuário**~~ **Corrigido (PR #21)** — `_mcp_error_text` desce o ExceptionGroup do anyio até a exceção que diz o que aconteceu.

4. ~~**Install de persona via git despeja o comando inteiro no erro**~~ **Corrigido (PR #21)** — mensagem curta com dica acionável; caminho interno não vaza mais.

5. ~~**PR #19**~~ **Mergeado.**

6. ~~**App instalado desatualizado**~~ **Rebuildado e reinstalado** do `deploy/hml` da madrugada (`fcb7f8c`). Um rebuild final acontece quando #20/#21 mergearem — nada do que eles mudam afeta o app em execução (teste de config e strings de erro raras).

## 🟡 Observações (não são bugs)

- **Modelo "some" da lista ao trocar default** — modelos de matriz (ex.: `gpt-5.6-sol`) só aparecem quando o provider tem chave configurada; sem chave, trocar o default para outro provider os oculta. Design correto (modelo inutilizável não deve ser oferecido), mas foi exatamente o que pareceu bug no seu primeiro dia com o Ollama. Se quiser, dá para deixar visível-porém-desabilitado com tooltip.
- Cron com mês restrito agora mostra o cron cru (`0 3 1 1 *`) em vez de mentir "Todo dia" — é o fix do #17 funcionando.
- Erro de conector desconhecido, BYO sem caminho, e cloud login já respondem em pt-BR (i18n backend do #8 segurando).

## Não testado (impossível headless / requer conta externa)

- **Voz/STT** (Whisper) — só existe dentro do app nativo (Tauri); browser/dev não liga microfone por design.
- **OAuth reais** (Slack/GitHub/Gmail/HubSpot…) — precisam de app + conta; as validações e o gating foram testados, o fluxo do navegador não.
- **Cloud logado** — desligado por decisão nossa; caminhos de recusa testados.
- **Auto-update ponta a ponta** — precisa de release publicada.
- **Build Windows** (`build_windows.ps1`).

## Consertado durante a noite (contexto)

1. **Dia da semana errado em TODA automação semanal** — em cron `0` = domingo, a lista começava em Monday; "toda automação de domingo dizia segunda". Bug pré-existente, em inglês também; o teste antigo **afirmava o bug**. (#17)
2. **Rótulos de agendamento** eram gerados no servidor em inglês (sidebar + página de Automações) — traduzidos, com 24h no pt-BR e concordância de gênero (todo sábado / toda segunda-feira). (#17)
3. **Data da tabela Unrouted seguia o locale do SO**, não do app (`toLocaleString()` sem argumento). (#18)

## Estado dos PRs

`#13 ✓ #18(=14) ✓ #15 ✓ #16 ✓ #17 ✓ #19 ✓` mergeados em `deploy/hml` · `#20` (fix dos testes do Sidebar) e `#21` (erros de backend) em review · GUI **100% traduzida** com guard cobrindo todo o `src/` (allowlist exata, cena fictícia do Slack documentada como exceção).
