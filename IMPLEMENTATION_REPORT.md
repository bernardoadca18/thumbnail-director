# Thumbnail Director — implementação e validação

Implementação concluída em código. A validação visual real da estratégia corrigida v2
ficou bloqueada no Concept Director/9Router; não há três opções v2 aprováveis comprovadas.
Nenhuma thumbnail foi selecionada automaticamente.

## Entrega

1. **Worktrees antes da edição:** `orchestrator` em `master` (`ec1405d`),
   `media-indexer` em `master` (`6b09ce0`) e `neovids-infra` em `main` (`7affea0`).
   Os três estavam limpos. `infra/` é um symlink para `neovids-infra/infra`.
   Sem commit, push, limpeza destrutiva ou alteração do Video Composer.
2. **Novo serviço:** `thumbnail-director/`, independente, sem banco/fila própria.
3. **Stack:** Python, FastAPI, Pydantic, httpx, pyvips/libvips, Pango/DejaVu e YAML.
   Docker validado com libvips 8.14; porta 8014 → 8000, volume persistente e rede NeoVids.
4. **Endpoints:** `POST /v1/thumbnails/generate`, `GET /v1/artifacts/{artifact_id}`,
   `GET /health/live`, `GET /health/ready`.
5. **Contratos:** `thumbnail_request_v1` / `thumbnail_package_v1`, fingerprint canônico
   calculado pelo Orchestrator sobre snapshot congelado, identidade, escopo, locale,
   versões e revisão. Estratégia/prompt atuais: `thumbnail_director_v2` /
   `thumbnail_concepts_v2`; artifacts v1 permanecem imutáveis e legíveis.
6. **Templates:** A `split_compare_v1`, B `hero_focus_v1`, C `curiosity_focus_v1`.
   PNG sRGB 1280×720, attention crop sem deformação, contraste leve, gradiente e texto
   medido por Pango, no máximo cinco palavras/duas linhas. PT-BR e EN-US.
7. **Media Indexer:** APIs públicas diretas, project/session scope e filtros preservados,
   SHA verificado para imagens, descrição exigida para o assunto e jogo solicitado.
   Similaridade semântica isolada não autoriza selecionar outro assunto/jogo.
8. **Frame endpoint adicionado:** `GET /media-assets/{media_id}/frame` com
   `timestamp_seconds` e `width`; validação de duração/tipo, managed storage interno,
   seek FFmpeg antes do input, um JPEG, timeout 15s, dimensão máxima 1920.
   Vídeos-fonte não são baixados; windows não-clean são rejeitadas.
9. **Storage/idempotência:** manifest, PNGs, SHA-256, lock por fingerprint,
   fsync e rename atômico do diretório. Replay válido não chama LLM; corrupção falha
   sem sobrescrever. Endpoints aceitam apenas IDs, não paths/URLs arbitrárias.
10. **Orchestrator:** `WorkflowThumbnailPackage`, migration `021_thumbnail_packages.py`,
    tabela JSONB com unique por fingerprint e workflow/revisão. Migration 021 foi
    verificada no ambiente existente. Worker Celery existente, retry limitado,
    recuperação de pending/running e seleção humana durável. Nenhuma fase de vídeo nova.
11. **Operator API**, sob `/operator/workflows/{workflow_id}/thumbnails`:
    `POST /generate`, `POST /regenerate`, `GET`, `POST /select`,
    `GET /{candidate_id}/content`. Proxy verifica SHA; seleção rejeita opções inexistentes,
    indisponíveis, package desatualizado e a estratégia v1 anterior à correção de relevância.
12. **Console:** aba Thumbnails, cards A/B/C, imagem, headline, template, fontes, status,
    revisão, seleção, novas opções, histórico e proteção contra seleção de tela antiga.
13. **Checks focais:** 6 testes Thumbnail Director, 5 Orchestrator e 2 Media Indexer
    passaram; smoke PostgreSQL com criação/reuso, seleção e histórico passou com rollback
    de todos os dados sintéticos; smoke libvips no Docker gerou os três PNGs; FFmpeg
    extraiu um JPEG 320×180 de fixture sintética; frontend typecheck/build, imports,
    lint focal, shell syntax, compose config e diff checks passaram.
14. **Amostra sintética (não editorial):**
    `data/validation/candidate_A.png`
    SHA-256 `b4aa43efbe312320adf463cbaf021e6e176513cd194096a345352839da34d7c3`.
    Manifest das três amostras: `data/validation/smoke_manifest.json`.
15. **Limitações concretas:** detalhadas abaixo.

## Integração real e limites

Workflow: `10bfea0f-2491-49e8-9de9-02cdc6b711c7`,
“O verdadeiro salto de GTA VI não está nos gráficos”.

A revisão 0 (`66875082-2cb5-4656-84f3-0041dbc3b420`) produziu três PNGs reais.
Os bytes foram lidos pelo proxy do Operator e os SHA conferidos. A inspeção visual
identificou dois problemas de correspondência (jogo errado na comparação e headline
sobre cão sobre outra pessoa). Por isso a seleção v1 foi bloqueada e o filtro de
relevância/prompt foi corrigido em v2. Esses artifacts não são exemplos editoriais
aprovados e não foram sobrescritos.

A segunda geração foi autorizada após esse diagnóstico. A revisão 1
(`802368e2-a55c-4b94-8f90-e372ff3894fc`) foi persistida, mantendo a revisão 0 no histórico.
O worker estava ocupado com narração de outro trabalho; foi executado somente o handler
durável de thumbnail do Orchestrator. A tentativa terminou com
`concept_generation_failed`. O gateway registrou falhas em modelos da rota
`neovids-standard`, inclusive HTTP 404 para um modelo indisponível. Não foi alterada a
configuração global do 9Router nem adicionado modelo/provedor novo. A entrega duplicada
que permanecia na fila foi revogada, sem interromper a narração.

Estado final: revisão 1 failed; seleção null; revisão 0 e seus SHA preservados. A geração
real v2 precisa ser repetida quando a rota existente fornecer structured output válido.
A filtragem conservadora também pode retornar unavailable quando os metadados existentes
não descrevem suficientemente um subject, em vez de usar imagem sem correspondência.

O patch final que ignora entregas duplicadas de packages terminais está em código e
validado por testes. O worker ocupado não foi reiniciado novamente para carregar esse
último ajuste; ele será aplicado no próximo restart controlado. API, novo serviço e
Console foram integrados ao ambiente local. Nenhuma produção de áudio/vídeo foi disparada
para validar thumbnails.

```text
THUMBNAIL_DIRECTOR_SERVICE = YES
THUMBNAIL_DIRECTOR_PORT = 8014
ORCHESTRATOR_OWNS_LIFECYCLE = YES
MEDIA_INDEXER_OWNS_SOURCE_ASSETS = YES
VIDEO_COMPOSER_CHANGED = NO
AI_IMAGE_GENERATION = NO
DETERMINISTIC_RENDERER = YES
OUTPUT_SIZE = 1280x720
CANDIDATE_COUNT = 3
SPLIT_COMPARE_TEMPLATE = YES
HERO_FOCUS_TEMPLATE = YES
CURIOSITY_FOCUS_TEMPLATE = YES
PT_BR_SUPPORTED = YES
EN_US_SUPPORTED = YES
HUMAN_SELECTION = YES
REGENERATE_PRESERVES_HISTORY = YES
FULL_TEST_SUITE_RUN = NO
BENCHMARK_RUN = NO
OBSERVABILITY_ADDED = NO
FULL_VIDEO_E2E_RUN = NO
IMAGE_GENERATION_MODEL_ADDED = NO
```
