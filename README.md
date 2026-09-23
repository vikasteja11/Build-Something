# Multi-Agent Code Writer + Reviewer

A Kubernetes-deployable multi-agent application where two AI-powered microservices collaborate on coding tasks: one generates code from a plain-text specification, and the other reviews it for bugs and style issues.

## Architecture Overview

```
Client Browser
      │
      ▼
┌──────────────┐     REST      ┌──────────────┐     REST      ┌─────────────────┐
│   frontend   │ ────────────► │  coder-agent │ ────────────► │  reviewer-agent  │
│(NodePort 30000)              │(NodePort 30080)              │   (ClusterIP)   │
└──────────────┘               └──────┬───────┘               └────────┬────────┘
                                      │                                │
                                      │          ┌──────────┐          │
                                      └─────────►│ Postgres │◄─────────┘
                                                 │  (PVC)   │
                                                 └──────────┘
```

**Components:**
- **frontend** — Sleek web UI for inputting prompts, tracking live pipeline execution, displaying code & review cards side-by-side, and listing execution history
- **coder-agent** — Takes a spec, generates code via OpenRouter, calls reviewer-agent, returns combined result
- **reviewer-agent** — Reviews code for bugs/style, returns comments + pass/needs-work verdict
- **PostgreSQL** — Shared persistent datastore for the full pipeline history

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [minikube](https://minikube.sigs.k8s.io/docs/start/) (for local Kubernetes)
- A [Docker Hub](https://hub.docker.com/) account
- An [OpenRouter API key](https://openrouter.ai/keys)

---

## 1. Local Testing with Docker Compose

```bash
# Set your OpenRouter API key
export OPENROUTER_API_KEY="sk-or-v1-your-key-here"

# Build and start all services
docker-compose up --build

# In another terminal, test the full pipeline:
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"spec": "Write a Python function that reverses a string"}'

# Check health
curl http://localhost:8000/health
curl http://localhost:8001/health

# View all saved records
curl http://localhost:8000/records

# Stop everything
docker-compose down
```

---

## 2. Build and Push Docker Images

Replace `YOUR_DOCKERHUB_USERNAME` with your actual Docker Hub username.

```bash
# Log in to Docker Hub
docker login

# Build and tag images
docker build -t YOUR_DOCKERHUB_USERNAME/coder-agent:latest ./coder-agent
docker build -t YOUR_DOCKERHUB_USERNAME/reviewer-agent:latest ./reviewer-agent

# Push to Docker Hub
docker push YOUR_DOCKERHUB_USERNAME/coder-agent:latest
docker push YOUR_DOCKERHUB_USERNAME/reviewer-agent:latest
```

---

## 3. Deploy to Kubernetes (minikube)

### 3.1 Start minikube

If you previously had a minikube cluster, it's highly recommended to delete it first to avoid `K8S_APISERVER_MISSING` errors:
```bash
minikube delete
```

Start a fresh cluster with guaranteed resources:
```bash
minikube start --memory=4096 --cpus=4
```

### 3.2 Update Kubernetes manifests

1. **Edit `k8s/secrets.yaml`** — replace the `OPENROUTER_API_KEY` value with your base64-encoded key:
   ```bash
   echo -n 'sk-or-v1-your-key-here' | base64
   ```
   Copy the output and paste it as the `OPENROUTER_API_KEY` value in `k8s/secrets.yaml`.

2. **Edit `k8s/coder-agent.yaml` and `k8s/reviewer-agent.yaml`** — replace `<YOUR_DOCKERHUB_USERNAME>` with your Docker Hub username in the `image:` fields.

### 3.3 Apply manifests

```bash
# Create namespace
kubectl apply -f k8s/namespace.yaml

# Create secrets
kubectl apply -f k8s/secrets.yaml

# Deploy PostgreSQL (with persistent storage)
kubectl apply -f k8s/postgres.yaml

# Wait for Postgres to be ready
kubectl -n multi-agent wait --for=condition=ready pod -l app=postgres --timeout=60s

# Deploy both agents
kubectl apply -f k8s/coder-agent.yaml
kubectl apply -f k8s/reviewer-agent.yaml

# Verify all pods are running
kubectl -n multi-agent get pods
```

### 3.4 Access from outside the cluster

Since minikube runs inside a Docker container on Mac, the easiest way to access the NodePort services is via Minikube's built-in service tunnel.

**1. Open the Web Frontend (Browser UI):**
```bash
minikube service frontend -n multi-agent
```
*This will automatically open your default web browser and securely route you to the sleek UI.*

**2. Test the Coder Agent API directly (optional):**
```bash
minikube service coder-agent -n multi-agent
```

---

## 4. Demonstrate Horizontal Scaling

```bash
# Scale coder-agent from 2 to 4 replicas
kubectl -n multi-agent scale deployment coder-agent --replicas=4

# Verify new pods are created
kubectl -n multi-agent get pods -l app=coder-agent

# Scale reviewer-agent from 2 to 3 replicas
kubectl -n multi-agent scale deployment reviewer-agent --replicas=3

# Verify
kubectl -n multi-agent get pods -l app=reviewer-agent

# Scale back down
kubectl -n multi-agent scale deployment coder-agent --replicas=2
kubectl -n multi-agent scale deployment reviewer-agent --replicas=2
```

---

## 5. Verify Persistent Storage

```bash
# Send a request to create a record
curl -X POST http://<MINIKUBE_IP>:30080/generate \
  -H "Content-Type: application/json" \
  -d '{"spec": "Write a Python function that sorts a list"}'

# Check records exist
curl http://<MINIKUBE_IP>:30080/records

# Delete the Postgres pod (it will restart automatically)
kubectl -n multi-agent delete pod -l app=postgres

# Wait for it to come back
kubectl -n multi-agent wait --for=condition=ready pod -l app=postgres --timeout=60s

# Verify records survived the restart
curl http://<MINIKUBE_IP>:30080/records
```

---

## API Reference

### Coder Agent (port 8000 / NodePort 30080)

| Endpoint | Method | Description |
|---|---|---|
| `/generate` | POST | Generate code from spec, get it reviewed, return combined result |
| `/health` | GET | Liveness probe |
| `/records` | GET | List all stored tasks |

**POST /generate** request body:
```json
{"spec": "Write a Python function that reverses a string"}
```

### Reviewer Agent (port 8001, internal only)

| Endpoint | Method | Description |
|---|---|---|
| `/review` | POST | Review code against a spec |
| `/health` | GET | Liveness probe |
| `/records` | GET | List all stored tasks |

**POST /review** request body:
```json
{"spec_id": "uuid", "spec": "the original spec", "code": "the generated code"}
```

---

## Cleanup

```bash
# Delete all Kubernetes resources
kubectl delete namespace multi-agent

# Stop minikube
minikube stop

# Remove local Docker Compose volumes
docker-compose down -v
```

---

## Project Structure

```
.
├── coder-agent/
│   ├── main.py            # FastAPI app (POST /generate, GET /health, GET /records)
│   ├── database.py        # SQLAlchemy models and DB setup
│   ├── requirements.txt   # Python dependencies
│   └── Dockerfile
├── reviewer-agent/
│   ├── main.py            # FastAPI app (POST /review, GET /health, GET /records)
│   ├── database.py        # SQLAlchemy models and DB setup
│   ├── requirements.txt   # Python dependencies
│   └── Dockerfile
├── k8s/
│   ├── namespace.yaml     # Kubernetes namespace
│   ├── secrets.yaml       # DB URL + OpenRouter API key
│   ├── postgres.yaml      # PVC + Deployment + ClusterIP Service
│   ├── coder-agent.yaml   # Deployment + NodePort Service
│   └── reviewer-agent.yaml# Deployment + ClusterIP Service
├── docs/
│   ├── description.md     # Project description
│   ├── architecture.md    # Architecture documentation
│   └── security.md        # Security analysis
├── docker-compose.yml     # Local development setup
└── README.md              # This file
```
