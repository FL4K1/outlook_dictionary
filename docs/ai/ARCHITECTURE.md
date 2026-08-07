# Architecture

> High-level architecture. No implementation details.

---

## Product Architecture
The platform connects organizational mail accounts, synchronizing mail continuously into a scalable metadata and indexing engine. The core retrieval pipeline flows sequentially:
`Provider → Sync → Normalize → Store → Index → Retrieve → Reason → Present`

## Authentication Architecture
Platform authentication is strictly provider-independent.
`External IdP → Platform AuthenticationService → Session + JWT`
- **Core Invariant:** JWTs contain ONLY identity/session identifiers (`sub`, `tid`, `oid`, `sid`, `jti`). Roles and permissions are resolved server-side exclusively.

## Provider Abstraction
Mail providers (Microsoft Graph, Gmail) are abstracted behind strict Protocol interfaces (`MailSyncProvider`, `MailAuthProvider`, `MailWebhookProvider`). Business logic never depends on concrete provider types.

## Multi-Tenancy
Tenant isolation is enforced strictly at the database repository layer. Every data entity belongs to a tenant, and cross-tenant access is architecturally impossible.

## Search Architecture
Multi-stage search utilizing Elasticsearch: Structured metadata search → Keyword full-text search → Semantic vector search → Candidate ranking.

## AI Architecture
AI reasoning (LLM) is applied only at the *end* of the retrieval pipeline to evaluate ranked candidates. AI is never used for initial retrieval.

## Dependency Direction
```
Routers → Services → Repositories → Models
                 ↘ Providers (via Protocol)
```
- Routers handle HTTP; Services handle business logic; Repositories handle data access. No layer violations allowed.

## Repository Layout
- `apps/`: Deployable services (`api`, `workers`, `webhook`, `web`).
- `packages/`: Shared domain logic (`mip_models`, `mip_providers`, `mip_ai`, `mip_email_parser`).
- `infra/`: Infrastructure configs.
- `docs/`: AI context and engineering reviews.

## Technology Stack
- **API**: FastAPI (Python 3.12+)
- **Database**: PostgreSQL 16
- **Search**: Elasticsearch 8.x
- **Cache/Queue**: Redis 7
- **Object Storage**: S3 / MinIO
- **Frontend**: Next.js 14+ (TypeScript)
- **AI**: Azure OpenAI / OpenAI
