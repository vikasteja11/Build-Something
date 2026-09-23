# Architecture

## System Overview

The application follows a **multi-agent pipeline architecture** where two independent AI agents collaborate through synchronous REST communication and a shared persistent datastore.

```
                         ┌────────────────────────────────────────────────────────────────┐
                         │                      Kubernetes Cluster                        │
                         │                                                                │
 ┌────────┐   NodePort   │  ┌──────────────┐   REST   ┌──────────────┐  REST  ┌──────────┐ │
 │ Client │──(30000)────►│  │   frontend   │─────────►│ coder-agent  │───────►│ reviewer-│ │
 │        │              │  │  (2 replicas)│          │ (2 replicas) │        │  agent   │ │
 └────────┘              │  └──────────────┘          └──────┬───────┘        │(2 repls) │ │
                         │                                   │                └────┬─────┘ │
                         │                                   │   ┌──────────┐      │      │
                         │                                   └──►│ PostgreSQL│◄─────┘      │
                         │                                       │ (1 repl) │             │
                         │                                       └──────────┘             │
                         └────────────────────────────────────────────────────────────────┘
```

## Component Roles

### 1. Frontend Web UI (`frontend`)

| Aspect | Detail |
|---|---|
| **Role** | Sleek user interface microservice |
| **Technology** | Python 3.11, FastAPI, HTML5/CSS3 (Glassmorphism), Vanilla JS |
| **Endpoints** | `GET /`, `GET /health`, `GET /api/system-status`, `POST /api/generate`, `GET /api/records` |
| **K8s resource** | Deployment (replicas: 2, scalable) + NodePort Service |
| **External access** | Yes — NodePort 30000 (and local port 3000) |

### 2. Coder Agent (`coder-agent`)

| Aspect | Detail |
|---|---|
| **Role** | Code generation from natural language specifications |
| **Technology** | Python 3.11, FastAPI |
| **Endpoints** | `POST /generate`, `GET /health`, `GET /records` |
| **LLM usage** | Calls OpenRouter API with a code-generation system prompt |
| **K8s resource** | Deployment (replicas: 2, scalable) + NodePort Service |
| **External access** | Yes — NodePort 30080 |

The coder-agent is the system's entry point. It:
1. Accepts a spec from the client
2. Calls the OpenRouter API to generate code
3. Saves the spec + generated code to PostgreSQL
4. Makes a synchronous REST call to the reviewer-agent
5. Returns the combined result (code + review) to the client

### 2. Reviewer Agent (`reviewer-agent`)

| Aspect | Detail |
|---|---|
| **Role** | Automated code review for bugs and style issues |
| **Technology** | Python 3.11, FastAPI |
| **Endpoints** | `POST /review`, `GET /health`, `GET /records` |
| **LLM usage** | Calls OpenRouter API with a code-review system prompt |
| **K8s resource** | Deployment (replicas: 2, scalable) + ClusterIP Service |
| **External access** | No — internal only |

The reviewer-agent:
1. Receives code and the original spec from the coder-agent (or directly via its API)
2. Calls the OpenRouter API with a review-specific prompt
3. Parses the structured review (comments + verdict)
4. Updates the same database row with review results
5. Returns the review to the caller

### 3. PostgreSQL (`postgres`)

| Aspect | Detail |
|---|---|
| **Role** | Persistent shared datastore |
| **Technology** | PostgreSQL 16 |
| **K8s resource** | Deployment (replicas: 1, NOT scalable) + ClusterIP Service + PVC |
| **External access** | No — internal only |
| **Storage** | 1Gi PersistentVolumeClaim survives pod restarts |

PostgreSQL stores the complete pipeline history in a single `tasks` table:

| Column | Type | Written by |
|---|---|---|
| `id` | UUID (PK) | coder-agent |
| `spec` | TEXT | coder-agent |
| `generated_code` | TEXT | coder-agent |
| `review_comments` | TEXT | reviewer-agent |
| `verdict` | VARCHAR(20) | reviewer-agent |
| `created_at` | TIMESTAMP | coder-agent |
| `updated_at` | TIMESTAMP | reviewer-agent |

## Architecture Pattern

This system uses an **agent pipeline with shared datastore** pattern:

- **Pipeline**: The request flows linearly: client → coder-agent → reviewer-agent → response. The coder-agent orchestrates the pipeline by making a synchronous service-to-service REST call to the reviewer-agent.

- **Shared datastore**: Both agents read from and write to the same PostgreSQL database and the same `tasks` table. The coder-agent creates a row (spec + code), and the reviewer-agent updates it (comments + verdict). This allows querying the full pipeline result from a single table.

- **Decoupled services**: Despite the pipeline flow, the agents are independently deployable and callable. The reviewer-agent can review code submitted directly to its API, not just code from the coder-agent.

## Component-to-Microservice Mapping

| Software Component | Microservice | Kubernetes Deployment | Kubernetes Service |
|---|---|---|---|
| User Interface & Web App | `frontend` | `frontend` (2 replicas) | NodePort (30000) |
| Code generation logic | `coder-agent` | `coder-agent` (2 replicas) | NodePort (30080) |
| Code review logic | `reviewer-agent` | `reviewer-agent` (2 replicas) | ClusterIP (8001) |
| Data persistence | `postgres` | `postgres` (1 replica) | ClusterIP (5432) |

## Request Flow (detailed)

```
1. Client sends POST /generate {"spec": "..."} to coder-agent (NodePort 30080)
2. coder-agent calls OpenRouter API → receives generated code
3. coder-agent inserts row into PostgreSQL: (id, spec, generated_code)
4. coder-agent calls POST /review on reviewer-agent (ClusterIP 8001)
   with payload: {spec_id, spec, code}
5. reviewer-agent calls OpenRouter API → receives review comments + verdict
6. reviewer-agent updates the same row in PostgreSQL: (review_comments, verdict)
7. reviewer-agent returns {spec_id, review_comments, verdict} to coder-agent
8. coder-agent returns combined response to client:
   {spec_id, spec, generated_code, review_comments, verdict}
```

## Kubernetes Networking

- **External → coder-agent**: NodePort Service exposes port 30080 on all cluster nodes
- **coder-agent → reviewer-agent**: Internal DNS: `reviewer-agent.multi-agent.svc.cluster.local:8001`
- **Both agents → PostgreSQL**: Internal DNS: `postgres.multi-agent.svc.cluster.local:5432`
- **reviewer-agent**: Not externally accessible (ClusterIP only)

## Horizontal Scaling

| Component | Scalable? | Command |
|---|---|---|
| coder-agent | ✅ Yes | `kubectl -n multi-agent scale deployment coder-agent --replicas=N` |
| reviewer-agent | ✅ Yes | `kubectl -n multi-agent scale deployment reviewer-agent --replicas=N` |
| postgres | ❌ No | Single replica (stateful, requires leader election for HA) |

Both agents are stateless (all state is in PostgreSQL), so they scale horizontally without coordination. Kubernetes Services automatically load-balance across replicas.
